"""Strict-container diagnostic of a single-frame log parser on public scenarios."""

import hashlib
import json
from pathlib import Path
import subprocess
import time


HERE = Path(__file__).resolve().parent
SOURCE = HERE / "t3-exact-profile-20260927-v3"
OUT = HERE / "t3-parse-probe-20260927"
WORKER = HERE / "t3_parse_probe_worker.py"
IMAGE = "sha256:c5724d34e2a8c951e4645b7c9aed1b95e3eabf1bd3dc24c0195055700cdbd45d"
ROSTER = [
    "t3-eq-deterministic-baseline", "t3-gb-pop-128-agents",
    "t3-gbatch-dense-3", "t3-cancelmodify-lifecycle", "t3-as01-base-mix",
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main():
    OUT.mkdir(exist_ok=False)
    prior = json.loads((HERE / "t3-06-release-20260925" / "report.json").read_text())
    reference = {r["unit"]: r["runs"][0] for r in prior["records"] if r["status"] == "passed"}
    plan = {"image": IMAGE, "worker_sha256": sha(WORKER), "roster": ROSTER,
            "baseline_input": str(SOURCE), "strict_container": True,
            "mode": "diagnostic only; original parse result is returned to simulator",
            "rankable": False}
    write(OUT / "plan.json", plan)
    records = []
    for unit in ROSTER:
        inp = (SOURCE / unit / "input").resolve()
        root = OUT / unit
        output = root / "output"
        diagnostic = root / "diagnostic"
        output.mkdir(parents=True)
        diagnostic.mkdir()
        name = "t3-parse-" + unit
        cmd = ["docker", "run", "--name", name, "--network", "none",
               "--read-only", "--user", "65534:65534", "--cap-drop=ALL",
               "--security-opt", "no-new-privileges", "--cpus", "4",
               "--memory", "4g", "--memory-swap", "4g", "--pids-limit", "256",
               "--ulimit", "nofile=1024:1024", "--ulimit", "nproc=256:256",
               "--ulimit", "fsize=67108864:67108864",
               "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
               "--mount", f"type=bind,src={inp},dst=/input,readonly",
               "--mount", f"type=bind,src={output.resolve()},dst=/output",
               "--mount", f"type=bind,src={diagnostic.resolve()},dst=/diagnostic",
               "--mount", f"type=bind,src={WORKER.resolve()},dst=/diagnostic_code/worker.py,readonly",
               IMAGE, "python", "/diagnostic_code/worker.py"]
        row = {"unit": unit, "command": cmd}
        print("START", unit, flush=True)
        try:
            started = time.perf_counter()
            with (root / "run.log").open("w") as log:
                process = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=900)
            row["host_wall_sec"] = time.perf_counter() - started
            row["exit_code"] = process.returncode
            assert process.returncode == 0
            row["probe"] = json.loads((diagnostic / "probe.json").read_text())
            row["stable"] = {p.relative_to(output).as_posix(): sha(p)
                             for p in output.rglob("*.parquet")}
            expected = reference[unit]
            assert row["stable"] == {p: v["sha256"] for p, v in expected["files"].items()
                                     if p.endswith(".parquet")}
            assert row["probe"]["event_count"] == expected["host_n_events"]
            row["release_identity"] = True
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            records.append(row)
            write(OUT / "runs.json", records)
        print("PASS", unit, round(row["host_wall_sec"], 3),
              "parse", round(row["probe"]["original_sec"], 3),
              "vs", round(row["probe"]["single_frame_sec"], 3), flush=True)
    write(OUT / "summary.json", {
        "runs": len(records), "all_release_identity": True,
        "original_parse_sec": sum(r["probe"]["original_sec"] for r in records),
        "single_frame_sec": sum(r["probe"]["single_frame_sec"] for r in records),
        "all_values_and_dtypes_identical": all(
            r["probe"]["all_values_and_dtypes_identical"] for r in records),
    })


if __name__ == "__main__":
    main()
