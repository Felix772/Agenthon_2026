"""Revalidate the frozen T4 package against official toolkit 2.5.1 without emitting proof bytes."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.resources
import io
import json
from pathlib import Path
import re
import stat
import sys
import zipfile


INPUT_FIELDS = {
    "schema_version", "interface_version", "competition_id", "track", "phase",
    "category", "image", "image_access", "models", "license",
}
SEALED_FIELDS = INPUT_FIELDS | {"team_id", "descriptor_digest"}
CLAIM_FIELDS = {"schema_version", "site_team_id", "descriptor_sha256", "proof"}
TEAM_NUMBER = 321
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024  # Local verifier bound, not a competition ZIP quota.
MAX_DESCRIPTOR_BYTES = 1024 * 1024


class Refused(ValueError):
    """A closed error code; never includes document values or proof material."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise Refused(code)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_bounded(path: Path, maximum: int) -> bytes:
    require(not path.is_symlink() and path.is_file(), "input_not_regular_file")
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    require(0 < len(raw) <= maximum, "input_size_outside_local_bound")
    return raw


def strict_json(raw: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    def bad_constant(_):
        raise Refused("nonfinite_json_constant")

    result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=bad_constant)
    require(type(result) is dict, "json_root_not_object")
    return result


def validate_input(track: str, path: Path) -> tuple[dict, dict, dict]:
    require(importlib.metadata.version("qfbench2-common") == "2.5.1", "toolkit_must_be_2_5_1")
    import jsonschema
    from qfbench2_common.contracts.descriptor import SubmissionDescriptor, seal_descriptor_digest

    raw = read_bounded(path, MAX_DESCRIPTOR_BYTES)
    descriptor = strict_json(raw)
    require(set(descriptor) == INPUT_FIELDS, "input_must_have_exactly_ten_fields")
    require(descriptor["track"] == track, "input_track_mismatch")
    require(descriptor["competition_id"] == f"agenthon2026-{track}-dev", "competition_id_mismatch")
    require(descriptor["phase"] == "dev" and descriptor["category"] == "api", "phase_or_category_mismatch")
    require(descriptor["image_access"] == "public", "this_release_requires_public_image")
    image = descriptor.get("image")
    require(type(image) is dict and set(image) == {"registry", "repository", "digest"}, "image_fields_invalid")
    digest = image.get("digest")
    require(type(digest) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is not None, "registry_digest_missing_or_malformed")
    require(len(set(digest[7:])) > 1, "registry_digest_is_placeholder")
    # This synthetic alias validates shape only. It is never packed or reported as the team's alias.
    shape = seal_descriptor_digest({**descriptor, "team_id": "team-" + "0" * 32})
    schema_bytes = (importlib.resources.files("qfbench2_common") / "schemas/submission.schema.json").read_bytes()
    schema = json.loads(schema_bytes)
    jsonschema.Draft202012Validator(schema).validate(shape)
    parsed = SubmissionDescriptor.from_mapping(shape)
    parsed.image_reference()
    metadata = {
        "descriptor_input_sha256": sha256(raw),
        "toolkit_version": "2.5.1",
        "schema_sha256": sha256(schema_bytes),
        "image_reference": parsed.image_reference(),
        "image_digest": digest,
        "input_field_count": len(INPUT_FIELDS),
        "remote_registry_existence_verified": False,
        "anonymous_manifest_and_layers_verified": False,
    }
    return descriptor, schema, metadata


def verify_package(track: str, descriptor_path: Path, archive_path: Path) -> dict:
    import jsonschema
    from qfbench2_common.contracts.descriptor import SubmissionDescriptor, seal_descriptor_digest

    expected, schema, report = validate_input(track, descriptor_path)
    archive_bytes = read_bounded(archive_path, MAX_ARCHIVE_BYTES)
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        members = archive.infolist()
        require([m.filename for m in members] == ["submission.json", "team-claim.json"], "zip_members_or_order_invalid")
        require(not archive.comment, "unexpected_zip_comment")
        for member in members:
            require(not member.is_dir() and not member.flag_bits & 1, "zip_member_not_plain_file")
            mode = member.external_attr >> 16
            require(stat.S_IFMT(mode) in (0, stat.S_IFREG), "zip_member_special_file")
            require(not member.comment and not member.extra, "unexpected_zip_member_metadata")
        require(0 < members[0].file_size <= MAX_DESCRIPTOR_BYTES, "descriptor_member_too_large")
        require(0 < members[1].file_size <= 1024, "claim_exceeds_official_byte_bound")
        require(archive.testzip() is None, "zip_crc_failed")
        descriptor_bytes = archive.read("submission.json")
        claim_bytes = archive.read("team-claim.json")

    descriptor, claim = strict_json(descriptor_bytes), strict_json(claim_bytes)
    require(set(descriptor) == SEALED_FIELDS, "sealed_descriptor_must_have_twelve_fields")
    require({field: descriptor[field] for field in INPUT_FIELDS} == expected, "packed_input_fields_changed")
    jsonschema.Draft202012Validator(schema).validate(descriptor)
    parsed = SubmissionDescriptor.from_mapping(descriptor)
    require(seal_descriptor_digest(descriptor) == descriptor, "jcs_reseal_mismatch")
    require(re.fullmatch(r"team-[0-9a-f]{32}", descriptor["team_id"]) is not None, "team_alias_shape_invalid")
    require(set(claim) == CLAIM_FIELDS, "claim_must_have_four_v2_fields")
    require(claim["schema_version"] == "2.0", "claim_version_invalid")
    require(type(claim["site_team_id"]) is int and claim["site_team_id"] == TEAM_NUMBER, "claim_team_number_mismatch")
    descriptor_sha = sha256(descriptor_bytes)
    require(claim["descriptor_sha256"] == descriptor_sha, "claim_descriptor_bytes_mismatch")
    require(type(claim["proof"]) is str and re.fullmatch(r"[0-9a-f]{64}", claim["proof"]) is not None, "claim_proof_shape_invalid")
    require(parsed.image_reference() == report["image_reference"], "packed_image_reference_changed")
    report.update({
        "archive_sha256": sha256(archive_bytes),
        "archive_size_bytes": len(archive_bytes),
        "members": [member.filename for member in members],
        "zip_crc_pass": True,
        "all_ten_input_fields_unchanged": True,
        "closed_twelve_field_schema_pass": True,
        "official_parser_and_jcs_digest_pass": True,
        "descriptor_sha256": descriptor_sha,
        "descriptor_digest": descriptor["descriptor_digest"],
        "team_number": TEAM_NUMBER,
        "team_id": descriptor["team_id"],
        "claim_v2_shape_and_exact_descriptor_binding_pass": True,
        "claim_hmac_independently_verified": False,
        "team_alias_independently_derived": False,
        "team_key_requested_or_read": False,
        "claim_proof_printed_or_recorded": False,
    })
    return report


def verify_release_ledger(track: str, path: Path, report: dict) -> dict:
    """Bind local publication evidence; do not claim to repeat its remote checks."""
    raw = read_bounded(path, MAX_DESCRIPTOR_BYTES)
    ledger = strict_json(raw)
    track_id = {"coding": "t1", "analysis": "t4"}[track]
    require(ledger.get("track") == track_id, "ledger_track_mismatch")
    require(ledger.get("repository") == f"felix772/agenthon-2026-{track_id}", "ledger_repository_mismatch")
    require(ledger.get("registry_digest") == report["image_digest"], "ledger_registry_digest_mismatch")
    require(report["image_reference"] == f"docker.io/{ledger['repository']}@{ledger['registry_digest']}", "ledger_image_reference_mismatch")
    require(ledger.get("descriptor_input_sha256") == report["descriptor_input_sha256"], "ledger_descriptor_bytes_mismatch")
    local_image_id = ledger.get("local_image_id")
    require(type(local_image_id) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", local_image_id) is not None, "ledger_local_image_id_invalid")
    for field in ("image_audit_passed", "anonymous_pull_passed", "release_ready_for_official_pack"):
        require(ledger.get(field) is True, "ledger_release_gate_not_passed")
    return {
        "release_ledger_path": str(path.resolve()),
        "release_ledger_sha256": sha256(raw),
        "release_ledger_binding_pass": True,
        "ledger_local_image_id": local_image_id,
        "ledger_attests_image_audit_and_anonymous_pull": True,
        "remote_checks_independently_repeated_by_verifier": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", choices=("coding", "analysis"), required=True)
    parser.add_argument("--descriptor", type=Path, required=True, help="Approved ten-field descriptor-input.json")
    parser.add_argument("--archive", type=Path, help="Officially packed submission.zip; required unless --preflight")
    parser.add_argument("--report", type=Path, required=True, help="New JSON report path; refuses overwrite")
    parser.add_argument("--release-ledger", type=Path, help="Bind root's concrete release ledger and descriptor byte hash; no remote check is repeated")
    parser.add_argument("--preflight", action="store_true", help="Validate input shape before official hidden prompt; does not prove remote pullability")
    args = parser.parse_args(argv)
    if args.preflight == (args.archive is not None):
        parser.error("use either --preflight or --archive")
    if args.report.exists() or args.report.is_symlink():
        print("refused: report_already_exists", file=sys.stderr)
        return 1
    report = {
        "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
        "track": args.track,
        "mode": "descriptor_preflight" if args.preflight else "package_verification",
        "verifier_sha256": sha256(Path(__file__).read_bytes()),
        "descriptor_path": str(args.descriptor.resolve()),
        "archive_path": str(args.archive.resolve()) if args.archive else None,
        "passed": False,
    }
    try:
        result = validate_input(args.track, args.descriptor)[2] if args.preflight else verify_package(args.track, args.descriptor, args.archive)
        if args.release_ledger:
            result.update(verify_release_ledger(args.track, args.release_ledger, result))
        report.update(result)
        report["passed"] = True
    except Exception as exc:
        # Library exceptions can include document data; never print their text or traceback.
        report["failure_code"] = str(exc) if isinstance(exc, Refused) else "validation_failed_" + type(exc).__name__
    try:
        with args.report.open("x", encoding="utf-8") as output:
            output.write(json.dumps(report, indent=2) + "\n")
    except OSError:
        print("refused: report_could_not_be_created", file=sys.stderr)
        return 1
    print(("PASS" if report["passed"] else "FAIL") + ": " + str(args.report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
