"""Frozen six-batch T3 semantic and paired timing experiment.

Run on Linux using the existing T3 validation environment. This script stages
only manifest-checked public inputs. It never builds, publishes or submits an
image. Start with --phase smoke; timing requires every smoke run to pass.
"""

from __future__ import annotations

import argparse
import calendar
import dataclasses
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import statistics
import subprocess
import sys
import tarfile
import tomllib
import traceback

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".validation/t3-release-source-20260925"
VERIFIER_ROOT = ROOT / ".validation/t3-current-verifier-20260927"
VERIFIER_ARCHIVE = ROOT / ".validation/t3-current-verifier-20260927.tar"
VERIFIER_ARCHIVE_SHA256 = "ba591213a97659d11e8e9617075cee410518dfbee119c283eb2d47fecd6c121c"
DEFAULT_OUT = ROOT / "project-evidence/t3-parallel-batch-experiment-20260927"
BASE_ID = "sha256:c5724d34e2a8c951e4645b7c9aed1b95e3eabf1bd3dc24c0195055700cdbd45d"
CANDIDATE_TAG = "simulation-agent:parallel-batch-20260927"
VARIANTS = ("baseline", "workers-1", "workers-2", "workers-4")
WORKERS = {"workers-1": 1, "workers-2": 2, "workers-4": 4}
sys.path.insert(0, str(VERIFIER_ROOT))
from throughput import node_fingerprint  # noqa: E402
from throughput.run_unit import (  # noqa: E402
    UnitRecord, UnitRun, _host_n_events, _reported_n_events,
    merge_host_metrics, retain_output,
)
from throughput.timer import timed_container_run  # noqa: E402
from qfbench2_track_simulation.limits import stable_paths_for  # noqa: E402
from qfbench2_track_simulation.scoring import build_developer_verifier  # noqa: E402


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path: Path, value: dict | list) -> None:
    path.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")


def inspect_image(image: str) -> str:
    return subprocess.check_output(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"], text=True
    ).strip()


def verify_current_verifier_source() -> None:
    if sha256(VERIFIER_ARCHIVE) != VERIFIER_ARCHIVE_SHA256:
        raise RuntimeError("Current T3 verifier archive changed")
    with tarfile.open(VERIFIER_ARCHIVE) as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError(f"Cannot read verifier source: {member.name}")
            expected = hashlib.sha256(source.read()).hexdigest()
            target = VERIFIER_ROOT / member.name
            if not target.is_file() or sha256(target) != expected:
                raise RuntimeError(f"Extracted current verifier changed: {member.name}")


def docker_duration_ns(started: str, finished: str) -> int:
    """Parse Docker RFC3339 nanosecond timestamps without float rounding."""
    def parse(value: str) -> int:
        if not value.endswith("Z"):
            raise ValueError(f"Expected Docker UTC timestamp: {value}")
        date, _, fraction = value[:-1].partition(".")
        whole = datetime.strptime(date, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        if len(fraction) > 9 or not fraction.isdigit() and fraction:
            raise ValueError(f"Invalid Docker fractional timestamp: {value}")
        return calendar.timegm(whole.utctimetuple()) * 1_000_000_000 + int(
            fraction.ljust(9, "0") or "0"
        )
    difference = parse(finished) - parse(started)
    if difference <= 0:
        raise ValueError("Docker finished time must follow start time")
    return difference


def roster() -> list[Path]:
    units = sorted(p for p in (SOURCE / "units").iterdir() if (p / "batch.json").exists())
    if len(units) != 6:
        raise RuntimeError(f"Expected six public batch units, found {len(units)}")
    return units


def stage_inputs(units: list[Path], out: Path) -> dict[str, dict[str, str]]:
    frozen: dict[str, dict[str, str]] = {}
    for unit in units:
        manifest = {row["path"]: row["sha256"] for row in json.loads(
            (unit / "manifest.json").read_text(encoding="utf-8")
        )["files"]}
        paths = sorted((unit / "scenarios").glob("*.json"))
        if not paths:
            raise RuntimeError(f"No sub-scenarios for {unit.name}")
        frozen[unit.name] = {}
        for source in paths:
            relative = source.relative_to(unit).as_posix()
            digest = sha256(source)
            if digest != manifest[relative]:
                raise RuntimeError(f"Manifest mismatch: {unit.name}/{relative}")
            destination = out / "inputs" / unit.name / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if sha256(destination) != digest:
                    raise RuntimeError(f"Staged input changed: {destination}")
            else:
                shutil.copyfile(source, destination)
            frozen[unit.name][relative] = digest
    return frozen


def schedule(units: list[Path], phase: str) -> list[tuple[str, str, int]]:
    rows: list[tuple[str, str, int]] = []
    if phase == "smoke":
        for unit in units:
            for variant in VARIANTS:
                rows.append((unit.name, variant, 0))
    elif phase == "timing":
        for unit in units:
            for repeat in range(1, 6):
                order = VARIANTS if repeat % 2 == 0 else tuple(reversed(VARIANTS))
                rows.extend((unit.name, variant, repeat) for variant in order)
    else:
        raise ValueError(phase)
    return rows


def key(unit: str, variant: str, repeat: int) -> str:
    return f"{unit}|{variant}|{repeat}"


def run_one(
    unit: Path, variant: str, repeat: int, image_id: str, out: Path, attempt: int,
    fingerprint: dict,
) -> dict:
    phase = "smoke" if repeat == 0 else "timing"
    dest = out / "attempts" / unit.name / variant / str(repeat) / str(attempt)
    dest.mkdir(parents=True, exist_ok=False)
    raw = dest / "raw"
    raw.mkdir()
    raw.chmod(0o777)
    accepted = dest / "accepted" / unit.name
    cidfile = dest / "container.cid"
    input_dir = out / "inputs" / unit.name
    verb = ["simulate-batch", "--batch-dir", "/input/scenarios", "--out-dir", "/output"]
    if variant in WORKERS:
        verb += ["--workers", str(WORKERS[variant])]
    name = f"t3-pb-{unit.name}-{variant}-{repeat}-{attempt}"
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
        image_id, *verb,
    ]
    row = {
        "key": key(unit.name, variant, repeat), "unit": unit.name, "variant": variant,
        "repeat": repeat, "phase": phase, "attempt": attempt, "command": cmd,
        "status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
    }
    write(dest / "attempt.json", row)
    try:
        # This helper imposes the hard deadline and samples whole-cgroup peak memory.
        proc, host_sec, gpu_sec, peak = timed_container_run(
            cmd, cidfile=cidfile, gpus=None, timeout_sec=300
        )
        row.update(
            exit_code=proc.returncode, host_observed_seconds=host_sec,
            host_gpu_seconds=gpu_sec, host_peak_memory_bytes=peak,
            stdout=proc.stdout[-5000:].decode(errors="replace"),
            stderr=proc.stderr[-5000:].decode(errors="replace"),
        )
        state = json.loads(subprocess.check_output(
            ["docker", "inspect", name], text=True
        ))[0]["State"]
        row["container_state"] = state
        if proc.returncode != 0 or state["OOMKilled"]:
            row.update(status="failed", failure_class="participant", rate=0.0)
            return row
        container_ns = docker_duration_ns(state["StartedAt"], state["FinishedAt"])
        container_sec = container_ns / 1_000_000_000
        row["container_seconds"] = container_sec
        if container_sec > 300:
            row.update(status="failed", failure_class="participant_timeout", rate=0.0)
            return row
        # Check the complete raw tree before the sanitizer copies accepted
        # files. The competition's 64 MiB limit is not bypassed by extras.
        row["raw_output_bytes"] = sum(
            path.lstat().st_size for path in raw.rglob("*") if path.is_file() or path.is_symlink()
        )
        if row["raw_output_bytes"] > 64 * 1024**2:
            row.update(status="failed", failure_class="participant_resource", rate=0.0)
            return row
        retain_output(raw, accepted, unit)
        files = {
            path.relative_to(accepted).as_posix(): {"sha256": sha256(path), "bytes": path.stat().st_size}
            for path in accepted.rglob("*") if path.is_file()
        }
        row["files"] = files
        row["output_bytes"] = sum(item["bytes"] for item in files.values())
        if row["output_bytes"] > 64 * 1024**2:
            row.update(status="failed", failure_class="participant_resource", rate=0.0)
            return row
        count = _host_n_events(accepted, True)
        if count != _reported_n_events(accepted, True):
            row.update(status="failed", failure_class="participant_schema", rate=0.0)
            return row
        row["event_count"] = count
        row["rate"] = count / container_sec
        metric = UnitRun(row["rate"], count, count, container_sec, gpu_sec, peak, 0)
        record = UnitRecord(
            unit=unit.name, verb="simulate-batch", image=image_id,
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
        row.update(
            status="failed", failure_class="infrastructure", error_type=type(exc).__name__,
            error=str(exc), traceback=traceback.format_exc(),
        )
        return row
    except Exception as exc:
        row.update(
            status="failed", failure_class="unclassified", error_type=type(exc).__name__,
            error=str(exc), traceback=traceback.format_exc(),
        )
        return row
    finally:
        try:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)
        except (subprocess.SubprocessError, OSError) as exc:
            row["cleanup_error"] = f"{type(exc).__name__}: {exc}"
        row["finished_at"] = datetime.now(timezone.utc).isoformat()
        write(dest / "attempt.json", row)


def summary(records: list[dict], units: list[Path]) -> dict:
    successful = {row["key"]: row for row in records if row["status"] == "passed"}
    participant_failures = [
        row for row in records
        if row["status"] == "failed" and row.get("failure_class", "").startswith("participant")
    ]
    result = {
        "smoke_passed": False, "timing_complete": False,
        "participant_failures_retained_at_zero": [row["key"] for row in participant_failures],
        "semantic_gate": False, "variants": {},
    }
    warm_keys = {key(unit.name, variant, 0) for unit in units for variant in VARIANTS}
    result["smoke_passed"] = warm_keys <= successful.keys()
    rates_by_variant: dict[str, dict[str, float] | None] = {}
    for variant in VARIANTS:
        rates: dict[str, float] = {}
        for unit in units:
            failed = any(
                row["unit"] == unit.name and row["variant"] == variant
                for row in participant_failures
            )
            if failed:
                rates[unit.name] = 0.0
                continue
            measured = [successful.get(key(unit.name, variant, repeat)) for repeat in range(1, 6)]
            if all(measured):
                rates[unit.name] = statistics.median(row["rate"] for row in measured)
        rates_by_variant[variant] = rates if len(rates) == len(units) else None
        result["variants"][variant] = {
            "resolved_unit_count": len(rates),
            "fixed_six_unit_denominator": len(units),
            "unit_median_rates": rates,
        }
    if any(rates is None for rates in rates_by_variant.values()):
        return result
    result["timing_complete"] = True
    result["semantic_gate"] = result["smoke_passed"] and not participant_failures
    baseline = rates_by_variant["baseline"]
    rng = random.Random(20260927)
    for variant in VARIANTS:
        rates = rates_by_variant[variant]
        per_unit = [rates[unit.name] - baseline[unit.name] for unit in units]
        bootstrap = sorted(
            statistics.mean(rng.choices(per_unit, k=len(units))) for _ in range(10000)
        )
        no_large_regression = all(
            rates[unit.name] >= 0.95 * baseline[unit.name] for unit in units
        )
        result["variants"][variant].update({
            "six_unit_mean_of_medians": statistics.mean(rates.values()),
            "difference_from_baseline": statistics.mean(per_unit),
            "paired_unit_bootstrap95_difference": [bootstrap[249], bootstrap[9749]],
            "no_unit_median_regression_over_5pct": no_large_regression,
            "exploratory_gate": variant != "baseline" and result["semantic_gate"]
                                and bootstrap[249] > 0 and no_large_regression,
        })
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("smoke", "timing", "all"), required=True)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-runs", type=int, help="bounded preflight; resume the same frozen plan")
    args = parser.parse_args()
    if args.max_runs is not None and args.max_runs < 1:
        parser.error("--max-runs must be positive")
    if sys.platform != "linux":
        parser.error("run this verifier in the existing Linux validation environment")
    out = args.out.resolve()
    verify_current_verifier_source()
    units = roster()
    fingerprint = node_fingerprint.collect().to_dict()
    images = {"baseline": BASE_ID}
    candidate = inspect_image(CANDIDATE_TAG)
    if inspect_image(BASE_ID) != BASE_ID:
        raise RuntimeError("Retained image ID changed")
    images.update({variant: candidate for variant in WORKERS})
    if args.resume:
        if not out.exists():
            raise RuntimeError("No experiment exists to resume")
    else:
        out.mkdir(parents=True, exist_ok=False)
    inputs = stage_inputs(units, out)
    plan = {
        "base_image": BASE_ID, "candidate_image": candidate,
        "upstream_source_ref": "1504b37b8472f7f949d4b8f80b7c8b2b71ab0e43",
        "current_rules_ref": "f910de231209ebbca060efee47fe2c41aabe4bce",
        "current_verifier_archive_sha256": VERIFIER_ARCHIVE_SHA256,
        "units": [unit.name for unit in units], "variants": list(VARIANTS),
        "node_fingerprint": fingerprint,
        "workers": WORKERS, "input_hashes": inputs,
        "warmup_per_variant_unit": 1, "measured_repeats_per_variant_unit": 5,
        "order": "per-unit measured rounds alternate baseline-first and baseline-last",
        "timer": "Docker State.StartedAt to State.FinishedAt; hard container deadline 300s",
        "quota": {"cpus": 4, "memory_bytes": 16 * 1024**3, "tmpfs_bytes": 64 * 1024**2},
        "selection": "positive paired six-unit rate difference bootstrap95 excludes zero; no unit median regression >5%; all gates",
        "worker_override_note": "Official invocation has no --workers. Counts 1 and 4 are diagnostic; selecting either requires a new immutable no-flag default image and fresh validation.",
        "rankable": False,
    }
    plan_path = out / "plan.json"
    if args.resume:
        if json.loads(plan_path.read_text(encoding="utf-8")) != plan:
            raise RuntimeError("Frozen experiment plan changed")
        records = json.loads((out / "records.json").read_text(encoding="utf-8"))
    else:
        write(plan_path, plan)
        records = []
        write(out / "records.json", records)
    by_name = {unit.name: unit for unit in units}
    phases = ("smoke", "timing") if args.phase == "all" else (args.phase,)
    runs_started = 0
    for phase in phases:
        if phase == "timing" and not summary(records, units)["smoke_passed"]:
            raise RuntimeError("All 24 semantic smoke/warm-up runs must pass before timing")
        for unit_name, variant, repeat in schedule(units, phase):
            item_key = key(unit_name, variant, repeat)
            prior_participant_failure = any(
                row["unit"] == unit_name and row["variant"] == variant
                and row["status"] == "failed"
                and row.get("failure_class", "").startswith("participant")
                for row in records
            )
            if prior_participant_failure:
                continue
            passed = next((row for row in records if row["key"] == item_key and row["status"] == "passed"), None)
            if passed:
                continue
            failures = [row for row in records if row["key"] == item_key and row["status"] == "failed"]
            if failures and failures[-1]["failure_class"] != "infrastructure":
                raise RuntimeError(f"Participant/unclassified failure must be resolved: {item_key}")
            attempt_root = out / "attempts" / unit_name / variant / str(repeat)
            existing_attempts = (
                [int(path.name) for path in attempt_root.iterdir() if path.is_dir() and path.name.isdigit()]
                if attempt_root.exists() else []
            )
            attempt = max([0, *existing_attempts]) + 1
            row = run_one(
                by_name[unit_name], variant, repeat, images[variant], out, attempt,
                fingerprint,
            )
            records.append(row)
            runs_started += 1
            write(out / "records.json", records)
            print(row["status"], item_key, row.get("rate"), flush=True)
            if row["status"] != "passed":
                if row.get("failure_class", "").startswith("participant"):
                    if args.max_runs is not None and runs_started >= args.max_runs:
                        write(out / "summary.json", summary(records, units))
                        return 0
                    continue  # Zero remains in the fixed six-unit denominator.
                raise RuntimeError(f"Run failed; retained attempt {row['failure_class']}: {item_key}")
            # Stable candidate bytes and event count must match its own warm-up.
            first = next(record for record in records if record["key"] == key(unit_name, variant, 0))
            if row["stable"] != first["stable"] or row["event_count"] != first["event_count"]:
                row.update(status="failed", failure_class="participant_repeat", rate=0.0)
                write(out / "records.json", records)
                attempt_file = (
                    out / "attempts" / unit_name / variant / str(repeat) / str(attempt) / "attempt.json"
                )
                write(attempt_file, row)
                if args.max_runs is not None and runs_started >= args.max_runs:
                    write(out / "summary.json", summary(records, units))
                    return 0
                continue  # The whole unit/variant scores zero; semantic gate fails.
            if args.max_runs is not None and runs_started >= args.max_runs:
                write(out / "summary.json", summary(records, units))
                return 0
        write(out / "summary.json", summary(records, units))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
