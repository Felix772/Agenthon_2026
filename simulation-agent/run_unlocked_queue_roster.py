"""Resume-safe 71-unit semantic gate for the T3 unlocked event queue.

Only the candidate runs. Existing selected-image warm-up outputs supply exact
stable-hash and event-count controls; the current public verifier independently
checks each candidate. No reference trace enters the participant container.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import run_parallel_batch_experiment as screen
import run_t3_full_roster_parallel4 as full
from throughput import node_fingerprint


OUT = screen.ROOT / "project-evidence/t3-unlocked-queue-full-roster-20260928"
CONTROL = screen.ROOT / "project-evidence/t3-parallel-batch4-full-roster-20260928/records.json"
PARENT = "sha256:df5e1b9d96abf06947a33cf499c3b4feed271fa31f6439de8ce49ff98a834489"
CANDIDATE_TAG = "simulation-agent:unlocked-queue-dev-20260928"
CANDIDATE = "sha256:94bda24a509530e2bf2dfb0f079a0c052b29422c87bc62163aac1701dfe194bc"
FINGERPRINT_FIELDS = ("cpu_model", "cpu_count", "memory_bytes", "gpu_name", "gpu_count")


def _control() -> dict[str, dict]:
    rows = json.loads(CONTROL.read_text())
    reference = {
        row["unit"]: row for row in rows
        if row["variant"] == "selected-default4" and row["repeat"] == 0 and row["status"] == "passed"
    }
    if len(reference) != 71:
        raise RuntimeError("Retained selected-image 71-unit warm-up is incomplete")
    return reference


def _summary(rows: list[dict], roster: list[Path]) -> dict:
    passed = {r["unit"]: r for r in rows if r["status"] == "passed"}
    batches = {u.name for u in roster if full.is_batch(u)}
    return {
        "complete": len(passed) == 71 and not any(r["status"] != "passed" for r in rows),
        "passed": len(passed), "singles_passed": len(passed.keys() - batches),
        "batches_passed": len(passed.keys() & batches),
        "failed": [{"unit": r["unit"], "class": r.get("failure_class")} for r in rows if r["status"] != "passed"],
        "all_candidate_stable_equal_control": all(r.get("control_match") for r in passed.values()),
        "reported_rates": {u: r.get("reported_rate") for u, r in passed.items()},
        "rankable": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-runs", type=int, default=None)
    args = parser.parse_args()
    if args.max_runs is not None and args.max_runs < 1:
        parser.error("--max-runs must be positive")
    if sys.platform != "linux":
        parser.error("Run with the retained Linux T3 validation Python")
    screen.verify_current_verifier_source()
    if screen.inspect_image(CANDIDATE_TAG) != CANDIDATE or screen.inspect_image(PARENT) != PARENT:
        raise RuntimeError("Candidate or parent image identity changed")
    roster = full.roster()
    reference = _control()
    if set(reference) != {unit.name for unit in roster}:
        raise RuntimeError("Retained control roster differs from current 71 units")
    OUT.mkdir(parents=True, exist_ok=True)
    input_hashes = full.stage_inputs(roster, OUT)
    fingerprint = node_fingerprint.collect().to_dict()
    plan = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "parent": PARENT, "candidate": CANDIDATE,
        "rules_ref": "f910de231209ebbca060efee47fe2c41aabe4bce",
        "unit_tree_unchanged_from_source_ref": "1504b37b8472f7f949d4b8f80b7c8b2b71ab0e43",
        "verifier_archive_sha256": screen.VERIFIER_ARCHIVE_SHA256,
        "units": [u.name for u in roster], "input_hashes": input_hashes,
        "control_record": str(CONTROL.relative_to(screen.ROOT)),
        "fingerprint": fingerprint,
        "co_residents_at_start": subprocess.check_output(
            ["docker", "ps", "--format", "{{.Names}} {{.Status}} {{.Image}}"], text=True
        ).splitlines(),
        "gate": "71/71 current developer-verifier admissions, exact stable parquet SHA/event count equality to retained selected-image warm-up, 300s hard limit, full strict Development resource settings",
        "timing": "observed in this run but not paired; do not infer 71-unit speedup",
        "rankable": False,
    }
    plan_path = OUT / "plan.json"
    if plan_path.exists():
        old = json.loads(plan_path.read_text())
        for key in ("parent", "candidate", "rules_ref", "unit_tree_unchanged_from_source_ref", "verifier_archive_sha256", "units", "input_hashes", "control_record", "gate", "timing", "rankable"):
            if old[key] != plan[key]:
                raise RuntimeError(f"Frozen full-roster plan changed: {key}")
        for key in FINGERPRINT_FIELDS:
            if old["fingerprint"][key] != fingerprint[key]:
                raise RuntimeError(f"Node fingerprint changed: {key}")
    else:
        screen.write(plan_path, plan)
    records_path = OUT / "records.json"
    records = json.loads(records_path.read_text()) if records_path.exists() else []
    started = 0
    for unit in roster:
        if any(row["unit"] == unit.name and row["status"] == "passed" for row in records):
            continue
        if any(row["unit"] == unit.name for row in records):
            raise RuntimeError(f"Failed attempt requires diagnosis: {unit.name}")
        run = screen.run_one if full.is_batch(unit) else full.run_single
        row = run(unit, "unlocked", 0, CANDIDATE, OUT, 1, fingerprint)
        if row["status"] == "passed":
            ctrl = reference[unit.name]
            row["control_match"] = row["stable"] == ctrl["stable"] and row["event_count"] == ctrl["event_count"]
            if not row["control_match"]:
                row["status"] = "failed"
                row["failure_class"] = "participant_semantic_difference"
            accepted = OUT / "attempts" / unit.name / "unlocked" / "0" / "1" / "accepted" / unit.name
            events = accepted / ("batch_events.json" if full.is_batch(unit) else "events.json")
            row["reported_rate"] = float(json.loads(events.read_text())["events_per_sec"])
        records.append(row)
        screen.write(records_path, records)
        screen.write(OUT / "summary.json", _summary(records, roster))
        print(row["status"], unit.name, row.get("reported_rate"), flush=True)
        started += 1
        if row["status"] != "passed":
            raise RuntimeError(f"71-unit gate failed: {unit.name}: {row.get('failure_class')}")
        if args.max_runs is not None and started >= args.max_runs:
            return
    screen.write(OUT / "node-fingerprint-after.json", node_fingerprint.collect().to_dict())
    screen.write(OUT / "co-residents-after.json", subprocess.check_output(
        ["docker", "ps", "--format", "{{.Names}} {{.Status}} {{.Image}}"], text=True
    ).splitlines())


if __name__ == "__main__":
    main()
