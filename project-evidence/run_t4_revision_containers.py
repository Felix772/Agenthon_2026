"""Test a read-only source overlay on the released T4 runtime, then official smoke.

No House request, Docker publication, or platform submission is performed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[1] if len(SCRIPT.parents) > 1 else SCRIPT.parent
IMAGE = "agenthon-t4:emergency-20261009-v3"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inside-smoke", action="store_true")
    args = parser.parse_args()
    if args.inside_smoke:
        from qfbench2_common.smoke import run_smoke
        from qfbench2_track_analysis.scoring import SCORER_VERSION, build_smoke_verifier
        assert SCORER_VERSION == "5.2.2"
        rows = []
        for unit in sorted(Path("/upstream/units").glob("t4-*")):
            output = args.output / unit.name
            checksum = digest(output / "answer.json")
            result = run_smoke(unit, output, build_smoke_verifier)
            assert checksum == digest(output / "answer.json")
            row = {"unit": unit.name, "admissible": bool(result.admissible), "score": result.score}
            rows.append(row)
            print(json.dumps(row), flush=True)
        report = {"scorer": SCORER_VERSION, "rows": rows,
                  "passed": len(rows) == 11 and all(r["admissible"] and r["score"] is None for r in rows)}
        (args.output / "official-smoke.json").write_text(json.dumps(report, indent=2) + "\n")
        return 0 if report["passed"] else 1

    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    source = ROOT / "analysis-agent/analysis_agent/fallback.py"
    expected = ROOT / "project-evidence/t4-vintage-e2-20261009/candidate-source-v2"
    upstream = ROOT / ".validation/t4-e2-scorer-5.2.2-20261001"
    common = ROOT / "Agenthon2026-public/common"
    source_hash = digest(source)
    rows = []
    restrictions = ["--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges", "--user", "65534:65534",
                    "--cpus", "2", "--memory", "2g", "--pids-limit", "128",
                    "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m,mode=1777"]
    for index, unit in enumerate(sorted((upstream / "units").glob("t4-*"))):
        output = args.output / unit.name
        output.mkdir()
        command = ["docker", "run", *restrictions, "--name", f"t4-revision-e2-{index}",
                   "--mount", f"type=bind,source={unit},target=/unit,readonly",
                   "--mount", f"type=bind,source={source},target=/app/analysis-agent/analysis_agent/fallback.py,readonly",
                   "--mount", f"type=bind,source={output},target=/output", IMAGE,
                   "analyze", "--task", "/unit/task.json", "--corpus", "/unit/corpus",
                   "--out", "/output/answer.json", "--timeout", "30", "--diagnostics", "/output/runtime.json"]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        (output / "container.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        answer = output / "answer.json"
        row = {"unit": unit.name, "exit_code": result.returncode,
               "answer_sha256": digest(answer) if answer.is_file() else None,
               "matches_frozen_source_output": answer.is_file() and
                    json.loads(answer.read_text(encoding="utf-8")) ==
                    json.loads((expected / unit.name / "answer.json").read_text(encoding="utf-8"))}
        rows.append(row)
        print(json.dumps(row), flush=True)
    assert digest(source) == source_hash
    report = {"mode": "Released runtime with read-only candidate fallback source overlay; not a new release image",
              "image": IMAGE, "fallback_sha256": source_hash, "rows": rows,
              "passed": len(rows) == 11 and all(r["exit_code"] == 0 and r["matches_frozen_source_output"] for r in rows)}
    (args.output / "container-report.json").write_text(json.dumps(report, indent=2) + "\n")
    command = ["docker", "run", *restrictions, "--entrypoint", "python",
               "-e", "PYTHONPATH=/upstream:/common",
               "--mount", f"type=bind,source={upstream},target=/upstream,readonly",
               "--mount", f"type=bind,source={common},target=/common,readonly",
               "--mount", f"type=bind,source={Path(__file__).resolve()},target=/run-smoke.py,readonly",
               "--mount", f"type=bind,source={args.output},target=/outputs", IMAGE,
               "/run-smoke.py", "--inside-smoke", "--output", "/outputs"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    (args.output / "smoke.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return 0 if report["passed"] and result.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
