import importlib.util
import importlib
import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
import zipfile
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, PROJECT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EvaluatorMountTests(unittest.TestCase):
    def test_aggregate_output_limit_includes_internal_files(self):
        evaluate = load_tool("evaluate")
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            (output / '.agent').mkdir()
            with (output / '.agent/scratch').open('wb') as handle:
                handle.truncate(64 * 1024 * 1024)
            self.assertEqual(evaluate.output_tree_size(output), 64 * 1024 * 1024)
            (output / 'result.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, '64 MiB'):
                evaluate.output_tree_size(output)

    def test_verifier_receives_public_checker_layout_only(self):
        evaluate = load_tool("evaluate")
        with tempfile.TemporaryDirectory() as temp:
            unit = Path(temp) / "unit"
            (unit / "checks/reference_data").mkdir(parents=True)
            (unit / "environment/data").mkdir(parents=True)
            (unit / "environment/data/prices.csv").write_text("price\n1\n")
            output = Path(temp) / "output"
            output.mkdir()
            command = evaluate.verifier_mounts(unit, output)
            mounts = [value for index, value in enumerate(command) if command[index - 1] == "--mount"]
            self.assertIn(f"type=bind,source={unit},target=/input,readonly", mounts)
            self.assertIn(f"type=bind,source={unit / 'checks'},target=/tests,readonly", mounts)
            self.assertIn(f"type=bind,source={unit / 'environment/data'},target=/app/data,readonly", mounts)
            self.assertIn(f"type=bind,source={unit / 'environment/data/prices.csv'},target=/app/prices.csv,readonly", mounts)
            self.assertIn(f"type=bind,source={output},target=/app/output,readonly", mounts)
            self.assertIn(f"type=bind,source={output},target=/output,readonly", mounts)


class SubmissionArchiveTests(unittest.TestCase):
    def test_retired_archive_writes_nothing(self):
        submission = load_tool("submission")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            descriptor = root / "submission.json"
            descriptor.write_text('{"ok": true}\n', encoding="utf-8")
            archive = root / "submission.zip"
            with self.assertRaisesRegex(ValueError, "retired"):
                submission.write_submission_zip(descriptor, archive)
            self.assertFalse(archive.exists())
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                submission.main([str(descriptor), "--out", str(root / "sealed.json"), "--zip", str(archive)])
            self.assertFalse((root / "sealed.json").exists())
            self.assertFalse(archive.exists())

    def test_cli_seals_valid_descriptor_without_packaging(self):
        if importlib.util.find_spec("qfbench2_common") is None:
            self.skipTest("Descriptor validation requires the organizer toolkit")
        submission = load_tool("submission")
        from qfbench2_common.contracts.fixtures import load_fixture
        from qfbench2_common.contracts import SubmissionDescriptor
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            body = root / "body.json"
            body.write_text(json.dumps(load_fixture("c5/coding_dev.json")), encoding="utf-8")
            descriptor = root / "submission.json"
            self.assertEqual(submission.main([str(body), "--out", str(descriptor)]), 0)
            SubmissionDescriptor.from_mapping(json.loads(descriptor.read_text()))
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["body.json", "submission.json"])

    @unittest.skipUnless(os.name == "posix", "Official private-key-file permissions require POSIX")
    def test_official_alias_pack_binds_exact_bytes_and_excludes_secret(self):
        from qfbench2_common.contracts.fixtures import load_fixture
        from qfbench2_common.contracts import SubmissionDescriptor
        from qfbench2_common.team_claim import team_claim_proof

        submission = load_tool("submission")
        key = "SYNTHETIC-local-pack-test-only"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            key_file = root / "key"
            key_file.write_text(key, encoding="utf-8")
            key_file.chmod(0o600)
            body = load_fixture("c5/coding_dev.json")
            body.pop("team_id")
            body.pop("descriptor_digest")
            body["models"] = []
            descriptor = root / "body.json"
            descriptor.write_text(json.dumps(body), encoding="utf-8")
            archive = root / "submission.zip"
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(submission.main(["alias", "--team-number", "11", "--team-key-file", str(key_file)]), 0)
                alias = output.getvalue().strip()
                arguments = ["pack", "--descriptor", str(descriptor), "--team-number", "11", "--team-key-file", str(key_file), "--out", str(archive)]
                self.assertEqual(submission.main(arguments), 0)
                original = archive.read_bytes()
                self.assertEqual(submission.main(arguments), 1)
                self.assertEqual(archive.read_bytes(), original)
            with zipfile.ZipFile(archive) as package:
                self.assertEqual(sorted(package.namelist()), ["submission.json", "team-claim.json"])
                payload = package.read("submission.json")
                claim = json.loads(package.read("team-claim.json"))
                parsed = SubmissionDescriptor.from_mapping(json.loads(payload))
                self.assertEqual(parsed.team_id, alias)
                self.assertEqual(json.loads(payload)["models"], [])
                self.assertEqual(claim["schema_version"], "2.0")
                self.assertEqual(claim["descriptor_sha256"], hashlib.sha256(payload).hexdigest())
                self.assertEqual(claim["proof"], team_claim_proof(11, key, claim["descriptor_sha256"]))
                self.assertNotEqual(claim["proof"], team_claim_proof(11, key, hashlib.sha256(payload + b" ").hexdigest()))
                for name in package.namelist():
                    self.assertNotIn(key.encode(), package.read(name))
            self.assertNotIn(key, output.getvalue() + errors.getvalue())

    def test_command_line_keys_are_refused_without_echo(self):
        submission = load_tool("submission")
        key = "SYNTHETIC-must-not-echo"
        for args in (["pack", "--team-key", key], ["body.json", "--team-key", key]):
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertNotEqual(submission.main(args), 0)
            self.assertNotIn(key, output.getvalue() + errors.getvalue())


class ReleaseBuildTests(unittest.TestCase):
    def test_release_base_must_include_a_digest(self):
        release = load_tool("build_release")
        release.require_immutable_image("registry.example/team/sandbox@sha256:" + "a" * 64)
        with self.assertRaises(ValueError):
            release.require_immutable_image("finance-bench-sandbox:latest")


if __name__ == "__main__":
    unittest.main()
