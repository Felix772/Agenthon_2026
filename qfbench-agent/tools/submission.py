"""Validate a descriptor, or delegate alias/pack to the official toolkit.

Examples: submission.py alias --team-number N
          submission.py pack --descriptor body.json --team-number N --out submission.zip
          submission.py body.json --out sealed.json
Requires the official toolkit environment. Never publishes or uploads.
"""
import argparse
import json
from pathlib import Path
import sys


def write_submission_zip(descriptor_path: Path, archive_path: Path):
    """Refuse the retired archive format before creating any files."""
    raise ValueError("One-file archives are retired; use submission.py pack (official team-claim packing)")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("alias", "pack"):
        from qfbench2_common.cli import main as official_main

        # The official CLI owns hidden input, key-file permissions, alias,
        # digest binding, proof, output permissions and refusal diagnostics.
        return official_main(["submission", *argv])

    # Reject key-like flags without echoing their possible values, including
    # on the legacy descriptor-only path. No key belongs on a command line.
    if any(arg.startswith("--team-k") for arg in argv):
        print("Use alias/pack with the official hidden prompt or --team-key-file.", file=sys.stderr)
        return 2
    from qfbench2_common.contracts.descriptor import SubmissionDescriptor, seal_descriptor_digest

    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("body", type=Path, help="JSON metadata body with actual organizer IDs and model disclosures")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--zip", dest="archive", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.archive:
        parser.error("--zip is retired; use submission.py pack --descriptor BODY --team-number N --out ZIP")
    body = json.loads(args.body.read_text(encoding="utf-8"))
    payload = seal_descriptor_digest(body)
    descriptor = SubmissionDescriptor.from_mapping(payload)
    with args.out.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    print("Validated:", descriptor.image_reference())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
