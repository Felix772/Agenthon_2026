"""Check the real T2 ZIP without requesting or printing the Team Key."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path

from qfbench2_common.contracts.descriptor import (
    SubmissionDescriptor,
    seal_descriptor_digest,
)


ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / "releases/t2-development-20260926/submission.zip"
REPORT = Path(__file__).with_name("t2-package-validation-20260926.json")
IMAGE_DIGEST = "sha256:faa9e5cb19d4964f42dd97c4a9622f05c6507dea37656eb15eef672842bf8f6c"
FIELDS = {
    "schema_version", "interface_version", "competition_id", "team_id",
    "track", "phase", "category", "image", "image_access", "models",
    "license", "descriptor_digest",
}


def main() -> None:
    with zipfile.ZipFile(ARCHIVE) as archive:
        names = archive.namelist()
        if names != ["submission.json", "team-claim.json"]:
            raise RuntimeError(f"unexpected ZIP members: {names!r}")
        descriptor_bytes = archive.read("submission.json")
        claim_bytes = archive.read("team-claim.json")
    descriptor = json.loads(descriptor_bytes)
    claim = json.loads(claim_bytes)
    if set(descriptor) != FIELDS:
        raise RuntimeError("descriptor fields do not match the closed C5 vocabulary")
    SubmissionDescriptor.from_mapping(descriptor)
    if seal_descriptor_digest(descriptor) != descriptor:
        raise RuntimeError("descriptor digest does not reseal identically")
    expected = {
        "registry": "docker.io",
        "repository": "felix772/agenthon-2026-t2",
        "digest": IMAGE_DIGEST,
    }
    if (
        descriptor["competition_id"] != "agenthon2026-forecasting-dev"
        or descriptor["track"] != "forecasting"
        or descriptor["phase"] != "dev"
        or descriptor["category"] != "api"
        or descriptor["image"] != expected
        or descriptor["image_access"] != "public"
        or descriptor["models"] != []
        or descriptor["license"] != "MIT"
        or not re.fullmatch(r"team-[0-9a-f]{32}", descriptor["team_id"])
    ):
        raise RuntimeError("descriptor release fields differ from the approved T2 candidate")
    if set(claim) != {"schema_version", "site_team_id", "descriptor_sha256", "proof"}:
        raise RuntimeError("team claim fields differ from the v2 contract")
    descriptor_sha256 = hashlib.sha256(descriptor_bytes).hexdigest()
    if (
        claim["schema_version"] != "2.0"
        or claim["site_team_id"] != 321
        or claim["descriptor_sha256"] != descriptor_sha256
        or not re.fullmatch(r"[0-9a-f]{64}", claim["proof"])
    ):
        raise RuntimeError("team claim structure or descriptor binding failed")
    report = {
        "archive": str(ARCHIVE.relative_to(ROOT)),
        "archive_size_bytes": ARCHIVE.stat().st_size,
        "archive_sha256": hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),
        "members": names,
        "toolkit_parser_and_digest_pass": True,
        "descriptor_sha256": descriptor_sha256,
        "team_id": descriptor["team_id"],
        "team_number": claim["site_team_id"],
        "claim_v2_structure_and_binding_pass": True,
        "claim_hmac_independently_verified": False,
        "image_digest": IMAGE_DIGEST,
        "license": "MIT",
        "models": [],
        "secret_material_inspected_or_recorded": False,
    }
    REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"official ZIP structure, descriptor and claim binding pass; {REPORT}")


if __name__ == "__main__":
    main()
