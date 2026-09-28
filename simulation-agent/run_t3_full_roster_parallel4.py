"""Frozen current-verifier admission and paired 71-unit T3 public proxy.

Run with the Linux T3 validation Python. The preflight batch smoke must pass
before full warm-ups, and full warm-ups before paired timing. Each phase can be
resumed after an explicitly recorded infrastructure interruption.
"""

from __future__ import annotations

import argparse
import dataclasses
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import shutil
import statistics
import subprocess
import sys
import traceback

import run_parallel_batch_experiment as screen
from throughput import node_fingerprint
from throughput.run_unit import (
    UnitRecord, UnitRun, _host_n_events, _reported_n_events,
    merge_host_metrics, retain_output,
)
from throughput.timer import timed_container_run
from qfbench2_track_simulation.limits import stable_paths_for
from qfbench2_track_simulation.scoring import build_developer_verifier

ROOT = screen.ROOT
SOURCE = screen.SOURCE
DEFAULT_OUT = ROOT / "project-evidence/t3-parallel-batch4-full-roster-20260927"
BASE_ID = screen.BASE_ID
SELECTED_TAG = "simulation-agent:parallel-batch4-20260927"
SELECTED_ID = "sha256:df5e1b9d96abf06947a33cf499c3b4feed271fa31f6439de8ce49ff98a834489"
VARIANTS = ("baseline", "selected-default4")


def roster() -> list[Path]:
    units = sorted(path for path in (SOURCE / "units").iterdir() if path.is_dir())
    if len(units) != 71 or sum((path / "batch.json").exists() for path in units) != 6:
        raise RuntimeError("Expected exactly 65 single plus six batch public units")
    return units


def is_batch(unit: Path) -> bool:
    return (unit / "batch.json").exists()


def stage_inputs(units: list[Path], out: Path) -> dict[str, dict[str, str]]:
    frozen: dict[str, dict[str, str]] = {}
    for unit in units:
        manifest = {row["path"]: row["sha256"] for row in json.loads(
            (unit / "manifest.json").read_text(encoding="utf-8")
        )["files"]}
        sources = sorted((unit / "scenarios").glob("*.json")) if is_batch(unit) else [unit / "scenario.json"]
        if not sources or any(not source.is_file() for source in sources):
            raise RuntimeError(f"Missing solver-facing input for {unit.name}")
        frozen[unit.name] = {}
        for source in sources:
            relative = source.relative_to(unit).as_posix()
            digest = screen.sha256(source)
            if manifest.get(relative) != digest:
                raise RuntimeError(f"Public manifest mismatch: {unit.name}/{relative}")
            target = out / "inputs" / unit.name / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if screen.sha256(target) != digest:
                    raise RuntimeError(f"Frozen staged input changed: {target}")
            else:
                shutil.copyfile(source, target)
            frozen[unit.name][relative] = digest
    return frozen


def schedule(units: list[Path], phase: str) -> list[tuple[str, str, int]]:
    rows = []
    if phase == "batch-smoke":
        for unit in units:
            if is_batch(unit):
                rows.extend((unit.name, "selected-smoke", repeat) for repeat in (0, 1))
    elif phase == "warmup":
        for unit in units:
            rows.extend((unit.name, variant, 0) for variant in VARIANTS)
    elif phase == "timing":
        for unit in units:
            for repeat in (1, 2):
                order = VARIANTS if repeat % 2 == 0 else tuple(reversed(VARIANTS))
                rows.extend((unit.name, variant, repeat) for variant in order)
    else:
        raise ValueError(phase)
    return rows


def run_single(
    unit: Path, variant: str, repeat: int, image_id: str, out: Path, attempt: int,
    fingerprint: dict,
) -> dict:
    dest = out / "attempts" / unit.name / variant / str(repeat) / str(attempt)
    dest.mkdir(parents=True, exist_ok=False)
    raw = dest / "raw"
    raw.mkdir()
    raw.chmod(0o777)
    accepted = dest / "accepted" / unit.name
    cidfile = dest / "container.cid"
    input_dir = out / "inputs" / unit.name
    name = f"t3-p4-{unit.name}-{variant}-{repeat}-{attempt}"
    cmd = [
        "docker", "run", "--name", name, "--cidfile", str(cidfile),
        "--network", "none", "--read-only", "--user", "65534:65534",
        "--cap-drop=ALL", "--security-opt", "no-new-privileges",
        "--cpus", "4", "--memory", "16g", "--memory-swap", "16g",
        "--pids-limit", "256", "--ulimit", "nofile=1024:1024",
        "--ulimit", "nproc=256:256", "--ulimit", "fsize=67108864:67108864",
        "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
        "--mount", f"type=bind,src={input_dir},dst=/input,readonly",
        "--mount", f"type=bind,src={raw},dst=/output",
        image_id, "simulate", "--config", "/input/scenario.json", "--out", "/output/trace.parquet",
    ]
    row = {
        "key": screen.key(unit.name, variant, repeat), "unit": unit.name,
        "variant": variant, "repeat": repeat,
        "phase": "warmup" if repeat == 0 else "timing",
        "attempt": attempt, "command": cmd, "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    screen.write(dest / "attempt.json", row)
    try:
        proc, host_sec, gpu_sec, peak = timed_container_run(
            cmd, cidfile=cidfile, gpus=None, timeout_sec=300
        )
        row.update(
            exit_code=proc.returncode, host_observed_seconds=host_sec,
            host_gpu_seconds=gpu_sec, host_peak_memory_bytes=peak,
            stdout=proc.stdout[-5000:].decode(errors="replace"),
            stderr=proc.stderr[-5000:].decode(errors="replace"),
        )
        state = json.loads(subprocess.check_output(["docker", "inspect", name], text=True))[0]["State"]
        row["container_state"] = state
        if proc.returncode != 0 or state["OOMKilled"]:
            row.update(status="failed", failure_class="participant", rate=0.0)
            return row
        container_sec = screen.docker_duration_ns(state["StartedAt"], state["FinishedAt"]) / 1e9
        row["container_seconds"] = container_sec
        if container_sec > 300:
            row.update(status="failed", failure_class="participant_timeout", rate=0.0)
            return row
        row["raw_output_bytes"] = sum(
            path.lstat().st_size for path in raw.rglob("*") if path.is_file() or path.is_symlink()
        )
        if row["raw_output_bytes"] > 64 * 1024**2:
            row.update(status="failed", failure_class="participant_resource", rate=0.0)
            return row
        retain_output(raw, accepted, unit)
        files = {
            path.relative_to(accepted).as_posix(): {"sha256": screen.sha256(path), "bytes": path.stat().st_size}
            for path in accepted.rglob("*") if path.is_file()
        }
        row["files"] = files
        row["output_bytes"] = sum(item["bytes"] for item in files.values())
        if row["output_bytes"] > 64 * 1024**2:
            row.update(status="failed", failure_class="participant_resource", rate=0.0)
            return row
        count = _host_n_events(accepted, False)
        if count != _reported_n_events(accepted, False):
            row.update(status="failed", failure_class="participant_schema", rate=0.0)
            return row
        row["event_count"] = count
        row["rate"] = count / container_sec
        metric = UnitRun(row["rate"], count, count, container_sec, gpu_sec, peak, 0)
        record = UnitRecord(
            unit=unit.name, verb="simulate", image=image_id,
            warmup_discarded=False, runs=[metric], median_events_per_sec=row["rate"],
            median_host_gpu_seconds=gpu_sec, median_host_peak_memory_bytes=peak,
            node_fingerprint=fingerprint,
        )
        merge_host_metrics(record, accepted.parent / "host_metrics.json")
        ctx = {"unit_dir": unit, "output_dir": accepted}
        verdict = build_developer_verifier(ctx).run(ctx)
        row["verdict"] = dataclasses.asdict(verdict)
        if not verdict.admissible:
            row.update(status="failed", failure_class="participant_semantics", rate=0.0)
            return row
        stable_paths = stable_paths_for(unit)
        if not all(path in files for path in stable_paths):
            row.update(status="failed", failure_class="participant_stable_missing", rate=0.0)
            return row
        row["stable"] = {path: files[path]["sha256"] for path in stable_paths}
        row["status"] = "passed"
        return row
    except (subprocess.SubprocessError, OSError) as exc:
        row.update(status="failed", failure_class="infrastructure", error_type=type(exc).__name__,
                   error=str(exc), traceback=traceback.format_exc())
        return row
    except Exception as exc:
        row.update(status="failed", failure_class="unclassified", error_type=type(exc).__name__,
                   error=str(exc), traceback=traceback.format_exc())
        return row
    finally:
        try:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)
        except (subprocess.SubprocessError, OSError) as exc:
            row["cleanup_error"] = f"{type(exc).__name__}: {exc}"
        row["finished_at"] = datetime.now(timezone.utc).isoformat()
        screen.write(dest / "attempt.json", row)


def summary(records: list[dict], units: list[Path]) -> dict:
    passed = {row["key"]: row for row in records if row["status"] == "passed"}
    failures = [row for row in records if row["status"] == "failed" and
                row.get("failure_class", "").startswith("participant")]
    batches = [unit for unit in units if is_batch(unit)]
    batch_smoke_keys = {
        screen.key(unit.name, "selected-smoke", repeat)
        for unit in batches for repeat in (0, 1)
    }
    full_warm_keys = {screen.key(unit.name, variant, 0) for unit in units for variant in VARIANTS}
    result = {
        "batch_smoke_passed": batch_smoke_keys <= passed.keys(),
        "full_warmups_passed": full_warm_keys <= passed.keys(),
        "timing_complete": False,
        "semantic_gate": False,
        "participant_failures_retained_at_zero": [row["key"] for row in failures],
        "variants": {},
    }
    rate_maps = {}
    for variant in VARIANTS:
        rates = {}
        for unit in units:
            failed = any(row["unit"] == unit.name and row["variant"] == variant for row in failures)
            if failed:
                rates[unit.name] = 0.0
                continue
            measured = [passed.get(screen.key(unit.name, variant, repeat)) for repeat in (1, 2)]
            if all(measured):
                rates[unit.name] = statistics.median(row["rate"] for row in measured)
        rate_maps[variant] = rates if len(rates) == len(units) else None
        result["variants"][variant] = {
            "resolved_unit_count": len(rates),
            "fixed_71_unit_denominator": len(units),
            "unit_median_rates": rates,
        }
    if any(rates is None for rates in rate_maps.values()):
        return result
    result["timing_complete"] = True
    result["semantic_gate"] = result["batch_smoke_passed"] and result["full_warmups_passed"] and not failures
    base = rate_maps["baseline"]
    selected = rate_maps["selected-default4"]
    for variant in VARIANTS:
        rates = rate_maps[variant]
        result["variants"][variant].update({
            "mean_of_71_unit_medians": statistics.mean(rates.values()),
            "single_mean": statistics.mean(rates[unit.name] for unit in units if not is_batch(unit)),
            "batch_mean": statistics.mean(rates[unit.name] for unit in batches),
        })
    differences = [selected[unit.name] - base[unit.name] for unit in units]
    rng = random.Random(20260927)
    bootstrap = sorted(statistics.mean(rng.choices(differences, k=len(units))) for _ in range(10000))
    result["paired_selected_difference"] = statistics.mean(differences)
    result["paired_71_unit_bootstrap95_difference"] = [bootstrap[249], bootstrap[9749]]
    result["per_unit_differences"] = {unit.name: selected[unit.name] - base[unit.name] for unit in units}
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("batch-smoke", "warmup", "timing", "all"), required=True)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-runs", type=int, help="bounded preflight; resume the same frozen plan")
    args = parser.parse_args()
    if args.max_runs is not None and args.max_runs < 1:
        parser.error("--max-runs must be positive")
    if sys.platform != "linux":
        parser.error("run under the existing Linux validation environment")
    screen.verify_current_verifier_source()
    units = roster()
    selected = screen.inspect_image(SELECTED_TAG)
    if selected != SELECTED_ID or screen.inspect_image(BASE_ID) != BASE_ID:
        raise RuntimeError("Frozen selected or baseline image identity changed")
    out = args.out.resolve()
    if args.resume:
        if not out.exists():
            raise RuntimeError("No full-roster experiment exists to resume")
    else:
        out.mkdir(parents=True, exist_ok=False)
    inputs = stage_inputs(units, out)
    fingerprint = node_fingerprint.collect().to_dict()
    plan = {
        "baseline_image": BASE_ID, "selected_image": SELECTED_ID,
        "current_rules_ref": "f910de231209ebbca060efee47fe2c41aabe4bce",
        "public_input_source_ref": "1504b37b8472f7f949d4b8f80b7c8b2b71ab0e43",
        "current_verifier_archive_sha256": screen.VERIFIER_ARCHIVE_SHA256,
        "units": [unit.name for unit in units], "variants": list(VARIANTS),
        "input_hashes": inputs, "node_fingerprint": fingerprint,
        "batch_smoke": "one selected no-flag warm-up plus one repeat per batch before full roster",
        "full_roster_warmup_per_variant_unit": 1,
        "full_roster_measured_repeats_per_variant_unit": 2,
        "order": "per-unit measured rounds alternate selected-first, baseline-first",
        "timer": "Docker State.StartedAt to State.FinishedAt; hard container deadline 300s",
        "quota": {"cpus": 4, "memory_bytes": 16 * 1024**3, "tmpfs_bytes": 64 * 1024**2},
        "ranking": "arithmetic mean of 71 per-unit median measured event rates, participant unit failures zero",
        "command": "official no-worker-flag batch or single simulation verb",
        "rankable": False,
    }
    plan_path = out / "plan.json"
    if args.resume:
        if json.loads(plan_path.read_text(encoding="utf-8")) != plan:
            raise RuntimeError("Frozen 71-unit experiment plan changed")
        records = json.loads((out / "records.json").read_text(encoding="utf-8"))
    else:
        screen.write(plan_path, plan)
        records = []
        screen.write(out / "records.json", records)
    unit_map = {unit.name: unit for unit in units}
    phases = ("batch-smoke", "warmup", "timing") if args.phase == "all" else (args.phase,)
    started = 0
    for phase in phases:
        status = summary(records, units)
        if phase == "warmup" and not status["batch_smoke_passed"]:
            raise RuntimeError("Selected no-flag six-batch smoke must pass first")
        if phase == "timing" and not status["full_warmups_passed"]:
            raise RuntimeError("All 142 full-roster warm-ups must pass before timing")
        for unit_name, variant, repeat in schedule(units, phase):
            item_key = screen.key(unit_name, variant, repeat)
            if any(row["unit"] == unit_name and row["variant"] == variant and
                   row["status"] == "failed" and
                   row.get("failure_class", "").startswith("participant") for row in records):
                continue
            if any(row["key"] == item_key and row["status"] == "passed" for row in records):
                continue
            prior = [row for row in records if row["key"] == item_key and row["status"] == "failed"]
            if prior and prior[-1]["failure_class"] != "infrastructure":
                raise RuntimeError(f"Unclassified failure must be diagnosed: {item_key}")
            attempt_root = out / "attempts" / unit_name / variant / str(repeat)
            prior_attempts = ([int(path.name) for path in attempt_root.iterdir()
                               if path.is_dir() and path.name.isdigit()]
                              if attempt_root.exists() else [])
            attempt = max([0, *prior_attempts]) + 1
            unit = unit_map[unit_name]
            image = BASE_ID if variant == "baseline" else SELECTED_ID
            run = screen.run_one if is_batch(unit) else run_single
            row = run(unit, variant, repeat, image, out, attempt, fingerprint)
            records.append(row)
            started += 1
            screen.write(out / "records.json", records)
            print(row["status"], item_key, row.get("rate"), flush=True)
            if row["status"] != "passed":
                if row.get("failure_class", "").startswith("participant"):
                    if args.max_runs is not None and started >= args.max_runs:
                        screen.write(out / "summary.json", summary(records, units))
                        return 0
                    continue
                raise RuntimeError(f"Run failed; retained attempt {row['failure_class']}: {item_key}")
            # Full timing repeats compare with the full-roster warm-up. Batch
            # preflight repeat separately compares with its own preflight warm-up.
            first = next(record for record in records
                         if record["key"] == screen.key(unit_name, variant, 0)
                         and record["status"] == "passed")
            if row["stable"] != first["stable"] or row["event_count"] != first["event_count"]:
                row.update(status="failed", failure_class="participant_repeat", rate=0.0)
                screen.write(out / "records.json", records)
                screen.write(attempt_root / str(attempt) / "attempt.json", row)
                if args.max_runs is not None and started >= args.max_runs:
                    screen.write(out / "summary.json", summary(records, units))
                    return 0
                continue
            if args.max_runs is not None and started >= args.max_runs:
                screen.write(out / "summary.json", summary(records, units))
                return 0
        screen.write(out / "summary.json", summary(records, units))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
