"""Synthetic package checks. No real Team Key, proof, registry request or upload is used."""
from contextlib import redirect_stderr, redirect_stdout
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import verify_t14_submission_package_20260929 as verifier
from qfbench2_common.contracts.descriptor import seal_descriptor_digest
from qfbench2_common.contracts.errors import ContractError


class PackageChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.descriptor_path = self.folder / "descriptor.json"
        self.archive_path = self.folder / "submission.zip"
        self.expected = {
            "schema_version": "1.0.0", "interface_version": "2.0",
            "competition_id": "agenthon2026-analysis-dev", "track": "analysis",
            "phase": "dev", "category": "api", "image_access": "public",
            "image": {"registry": "docker.io", "repository": "felix772/agenthon-2026-t4",
                      "digest": "sha256:" + "12" * 32},
            "models": [{"name": "nvidia/nemotron-3-super-120b-a12b", "version": "rl-030326-fp8",
                        "revision": "rl-030326-fp8", "training_cutoff": "unpublished", "access": "api"}],
            "license": "Apache-2.0",
        }
        self.write_expected()
        self.sealed = seal_descriptor_digest({**self.expected, "team_id": "team-" + "23" * 16})
        self.descriptor_bytes = (json.dumps(self.sealed, sort_keys=True, indent=2) + "\n").encode()
        # Shape-only synthetic proof is intentionally not authenticated.
        self.claim = {"schema_version": "2.0", "site_team_id": 321,
                      "descriptor_sha256": verifier.sha256(self.descriptor_bytes), "proof": "45" * 32}
        self.write_archive()

    def write_expected(self):
        self.descriptor_path.write_text(json.dumps(self.expected), encoding="utf-8")

    def write_archive(self, descriptor_bytes=None, claim=None, members=None):
        pairs = members or [("submission.json", self.descriptor_bytes if descriptor_bytes is None else descriptor_bytes),
                            ("team-claim.json", json.dumps(self.claim if claim is None else claim).encode())]
        with zipfile.ZipFile(self.archive_path, "w", zipfile.ZIP_STORED) as archive:
            for name, raw in pairs:
                archive.writestr(name, raw)

    def verify(self, track="analysis"):
        return verifier.verify_package(track, self.descriptor_path, self.archive_path)

    def test_valid_analysis_structure_is_not_hmac_authentication(self):
        report = self.verify()
        self.assertTrue(report["official_parser_and_jcs_digest_pass"])
        self.assertFalse(report["claim_hmac_independently_verified"])
        self.assertNotIn(self.claim["proof"], json.dumps(report))

    def test_valid_coding(self):
        self.expected.update(track="coding", competition_id="agenthon2026-coding-dev")
        self.expected["image"]["repository"] = "felix772/agenthon-2026-t1"
        self.write_expected()
        sealed = seal_descriptor_digest({**self.expected, "team_id": self.sealed["team_id"]})
        raw = json.dumps(sealed).encode()
        claim = {**self.claim, "descriptor_sha256": verifier.sha256(raw)}
        self.write_archive(raw, claim)
        self.assertTrue(self.verify("coding")["all_ten_input_fields_unchanged"])

    def test_every_input_field_mismatch_is_refused(self):
        for field in sorted(verifier.INPUT_FIELDS):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.sealed)
                changed[field] = "synthetic-change"
                self.write_archive(json.dumps(changed).encode())
                with self.assertRaisesRegex(verifier.Refused, "packed_input_fields_changed"):
                    self.verify()

    def test_closed_twelve_fields(self):
        for changed in ({**self.sealed, "extra": 1}, {k: v for k, v in self.sealed.items() if k != "team_id"}):
            self.write_archive(json.dumps(changed).encode())
            with self.assertRaisesRegex(verifier.Refused, "sealed_descriptor_must_have_twelve_fields"):
                self.verify()

    def test_jcs_tamper(self):
        changed = {**self.sealed, "descriptor_digest": "sha256:" + "67" * 32}
        self.write_archive(json.dumps(changed).encode())
        with self.assertRaises(ContractError):
            self.verify()

    def test_exact_bytes_binding(self):
        self.write_archive(self.descriptor_bytes + b"\n")
        with self.assertRaisesRegex(verifier.Refused, "claim_descriptor_bytes_mismatch"):
            self.verify()

    def test_team_claim_constraints(self):
        for field, value in (("site_team_id", True), ("site_team_id", 322), ("schema_version", "1.0"), ("proof", "invalid")):
            with self.subTest(field=field, value=value):
                self.write_archive(claim={**self.claim, field: value})
                with self.assertRaises(verifier.Refused):
                    self.verify()

    def test_crc_tamper(self):
        archive = self.archive_path.read_bytes()
        offset = archive.index(self.descriptor_bytes)
        tampered = archive[:offset] + bytes([archive[offset] ^ 1]) + archive[offset + 1:]
        self.archive_path.write_bytes(tampered)
        with self.assertRaises((verifier.Refused, zipfile.BadZipFile)):
            self.verify()

    def test_duplicate_json_key(self):
        self.write_archive(b'{"track":"analysis",' + self.descriptor_bytes[1:])
        with self.assertRaisesRegex(verifier.Refused, "duplicate_json_key"):
            self.verify()

    def test_extra_zip_member_and_large_claim(self):
        self.write_archive(members=[("submission.json", self.descriptor_bytes),
                                    ("team-claim.json", json.dumps(self.claim).encode()), ("extra", b"x")])
        with self.assertRaisesRegex(verifier.Refused, "zip_members_or_order_invalid"):
            self.verify()
        self.write_archive(members=[("submission.json", self.descriptor_bytes), ("team-claim.json", b"x" * 1025)])
        with self.assertRaisesRegex(verifier.Refused, "claim_exceeds_official_byte_bound"):
            self.verify()

    def test_placeholder_digest_and_missing_descriptor(self):
        self.expected["image"]["digest"] = "sha256:" + "0" * 64
        self.write_expected()
        with self.assertRaisesRegex(verifier.Refused, "registry_digest_is_placeholder"):
            self.verify()
        self.descriptor_path.unlink()
        with self.assertRaisesRegex(verifier.Refused, "input_not_regular_file"):
            self.verify()

    def test_release_ledger_binding(self):
        report = self.verify()
        ledger = {"track": "t4", "repository": "felix772/agenthon-2026-t4",
                  "registry_digest": self.expected["image"]["digest"], "local_image_id": "sha256:" + "89" * 32,
                  "descriptor_input_sha256": report["descriptor_input_sha256"],
                  "image_audit_passed": True, "anonymous_pull_passed": True, "release_ready_for_official_pack": True}
        path = self.folder / "ledger.json"
        path.write_text(json.dumps(ledger), encoding="utf-8")
        result = verifier.verify_release_ledger("analysis", path, report)
        self.assertTrue(result["release_ledger_binding_pass"])
        self.assertFalse(result["remote_checks_independently_repeated_by_verifier"])
        for field in ledger:
            with self.subTest(field=field):
                path.write_text(json.dumps({**ledger, field: None}), encoding="utf-8")
                with self.assertRaises(verifier.Refused):
                    verifier.verify_release_ledger("analysis", path, report)

    def test_cli_report_no_overwrite_and_no_proof_leak(self):
        report = self.folder / "report.json"
        args = ["--track", "analysis", "--descriptor", str(self.descriptor_path),
                "--archive", str(self.archive_path), "--report", str(report)]
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            self.assertEqual(verifier.main(args), 0)
            original = report.read_bytes()
            self.assertEqual(verifier.main(args), 1)
        self.assertEqual(report.read_bytes(), original)
        self.assertNotIn(self.claim["proof"], output.getvalue() + original.decode())


if __name__ == "__main__":
    unittest.main()
