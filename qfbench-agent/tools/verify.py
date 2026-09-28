"""Development-only adapter: serialize the unchanged official verifier's result."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path

from qfbench2_common.smoke import run_smoke
from qfbench2_track_coding.scoring import build_verifier


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--unit", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        verdict = run_smoke(args.unit, args.out, build_verifier)
        payload = asdict(verdict)
        code = 0 if verdict.admissible else 1
    except (Exception, SystemExit) as exc:
        payload = {"admissible": False, "environment_error": str(exc)}
        code = 2
    args.report.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
