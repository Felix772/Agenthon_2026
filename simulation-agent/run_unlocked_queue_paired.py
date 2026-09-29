"""Predeclared full-roster paired rates for the T3 event-queue candidate.

Requires the candidate's 71/71 semantic gate first. Executes an alternating
incumbent/candidate pair at each scored unit; records Development self-reports
and Docker start-to-exit rates separately. No publication or upload.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import random
import statistics
import subprocess
import sys

import run_parallel_batch_experiment as screen
import run_t3_full_roster_parallel4 as full
import run_unlocked_queue_roster as semantic
from throughput import node_fingerprint


OUT = screen.ROOT / "project-evidence/t3-unlocked-queue-paired-20260928"
IMAGES = {"incumbent": semantic.PARENT, "unlocked": semantic.CANDIDATE}
VARIANTS = tuple(IMAGES)
REPEATS = (0, 1, 2)  # 0 is discarded warm-up; 1, 2 are measured


def _summary(records: list[dict], units: list[Path]) -> dict:
    passed = {(r["unit"], r["variant"], r["repeat"]): r for r in records if r["status"] == "passed"}
    result: dict = {"complete": len(passed) == 71 * 2 * len(REPEATS), "units": {}, "rankable": False}
    for unit in units:
        name = unit.name
        runs = {variant: [passed.get((name, variant, rep)) for rep in (1, 2)] for variant in VARIANTS}
        if not all(all(group) for group in runs.values()):
            continue
        row: dict = {"is_batch": full.is_batch(unit)}
        for variant, group in runs.items():
            row[f"{variant}_reported_rate"] = statistics.median(r["reported_rate"] for r in group)
            row[f"{variant}_container_rate"] = statistics.median(r["rate"] for r in group)
            row[f"{variant}_reported_samples"] = [r["reported_rate"] for r in group]
            row[f"{variant}_container_samples"] = [r["rate"] for r in group]
        row["reported_difference"] = row["unlocked_reported_rate"] - row["incumbent_reported_rate"]
        row["container_difference"] = row["unlocked_container_rate"] - row["incumbent_container_rate"]
        result["units"][name] = row
    if result["complete"]:
        for label, names in (
            ("all_71", [u.name for u in units]),
            ("single_65", [u.name for u in units if not full.is_batch(u)]),
            ("batch_6", [u.name for u in units if full.is_batch(u)]),
        ):
            subset = [result["units"][name] for name in names]
            values = {}
            for metric in ("reported", "container"):
                a = statistics.mean(r[f"incumbent_{metric}_rate"] for r in subset)
                b = statistics.mean(r[f"unlocked_{metric}_rate"] for r in subset)
                diffs = [r[f"{metric}_difference"] for r in subset]
                rng = random.Random(20260928 + len(subset))
                bootstrap = sorted(statistics.mean(rng.choices(diffs, k=len(subset))) for _ in range(10_000))
                values[metric] = {
                    "incumbent_mean_rate": a, "candidate_mean_rate": b,
                    "absolute_difference": b - a, "relative_difference": b / a - 1,
                    "paired_unit_bootstrap95_absolute_difference": [bootstrap[249], bootstrap[9749]],
                    "improved_units": sum(d > 0 for d in diffs),
                    "slowed_units": sum(d < 0 for d in diffs),
                }
            result[label] = values
    return result


def main() -> None:
    if sys.platform != "linux":
        raise RuntimeError("Run with the retained Linux T3 validation Python")
    screen.verify_current_verifier_source()
    if screen.inspect_image(semantic.CANDIDATE_TAG) != semantic.CANDIDATE or screen.inspect_image(semantic.PARENT) != semantic.PARENT:
        raise RuntimeError("Frozen image identity changed")
    semantic_summary = json.loads((semantic.OUT / "summary.json").read_text())
    if not semantic_summary["complete"] or not semantic_summary["all_candidate_stable_equal_control"]:
        raise RuntimeError("Full 71-unit semantic gate must pass before paired timing")
    controls = {row["unit"]: row for row in json.loads((semantic.OUT / "records.json").read_text())}
    units = full.roster()
    OUT.mkdir(parents=True, exist_ok=True)
    input_hashes = full.stage_inputs(units, OUT)
    fingerprint = node_fingerprint.collect().to_dict()
    plan = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "incumbent": semantic.PARENT, "candidate": semantic.CANDIDATE,
        "rules_ref": "f910de231209ebbca060efee47fe2c41aabe4bce",
        "verifier_archive_sha256": screen.VERIFIER_ARCHIVE_SHA256,
        "units": [u.name for u in units], "input_hashes": input_hashes,
        "fingerprint": fingerprint,
        "co_residents_at_start": subprocess.check_output(
            ["docker", "ps", "--format", "{{.Names}} {{.Status}} {{.Image}}"], text=True
        ).splitlines(),
        "schedule": "per unit: fresh warm-up per image, then 2 measured pairs; first pair incumbent-first, second candidate-first",
        "score": "arithmetic mean of 71 per-unit median absolute events/sec rates, split 65 singles / 6 batches",
        "rate_sources": "reported events_per_sec models online Development; Docker timing is local Final proxy only",
        "verification": "current public developer verifier plus exact stable parquet SHA and event count equality to 71-unit semantic control on every run",
        "resource": "4 CPU/16GiB/no-swap, UID65534, read-only/no-network, 64MiB tmpfs, 300s hard timeout",
        "rankable": False,
    }
    plan_path = OUT / "plan.json"
    if plan_path.exists():
        old = json.loads(plan_path.read_text())
        for key in ("incumbent", "candidate", "rules_ref", "verifier_archive_sha256", "units", "input_hashes", "schedule", "score", "rate_sources", "verification", "resource", "rankable"):
            if old[key] != plan[key]:
                raise RuntimeError(f"Frozen paired plan changed: {key}")
        for key in semantic.FINGERPRINT_FIELDS:
            if old["fingerprint"][key] != fingerprint[key]:
                raise RuntimeError(f"Node fingerprint changed: {key}")
    else:
        screen.write(plan_path, plan)
    records_path = OUT / "records.json"
    records = json.loads(records_path.read_text()) if records_path.exists() else []
    for unit in units:
        for repeat in REPEATS:
            order = VARIANTS if repeat != 2 else tuple(reversed(VARIANTS))
            for variant in order:
                key = screen.key(unit.name, variant, repeat)
                if any(r["key"] == key and r["status"] == "passed" for r in records):
                    continue
                if any(r["key"] == key for r in records):
                    raise RuntimeError(f"Failed run needs diagnosis: {key}")
                run = screen.run_one if full.is_batch(unit) else full.run_single
                row = run(unit, variant, repeat, IMAGES[variant], OUT, 1, fingerprint)
                if row["status"] == "passed":
                    ctrl = controls[unit.name]
                    if row["stable"] != ctrl["stable"] or row["event_count"] != ctrl["event_count"]:
                        row["status"] = "failed"
                        row["failure_class"] = "participant_semantic_difference"
                    else:
                        accepted = OUT / "attempts" / unit.name / variant / str(repeat) / "1" / "accepted" / unit.name
                        events = accepted / ("batch_events.json" if full.is_batch(unit) else "events.json")
                        row["reported_rate"] = float(json.loads(events.read_text())["events_per_sec"])
                records.append(row)
                screen.write(records_path, records)
                screen.write(OUT / "summary.json", _summary(records, units))
                print(row["status"], key, row.get("reported_rate"), row.get("rate"), flush=True)
                if row["status"] != "passed":
                    raise RuntimeError(f"Paired timing stopped: {key}: {row.get('failure_class')}")
    screen.write(OUT / "node-fingerprint-after.json", node_fingerprint.collect().to_dict())
    screen.write(OUT / "co-residents-after.json", subprocess.check_output(
        ["docker", "ps", "--format", "{{.Names}} {{.Status}} {{.Image}}"], text=True
    ).splitlines())


if __name__ == "__main__":
    main()
