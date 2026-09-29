"""Frozen 71-unit paired timing of unlocked queue versus scalar latency clip.

Run only after the scalar candidate's complete 71-unit semantic gate. Each
unit gets one warm-up per image and two measured pairs with reversed order.
This is a local, nonrankable Development/Final-proxy experiment; it neither
builds nor publishes an image. Re-running resumes only an intact prefix of
successful runs and never silently retries a failed or interrupted attempt.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import subprocess
import sys

import run_parallel_batch_experiment as screen
import run_t3_full_roster_parallel4 as full
import run_unlocked_queue_roster as queue_roster
from qfbench2_track_simulation.scoring import cluster_key
from throughput import node_fingerprint


OUT = screen.ROOT / "project-evidence/t3-scalar-latency-paired-20260928"
SEMANTIC_OUT = screen.ROOT / "project-evidence/t3-scalar-latency-semantic-20260928"
UNLOCKED_OUT = queue_roster.OUT
UNLOCKED_TAG = queue_roster.CANDIDATE_TAG
UNLOCKED_ID = "sha256:94bda24a509530e2bf2dfb0f079a0c052b29422c87bc62163aac1701dfe194bc"
SCALAR_TAG = "simulation-agent:unlocked-queue-scalar-dev-20260928"
SCALAR_ID = "sha256:a12731a118178e10469608ec92172bc7db4b799a2b4ad8cd2b1acf939dd053c9"
IMAGES = {"unlocked": UNLOCKED_ID, "scalar": SCALAR_ID}
VARIANTS = ("unlocked", "scalar")
REPEATS = (0, 1, 2)  # 0 is a discarded warm-up; 1 and 2 are measured.
EXPECTED_ROSTER_INPUT_SHA256 = "daed56d432146c2c985872848ec4809bde1e0e062db489bbe3250c9c97508bb0"
EXPECTED_CARD_SHA256 = "a3184d90b0e0bda0a8bbd0c669e542c40e9296b4bda3db530af9a3f8eb634943"
RULE_REFS = {
    "Agenthon2026-public": "95a0de3d9a814f3883c151b7efdbbcf579139244",
    "track1-coding-public": "1a60fc48024c4f0e9978416d84e8370dbb0236cd",
    "track2-forecasting-public": "28a6cae9674f69e63a07a19165a9217e85eacfff",
    "track3-simulation-public": "f910de231209ebbca060efee47fe2c41aabe4bce",
    "track4-analysis-public": "7b2bce1d80d96f5d5667d7f67bfaa945fa5d1491",
}
FINGERPRINT_FIELDS = queue_roster.FINGERPRINT_FIELDS
EXPECTED_RUNS = 71 * len(VARIANTS) * len(REPEATS)
BOOTSTRAPS = 20_000
INTERRUPTED_KEY = "t3-gb-horizon-240s|unlocked|0"
INTERRUPTED_PREFIX_LENGTH = 150


def _roster_input_hash(units: list[str], input_hashes: dict) -> str:
    payload = {"units": units, "input_hashes": input_hashes}
    packed = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(packed).hexdigest()


def _mapping_hash(mapping: dict) -> str:
    packed = json.dumps(mapping, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(packed).hexdigest()


def _scheduled(units: list[Path]) -> list[dict]:
    return [
        {"unit": unit.name, "variant": variant, "repeat": repeat,
         "key": screen.key(unit.name, variant, repeat)}
        for unit in units for repeat in REPEATS
        for variant in (VARIANTS if repeat != 2 else tuple(reversed(VARIANTS)))
    ]


def _read_semantic_controls(units: list[Path]) -> tuple[dict, dict, str]:
    semantic_plan_path = SEMANTIC_OUT / "plan.json"
    semantic_records_path = SEMANTIC_OUT / "records.json"
    semantic_plan = json.loads(semantic_plan_path.read_text())
    semantic_summary = json.loads((SEMANTIC_OUT / "summary.json").read_text())
    semantic_rows = json.loads(semantic_records_path.read_text())
    unlocked_summary = json.loads((UNLOCKED_OUT / "summary.json").read_text())
    unlocked_rows = json.loads((UNLOCKED_OUT / "records.json").read_text())
    names = [unit.name for unit in units]
    if (
        semantic_plan["parent"] != UNLOCKED_ID
        or semantic_plan["candidate"] != SCALAR_ID
        or semantic_plan["rules_ref"] != RULE_REFS["track3-simulation-public"]
        or semantic_plan["verifier_archive_sha256"] != screen.VERIFIER_ARCHIVE_SHA256
        or semantic_plan["units"] != names
        or _roster_input_hash(names, semantic_plan["input_hashes"]) != EXPECTED_ROSTER_INPUT_SHA256
    ):
        raise RuntimeError("Scalar semantic plan differs from the frozen image, rules, or 71-unit inputs")
    if not semantic_summary["complete"] or not semantic_summary["all_candidate_stable_equal_control"]:
        raise RuntimeError("The scalar candidate must pass its entire 71-unit semantic gate first")
    if not unlocked_summary["complete"] or not unlocked_summary["all_candidate_stable_equal_control"]:
        raise RuntimeError("The frozen unlocked-queue 71-unit semantic gate is incomplete")
    scalar = {row["unit"]: row for row in semantic_rows if row["status"] == "passed"}
    unlocked = {
        row["unit"]: row for row in unlocked_rows
        if row["variant"] == "unlocked" and row["repeat"] == 0 and row["status"] == "passed"
    }
    if (
        len(semantic_rows) != 71 or len(unlocked_rows) != 71
        or len(scalar) != 71 or len(unlocked) != 71
        or set(scalar) != set(names) or set(unlocked) != set(names)
    ):
        raise RuntimeError("Semantic controls do not cover exactly the frozen 71-unit roster")
    for name in names:
        row = scalar[name]
        control = unlocked[name]
        if (
            row["variant"] != "scalar" or row["repeat"] != 0
            or not row.get("control_match")
            or row["stable"] != control["stable"]
            or row["event_count"] != control["event_count"]
        ):
            raise RuntimeError(f"Semantic control mismatch: {name}")
    return unlocked, semantic_plan, screen.sha256(semantic_records_path)


def _interval(values: list[float], families: list[str] | None, seed: int) -> list[float] | None:
    if families is not None and len(set(families)) < 2:
        return None
    rng = random.Random(seed)
    draws = []
    if families is None:
        for _ in range(BOOTSTRAPS):
            draws.append(statistics.mean(rng.choices(values, k=len(values))))
    else:
        grouped: dict[str, list[float]] = defaultdict(list)
        for family, difference in zip(families, values, strict=True):
            grouped[family].append(difference)
        blocks = list(grouped.values())
        for _ in range(BOOTSTRAPS):
            sample = [difference for block in rng.choices(blocks, k=len(blocks)) for difference in block]
            draws.append(statistics.mean(sample))
    draws.sort()
    return [draws[499], draws[19_499]]


def _metrics(names: list[str], rows: dict[str, dict], families: dict[str, str]) -> dict:
    result = {"count": len(names)}
    for metric in ("reported", "container"):
        unlocked = [rows[name][f"unlocked_{metric}_rate"] for name in names]
        scalar = [rows[name][f"scalar_{metric}_rate"] for name in names]
        differences = [b - a for a, b in zip(unlocked, scalar, strict=True)]
        base_mean = statistics.mean(unlocked)
        candidate_mean = statistics.mean(scalar)
        result[metric] = {
            "unlocked_mean_rate": base_mean,
            "scalar_mean_rate": candidate_mean,
            "absolute_difference": candidate_mean - base_mean,
            "relative_difference": candidate_mean / base_mean - 1,
            "improved_units": sum(value > 0 for value in differences),
            "slowed_units": sum(value < 0 for value in differences),
            "unit_bootstrap95_difference": _interval(differences, None, 20260929 + len(names)),
            "family_block_bootstrap95_difference": _interval(
                differences, [families[name] for name in names], 20260930 + len(names)
            ),
        }
    return result


def _summary(records: list[dict], units: list[Path], families: dict[str, str]) -> dict:
    passed = {(row["unit"], row["variant"], row["repeat"]): row for row in records
              if row["status"] == "passed"}
    result: dict = {
        "complete": len(passed) == EXPECTED_RUNS and len(records) == EXPECTED_RUNS,
        "passed_runs": len(passed), "expected_runs": EXPECTED_RUNS,
        "units": {}, "rankable": False,
        "interval_note": "Resampling families is sensitivity analysis, not uncertainty of the fixed scored roster",
    }
    for unit in units:
        name = unit.name
        groups = {variant: [passed.get((name, variant, repeat)) for repeat in (1, 2)]
                  for variant in VARIANTS}
        if not all(all(group) for group in groups.values()):
            continue
        row: dict = {"is_batch": full.is_batch(unit), "scenario_family": families[name]}
        for variant, group in groups.items():
            reported = [sample["reported_rate"] for sample in group]
            container = [sample["rate"] for sample in group]
            row[f"{variant}_reported_rate"] = statistics.median(reported)
            row[f"{variant}_container_rate"] = statistics.median(container)
            row[f"{variant}_reported_samples"] = reported
            row[f"{variant}_container_samples"] = container
        for metric in ("reported", "container"):
            row[f"{metric}_difference"] = row[f"scalar_{metric}_rate"] - row[f"unlocked_{metric}_rate"]
            row[f"paired_{metric}_differences"] = [
                passed[(name, "scalar", repeat)]["reported_rate" if metric == "reported" else "rate"]
                - passed[(name, "unlocked", repeat)]["reported_rate" if metric == "reported" else "rate"]
                for repeat in (1, 2)
            ]
        result["units"][name] = row
    if result["complete"]:
        names = [unit.name for unit in units]
        splits = {
            "all_71": names,
            "single_65": [unit.name for unit in units if not full.is_batch(unit)],
            "batch_6": [unit.name for unit in units if full.is_batch(unit)],
        }
        if len(splits["single_65"]) != 65 or len(splits["batch_6"]) != 6:
            raise RuntimeError("Frozen 65/6 roster changed")
        for label, subset in splits.items():
            result[label] = _metrics(subset, result["units"], families)
        result["family_mean_differences"] = {
            family: _metrics([name for name in names if families[name] == family], result["units"], families)
            for family in sorted(set(families.values()))
        }
        result["leave_one_family_out_all_71"] = {
            family: _metrics([name for name in names if families[name] != family], result["units"], families)
            for family in sorted(set(families.values()))
        }
    return result


def _confirm_interrupted_warmup(attempt: Path, item: dict) -> None:
    """Authorize one fresh attempt while leaving the Docker-outage evidence intact."""
    if item["key"] != INTERRUPTED_KEY:
        raise RuntimeError("Only the diagnosed interrupted warm-up may use attempt 2")
    row = json.loads((attempt / "attempt.json").read_text())
    if any(row.get(field) != value for field, value in (
        ("key", INTERRUPTED_KEY), ("unit", item["unit"]),
        ("variant", item["variant"]), ("repeat", item["repeat"]),
        ("phase", "warmup"), ("attempt", 1), ("status", "running"),
    )):
        raise RuntimeError("Interrupted warm-up record differs from the diagnosed attempt")
    raw = attempt / "raw"
    if not raw.is_dir() or any(raw.iterdir()):
        raise RuntimeError("Interrupted warm-up output is not empty; inspect it before retrying")
    cid = (attempt / "container.cid").read_text().strip()
    if len(cid) != 64 or any(char not in "0123456789abcdef" for char in cid):
        raise RuntimeError("Interrupted warm-up container ID is invalid")
    container = json.loads(subprocess.check_output(["docker", "inspect", cid], text=True))[0]
    expected_name = f"/t3-p4-{item['unit']}-{item['variant']}-{item['repeat']}-1"
    state = container["State"]
    if (container["Id"] != cid or container["Name"] != expected_name
            or state["Status"] != "exited" or state["ExitCode"] != 255
            or state["Running"]):
        raise RuntimeError("Interrupted warm-up container does not match the Docker outage")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-runs", type=int, help="stop after this many new successful runs; rerun to resume")
    parser.add_argument(
        "--recover-interrupted-151", action="store_true",
        help="use attempt 2 for the diagnosed Docker-outage warm-up after 150 passed runs",
    )
    args = parser.parse_args()
    if args.max_runs is not None and args.max_runs < 1:
        parser.error("--max-runs must be positive")
    if sys.platform != "linux":
        parser.error("Use the retained Linux T3 validation Python")
    screen.verify_current_verifier_source()
    if (
        screen.inspect_image(UNLOCKED_TAG) != UNLOCKED_ID
        or screen.inspect_image(UNLOCKED_ID) != UNLOCKED_ID
        or screen.inspect_image(SCALAR_TAG) != SCALAR_ID
        or screen.inspect_image(SCALAR_ID) != SCALAR_ID
    ):
        raise RuntimeError("A frozen Docker image identity changed")
    units = full.roster()
    names = [unit.name for unit in units]
    controls, semantic_plan, semantic_records_sha256 = _read_semantic_controls(units)
    families = {unit.name: cluster_key(unit) for unit in units}
    if any(not family for family in families.values()):
        raise RuntimeError("The official scorer did not return a family for every unit")
    current_fingerprint = node_fingerprint.collect().to_dict()
    for field in FINGERPRINT_FIELDS:
        if current_fingerprint[field] != semantic_plan["fingerprint"][field]:
            raise RuntimeError(f"Validation node changed since semantic gate: {field}")
    OUT.mkdir(parents=True, exist_ok=True)
    input_hashes = full.stage_inputs(units, OUT)
    if input_hashes != semantic_plan["input_hashes"] or (
        _roster_input_hash(names, input_hashes) != EXPECTED_ROSTER_INPUT_SHA256
    ):
        raise RuntimeError("Staged paired inputs differ from the frozen semantic gate")
    card_hashes = {unit.name: screen.sha256(unit / "card.toml") for unit in units}
    if _mapping_hash(card_hashes) != EXPECTED_CARD_SHA256:
        raise RuntimeError("Public card content changed from the predeclared verifier roster")
    schedule = _scheduled(units)
    plan = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "images": IMAGES, "rule_refs": RULE_REFS,
        "verifier_archive_sha256": screen.VERIFIER_ARCHIVE_SHA256,
        "semantic_gate_records_sha256": semantic_records_sha256,
        "roster_input_sha256": EXPECTED_ROSTER_INPUT_SHA256,
        "card_sha256": EXPECTED_CARD_SHA256,
        "units": names, "input_hashes": input_hashes,
        "card_hashes": card_hashes,
        "scenario_families": families, "schedule": schedule,
        "fingerprint": current_fingerprint,
        "co_residents_at_start": subprocess.check_output(
            ["docker", "ps", "--format", "{{.Names}} {{.Status}} {{.Image}}"], text=True
        ).splitlines(),
        "resource": "4 CPU/16GiB/no-swap, UID65534, read-only/no-network, 64MiB tmpfs, 300s hard limit",
        "verification": "current public developer verifier plus exact stable parquet SHA/event count equality to unlocked 71-unit semantic control on every run",
        "timing": "one warm-up per image/unit, two alternating measured pairs; median absolute rate per unit; arithmetic mean across all 71 (65 singles/6 batches)",
        "rate_sources": "self-reported events_per_sec is the Development proxy; Docker State.StartedAt-to-FinishedAt is a local Final timing proxy",
        "bootstrap": "20,000 deterministic resamples of paired unit-rate differences and official scenario_family blocks; diagnostic only",
        "rankable": False,
    }
    plan_path = OUT / "plan.json"
    frozen_fields = (
        "images", "rule_refs", "verifier_archive_sha256", "semantic_gate_records_sha256",
        "roster_input_sha256", "card_sha256", "units", "input_hashes", "card_hashes", "scenario_families",
        "schedule", "resource", "verification", "timing", "rate_sources", "bootstrap", "rankable",
    )
    if plan_path.exists():
        old = json.loads(plan_path.read_text())
        for field in frozen_fields:
            if old[field] != plan[field]:
                raise RuntimeError(f"Frozen paired plan changed: {field}")
        for field in FINGERPRINT_FIELDS:
            if old["fingerprint"][field] != current_fingerprint[field]:
                raise RuntimeError(f"Validation node changed during paired timing: {field}")
    else:
        screen.write(plan_path, plan)
    records_path = OUT / "records.json"
    records = json.loads(records_path.read_text()) if records_path.exists() else []
    actual_keys = [row["key"] for row in records]
    scheduled_keys = [row["key"] for row in schedule]
    if actual_keys != scheduled_keys[:len(records)] or any(row["status"] != "passed" for row in records):
        raise RuntimeError("Prior records are not an intact successful schedule prefix; diagnose before resuming")
    if args.recover_interrupted_151 and (
        len(records) != INTERRUPTED_PREFIX_LENGTH
        or schedule[len(records)]["key"] != INTERRUPTED_KEY
    ):
        raise RuntimeError("Interruption recovery requires exactly the frozen 150-run prefix")
    started = 0
    for item in schedule[len(records):]:
        unit = next(unit for unit in units if unit.name == item["unit"])
        variant, repeat = item["variant"], item["repeat"]
        attempt_number = 1
        attempt = OUT / "attempts" / unit.name / variant / str(repeat) / str(attempt_number)
        if args.recover_interrupted_151 and item["key"] == INTERRUPTED_KEY and not attempt.is_dir():
            raise RuntimeError(f"Diagnosed interrupted attempt is missing: {attempt}")
        if attempt.exists():
            if not args.recover_interrupted_151 or item["key"] != INTERRUPTED_KEY:
                raise RuntimeError(f"Interrupted attempt exists; diagnose it before resuming: {attempt}")
            _confirm_interrupted_warmup(attempt, item)
            attempt_number = 2
            attempt = attempt.with_name(str(attempt_number))
            if attempt.exists():
                raise RuntimeError(f"Recovery attempt already exists: {attempt}")
        run = screen.run_one if full.is_batch(unit) else full.run_single
        row = run(unit, variant, repeat, IMAGES[variant], OUT, attempt_number, current_fingerprint)
        if row["status"] == "passed":
            control = controls[unit.name]
            if row["stable"] != control["stable"] or row["event_count"] != control["event_count"]:
                row["status"] = "failed"
                row["failure_class"] = "participant_semantic_difference"
            else:
                accepted = attempt / "accepted" / unit.name
                events = accepted / ("batch_events.json" if full.is_batch(unit) else "events.json")
                rate = float(json.loads(events.read_text())["events_per_sec"])
                if not math.isfinite(rate) or rate <= 0:
                    row["status"] = "failed"
                    row["failure_class"] = "participant_reported_rate_invalid"
                else:
                    row["reported_rate"] = rate
        records.append(row)
        screen.write(records_path, records)
        screen.write(OUT / "summary.json", _summary(records, units, families))
        print(row["status"], item["key"], row.get("reported_rate"), row.get("rate"), flush=True)
        if row["status"] != "passed":
            raise RuntimeError(f"Paired timing stopped: {item['key']}: {row.get('failure_class')}")
        started += 1
        if args.max_runs is not None and started >= args.max_runs and len(records) < EXPECTED_RUNS:
            return
    screen.write(OUT / "node-fingerprint-after.json", node_fingerprint.collect().to_dict())
    screen.write(OUT / "co-residents-after.json", subprocess.check_output(
        ["docker", "ps", "--format", "{{.Names}} {{.Status}} {{.Image}}"], text=True
    ).splitlines())


if __name__ == "__main__":
    main()
