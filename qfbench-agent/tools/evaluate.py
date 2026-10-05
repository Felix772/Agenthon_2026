"""Run each public task once in Docker, then run the official Linux verifier.

This tool is a development launcher, not the organizer's submission runner.
Only the verifier can see public checks. No changes to public units are made.
"""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
import tomllib
import uuid

EXCLUDED = {"checks", "reference", "reference_data", "solution", ".git", ".venv", "__pycache__"}
MODEL_ENV = ("MODEL_ENDPOINT", "MODEL_NAME", "MODEL_TOKEN", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy", "QFBENCH_NETWORK", "QFBENCH_SEED", "AGENT_MAX_ATTEMPTS", "AGENT_SOFT_TIMEOUT_SEC")


def runtime_flags(cpus, memory):
    """Published Development restrictions; GPU provisioning remains external."""
    return ["--read-only", "--user=65534:65534", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=256",
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=64m",
            "--ulimit=nofile=1024:1024", "--ulimit=nproc=256:256",
            "--ulimit=fsize=67108864:67108864", "--cpus", str(cpus),
            "--memory", str(memory), "--memory-swap", str(memory)]


def output_tree_size(output):
    total = 0
    for path in output.rglob("*"):
        if path.is_symlink():
            raise ValueError("Output symlinks are forbidden")
        if path.is_file():
            total += path.stat().st_size
    if total > 64 * 1024 * 1024:
        raise ValueError("Complete output tree exceeds the official 64 MiB limit")
    return total


def mount(source, target, readonly=False):
    if "," in str(source):
        raise ValueError("Docker bind paths containing commas are unsupported")
    return ["--mount", f"type=bind,source={source},target={target}" + (",readonly" if readonly else "")]


def run_container(command, name, timeout, log):
    started = time.monotonic()
    evidence = {"status": "launching", "timeout_sec": timeout, "outer_watchdog": True}
    status_file = log.with_suffix(".status.json")
    status_file.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    with log.open("w", encoding="utf-8") as handle:
        try:
            result = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT, timeout=timeout)
            evidence.update(status="exited", exit_code=result.returncode, timed_out=result.returncode == 124)
        except subprocess.TimeoutExpired:
            evidence.update(status="outer_timeout", exit_code=124, timed_out=True)
        except OSError as exc:
            evidence.update(status="launch_failed", exit_code=None, timed_out=False, error_type=type(exc).__name__)
        finally:
            evidence["elapsed_sec"] = round(time.monotonic() - started, 3)
            status_file.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
            # A timeout kills docker.exe, not necessarily the container. Always
            # remove just this run's uniquely named container as well.
            try:
                subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            except (OSError, subprocess.TimeoutExpired) as exc:
                evidence["cleanup_error_type"] = type(exc).__name__
            evidence["including_cleanup_sec"] = round(time.monotonic() - started, 3)
            status_file.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    return evidence


def budget_worksheet(units, soft_timeout=360):
    if not math.isfinite(soft_timeout) or soft_timeout <= 0:
        raise ValueError("Soft timeout must be positive and finite")
    rows = []
    for unit in units:
        card = tomllib.loads((unit / "card.toml").read_text(encoding="utf-8"))
        timeout = float(card["agent"]["timeout_sec"])
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Invalid task timeout")
        rows.append({"task": unit.name, "card_timeout_sec": timeout, "solve_cap_sec": min(soft_timeout, timeout)})
    total = sum(row["solve_cap_sec"] for row in rows)
    return {"units": rows, "solve_cap_sum_sec": total, "internal_solve_target_sec": 34560,
            "within_internal_solve_target": total <= 34560, "ingestion_limit_sec": 43200,
            "cap_enforcement": "CLI parent-process watchdog; child card timer and 15s publication reserve at 360s",
            "parent_reaping_grace_sec_per_unit": 2,
            "max_roster_reaping_grace_sec": 2 * len(rows),
            "setup_pull_ingestion_overhead_sec": None, "safety_reserve_sec": None,
            "release_gate": "observed solve sum + measured setup/pull/ingestion overhead + safety reserve <= 43200",
            "release_gate_status": "unmeasured; worksheet is not a runtime validation"}


def stage_input(unit, destination):
    for path in unit.rglob("*"):
        if path.is_symlink():
            raise ValueError("Task symlinks are unsupported")
    shutil.copytree(unit, destination, ignore=shutil.ignore_patterns(*EXCLUDED))


def verifier_mounts(unit, output):
    """Build the task-specific mount layout used only by trusted checks."""
    command = mount(unit, "/input", True)
    command += mount(output, "/app/output", True) + mount(output, "/output", True)
    checks = unit / "checks"
    if checks.is_dir():
        command += mount(checks, "/tests", True)
    data = unit / "environment" / "data"
    if data.is_dir():
        command += mount(data, "/app/data", True)
        # A few public checkers retain the task image's old /app/<file>
        # convention. Only the verifier sees these input files.
        for source in sorted(data.iterdir()):
            if source.is_file():
                command += mount(source, "/app/" + source.name, True)
    return command


def main():
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-repo", type=Path, default=project.parent / "track1-coding-public")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--unit", action="append", help="Unit directory name; repeat for a selected suite")
    selection.add_argument("--all", action="store_true")
    parser.add_argument("--image", default="qfbench-agent:dev")
    parser.add_argument("--harness-image", default="qfbench-harness:dev")
    parser.add_argument("--network", default="none", help="Organizer-configured development network; default has no model access")
    parser.add_argument("--results", type=Path)
    args = parser.parse_args()
    if any(not os.environ.get(key) for key in ("MODEL_ENDPOINT", "MODEL_NAME", "MODEL_TOKEN")):
        parser.error("Set organizer MODEL_ENDPOINT, MODEL_NAME and MODEL_TOKEN before evaluation. Use the test suite for offline mock tests.")
    if args.network == "none":
        parser.error("A real model solve needs an explicitly configured --network; 'none' cannot reach MODEL_ENDPOINT.")
    public = args.public_repo.resolve(strict=True)
    units_root = public / "units"
    units = sorted(p for p in units_root.iterdir() if p.is_dir() and (p / "card.toml").is_file()) if args.all else [units_root / name for name in args.unit]
    if len(set(units)) != len(units) or not units:
        parser.error("Select a nonempty set of unique tasks")
    for unit in units:
        if unit.resolve().parent != units_root.resolve():
            parser.error("--unit must name a direct child of the public units directory")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    results = (args.results or project / "artifacts" / ("eval-" + stamp)).resolve()
    if results.is_relative_to(public):
        parser.error("Evaluation results must be outside the public repository")
    results.mkdir(parents=True, exist_ok=False)
    image_info = json.loads(subprocess.check_output(["docker", "image", "inspect", args.image], text=True))[0]
    harness_info = json.loads(subprocess.check_output(["docker", "image", "inspect", args.harness_image], text=True))[0]
    metadata = {"image": args.image, "image_id": image_info["Id"], "harness_image_id": harness_info["Id"],
                "model": os.environ["MODEL_NAME"], "selected_tasks": len(units), "attempts_per_task": 1,
                "public_commit": subprocess.check_output(["git", "-c", f"safe.directory={public.as_posix()}", "-C", str(public), "rev-parse", "HEAD"], text=True).strip()}
    (results / "environment.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    roster = sorted(p for p in units_root.iterdir() if p.is_dir() and (p / "card.toml").is_file())
    worksheet = budget_worksheet(roster, float(os.environ.get("AGENT_SOFT_TIMEOUT_SEC", "360")))
    (results / "budget-worksheet.json").write_text(json.dumps(worksheet, indent=2), encoding="utf-8")
    records = []
    for index, unit in enumerate(units):
        run = results / unit.name
        run.mkdir()
        output = run / "output"
        output.mkdir()
        output.chmod(0o777)
        record = {"task": unit.name, "passed": False}
        ingestion_started = time.monotonic()
        try:
            card = tomllib.loads((unit / "card.toml").read_text(encoding="utf-8"))
            timeout = float(card["agent"]["timeout_sec"])
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("Invalid task timeout")
            stage_input(unit, run / "input")
            name = "qfagent-" + uuid.uuid4().hex[:12]
            command = ["docker", "run", "--rm", "--name", name, "--network", args.network,
                       *runtime_flags(card["environment"]["cpus"], card["environment"]["memory"])]
            for key in MODEL_ENV:
                if key in os.environ:
                    command += ["-e", key]
            command += mount(run / "input", "/input", True) + mount(output, "/app/output") + mount(output, "/output")
            command += [image_info["Id"], "solve", "--task-dir", "/input", "--out", "/app/output"]
            local_ceiling = min(timeout, float(os.environ.get("AGENT_SOFT_TIMEOUT_SEC", "360"))) + 15
            record["agent"] = run_container(command, name, local_ceiling, run / "agent.log")
            record["local_ingestion_sec"] = round(time.monotonic() - ingestion_started, 3)
            record["output_bytes"] = output_tree_size(output)
            if record["agent"]["exit_code"] == 0:
                name = "qfverify-" + uuid.uuid4().hex[:12]
                command = ["docker", "run", "--rm", "--name", name, "--network=none", "--entrypoint", "python"]
                command += mount(public, "/public", True) + mount(project / "tools", "/tools", True)
                command += verifier_mounts(unit, output) + mount(run, "/review")
                command += [harness_info["Id"], "/tools/verify.py", "--unit", "/input",
                            "--out", "/app/output", "--report", "/review/verdict.json"]
                record["verifier"] = run_container(command, name, float(card["verifier"]["timeout_sec"]) + 30, run / "verifier.log")
                if (run / "verdict.json").exists():
                    verdict = json.loads((run / "verdict.json").read_text(encoding="utf-8"))
                    record["passed"] = record["verifier"]["exit_code"] == 0 and verdict.get("admissible") is True
                    record["failed_gates"] = [k for k, v in verdict.get("gate_results", {}).items() if not v["passed"]]
                    record["environment_error"] = verdict.get("environment_error")
                else:
                    record["environment_error"] = "Verifier did not produce a structured verdict; see verifier.log"
            agent_report = output / ".agent/run.json"
            if agent_report.exists():
                diagnostics = json.loads(agent_report.read_text(encoding="utf-8"))
                record["model_usage"] = diagnostics.get("model_usage")
                record["agent_diagnostics"] = {key: diagnostics.get(key) for key in ("status", "stage", "failure_category", "attempts", "events", "transport")}
        except Exception as exc:
            record["environment_error"] = type(exc).__name__
        # Preserve the last agent stage even when output validation failed.
        if "agent_diagnostics" not in record and (output / ".agent/run.json").is_file():
            try:
                diagnostics = json.loads((output / ".agent/run.json").read_text(encoding="utf-8"))
                record["model_usage"] = diagnostics.get("model_usage")
                record["agent_diagnostics"] = {key: diagnostics.get(key) for key in ("status", "stage", "failure_category", "attempts", "events", "transport")}
            except (ValueError, OSError):
                record["agent_report_unreadable"] = True
        records.append(record)
        with (results / "results.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        print(f"[{index + 1}/{len(units)}] {unit.name}: {'PASS' if record['passed'] else 'FAIL'}", flush=True)
    passed = sum(r["passed"] for r in records)
    summary = {"tasks": len(records), "passed": passed, "one_run_pass_rate": passed / len(records) if records else 0,
               "environment_failures": sum(bool(r.get("environment_error")) for r in records), "results": str(results),
               "timed_out": sum(bool(r.get("agent", {}).get("timed_out")) for r in records), "not_reached": 0,
               "observed_local_ingestion_sec": sum(r.get("local_ingestion_sec", 0) for r in records),
               "timing_scope": "Local agent staging/launch/execution/cleanup only; no claim about organizer setup/pull overhead"}
    (results / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if passed == len(records) and records else 1


if __name__ == "__main__":
    raise SystemExit(main())
