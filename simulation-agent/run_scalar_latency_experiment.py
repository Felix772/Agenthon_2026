"""Deferred T3 experiment: paired four-unit screen, then 71-unit semantic gate.

Compare the experimental scalar-clamp derivative with its frozen unlocked-queue
parent. Both stages use the current public verifier and the retained strict
container launcher. This script does not build images or upload submissions.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import subprocess
import sys

import run_parallel_batch_experiment as runner
import run_t3_full_roster_parallel4 as roster_runner
import run_unlocked_queue_roster as queue_roster
from throughput import node_fingerprint


PARENT_TAG = queue_roster.CANDIDATE_TAG
PARENT_ID = queue_roster.CANDIDATE
CANDIDATE_TAG = "simulation-agent:unlocked-queue-scalar-dev-20260928"
RULES_REF = "f910de231209ebbca060efee47fe2c41aabe4bce"
OUT_SCREEN = runner.ROOT / "project-evidence/t3-scalar-latency-screen-20260928"
OUT_SEMANTIC = runner.ROOT / "project-evidence/t3-scalar-latency-semantic-20260928"
UNITS = (
    "t3-eq-deterministic-baseline",
    "t3-as01-base-mix",
    "t3-gb-pop-128-agents",
    "t3-gbatch-homog-4",
)
VARIANTS = ("unlocked", "scalar")
FINGERPRINT_FIELDS = queue_roster.FINGERPRINT_FIELDS


def _controls() -> dict[str, dict]:
    summary = json.loads((queue_roster.OUT / "summary.json").read_text())
    if not summary["complete"] or not summary["all_candidate_stable_equal_control"]:
        raise RuntimeError("Frozen unlocked-queue 71-unit semantic gate is incomplete")
    rows = json.loads((queue_roster.OUT / "records.json").read_text())
    controls = {
        row["unit"]: row for row in rows
        if row["variant"] == "unlocked" and row["repeat"] == 0 and row["status"] == "passed"
    }
    if len(controls) != 71:
        raise RuntimeError("Expected 71 distinct unlocked-queue control units")
    return controls


def _screen_summary(rows: list[dict]) -> dict:
    passed = {
        (r["unit"], r["variant"], r["repeat"]): r
        for r in rows if r["status"] == "passed"
    }
    result: dict = {
        "complete": len(passed) == len(UNITS) * len(VARIANTS) * 3
        and not any(r["status"] != "passed" for r in rows),
        "all_semantics_equal_control": all(r.get("control_match") for r in passed.values()),
        "units": {}, "rankable": False,
    }
    for unit in UNITS:
        groups = {
            variant: [passed.get((unit, variant, repeat)) for repeat in (1, 2)]
            for variant in VARIANTS
        }
        if not all(all(group) for group in groups.values()):
            continue
        row: dict = {}
        for variant, group in groups.items():
            row[f"{variant}_reported_median"] = statistics.median(r["reported_rate"] for r in group)
            row[f"{variant}_container_median"] = statistics.median(r["rate"] for r in group)
        row["reported_difference"] = row["scalar_reported_median"] - row["unlocked_reported_median"]
        row["container_difference"] = row["scalar_container_median"] - row["unlocked_container_median"]
        result["units"][unit] = row
    if result["complete"]:
        for metric in ("reported", "container"):
            parent = statistics.mean(r[f"unlocked_{metric}_median"] for r in result["units"].values())
            candidate = statistics.mean(r[f"scalar_{metric}_median"] for r in result["units"].values())
            result[f"equal_unit_{metric}_mean_difference"] = candidate - parent
            result[f"equal_unit_{metric}_mean_ratio"] = candidate / parent
    return result


def _semantic_summary(rows: list[dict], roster: list[Path]) -> dict:
    passed = {r["unit"]: r for r in rows if r["status"] == "passed"}
    batches = {u.name for u in roster if roster_runner.is_batch(u)}
    return {
        "complete": len(passed) == 71 and not any(r["status"] != "passed" for r in rows),
        "passed": len(passed),
        "singles_passed": len(passed.keys() - batches),
        "batches_passed": len(passed.keys() & batches),
        "all_candidate_stable_equal_control": all(r.get("control_match") for r in passed.values()),
        "failed": [{"unit": r["unit"], "class": r.get("failure_class")}
                   for r in rows if r["status"] != "passed"],
        "rankable": False,
    }


def _freeze_plan(out: Path, units: list[Path], candidate_id: str, stage: str) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    input_hashes = roster_runner.stage_inputs(units, out)
    fingerprint = node_fingerprint.collect().to_dict()
    plan = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "stage": stage, "parent": PARENT_ID, "candidate": candidate_id,
        "rules_ref": RULES_REF,
        "verifier_archive_sha256": runner.VERIFIER_ARCHIVE_SHA256,
        "units": [u.name for u in units], "input_hashes": input_hashes,
        "fingerprint": fingerprint,
        "co_residents_at_start": subprocess.check_output(
            ["docker", "ps", "--format", "{{.Names}} {{.Status}} {{.Image}}"], text=True
        ).splitlines(),
        "resource": "4 CPU/16GiB/no-swap, UID65534, read-only/no-network, 64MiB tmpfs, 300s hard timeout",
        "verification": "current public developer verifier plus exact stable parquet SHA/event count equality to frozen unlocked-queue control",
        "rankable": False,
    }
    if stage == "screen":
        plan["schedule"] = "one warm-up per image/unit; two alternating paired measured repeats"
    else:
        plan["schedule"] = "one candidate run per 71 scored units, after successful four-unit paired screen"
    path = out / "plan.json"
    if path.exists():
        old = json.loads(path.read_text())
        for key in ("stage", "parent", "candidate", "rules_ref", "verifier_archive_sha256",
                    "units", "input_hashes", "resource", "verification", "schedule", "rankable"):
            if old[key] != plan[key]:
                raise RuntimeError(f"Frozen {stage} plan changed: {key}")
        for key in FINGERPRINT_FIELDS:
            if old["fingerprint"][key] != fingerprint[key]:
                raise RuntimeError(f"Node fingerprint changed: {key}")
    else:
        runner.write(path, plan)
    return fingerprint


def _run(unit: Path, variant: str, repeat: int, image: str, out: Path,
         fingerprint: dict, control: dict) -> dict:
    launch = runner.run_one if roster_runner.is_batch(unit) else roster_runner.run_single
    row = launch(unit, variant, repeat, image, out, 1, fingerprint)
    if row["status"] == "passed":
        row["control_match"] = (
            row["stable"] == control["stable"]
            and row["event_count"] == control["event_count"]
        )
        if not row["control_match"]:
            row["status"] = "failed"
            row["failure_class"] = "participant_semantic_difference"
        accepted = out / "attempts" / unit.name / variant / str(repeat) / "1" / "accepted" / unit.name
        events = accepted / ("batch_events.json" if roster_runner.is_batch(unit) else "events.json")
        row["reported_rate"] = float(json.loads(events.read_text())["events_per_sec"])
    return row


def _run_screen(candidate_id: str, controls: dict[str, dict]) -> None:
    roster = {p.name: p for p in roster_runner.roster()}
    units = [roster[name] for name in UNITS]
    fingerprint = _freeze_plan(OUT_SCREEN, units, candidate_id, "screen")
    path = OUT_SCREEN / "records.json"
    rows = json.loads(path.read_text()) if path.exists() else []
    for unit in units:
        for repeat in (0, 1, 2):
            order = VARIANTS if repeat != 2 else tuple(reversed(VARIANTS))
            for variant in order:
                key = runner.key(unit.name, variant, repeat)
                if any(row["key"] == key and row["status"] == "passed" for row in rows):
                    continue
                if any(row["key"] == key for row in rows):
                    raise RuntimeError(f"Failed attempt requires diagnosis before retry: {key}")
                image = PARENT_ID if variant == "unlocked" else candidate_id
                row = _run(unit, variant, repeat, image, OUT_SCREEN, fingerprint, controls[unit.name])
                rows.append(row)
                runner.write(path, rows)
                runner.write(OUT_SCREEN / "summary.json", _screen_summary(rows))
                print(row["status"], key, row.get("reported_rate"), row.get("rate"), flush=True)
                if row["status"] != "passed":
                    raise RuntimeError(f"Four-unit screen failed: {key}: {row.get('failure_class')}")


def _run_semantic(candidate_id: str, controls: dict[str, dict], max_runs: int | None) -> None:
    screen_result = json.loads((OUT_SCREEN / "summary.json").read_text())
    if not screen_result["complete"] or not screen_result["all_semantics_equal_control"]:
        raise RuntimeError("Successful four-unit paired screen required before 71-unit gate")
    units = roster_runner.roster()
    if set(controls) != {unit.name for unit in units}:
        raise RuntimeError("Frozen control roster differs from current 71 units")
    fingerprint = _freeze_plan(OUT_SEMANTIC, units, candidate_id, "semantic")
    path = OUT_SEMANTIC / "records.json"
    rows = json.loads(path.read_text()) if path.exists() else []
    started = 0
    for unit in units:
        if any(row["unit"] == unit.name and row["status"] == "passed" for row in rows):
            continue
        if any(row["unit"] == unit.name for row in rows):
            raise RuntimeError(f"Failed attempt requires diagnosis before retry: {unit.name}")
        row = _run(unit, "scalar", 0, candidate_id, OUT_SEMANTIC, fingerprint, controls[unit.name])
        rows.append(row)
        runner.write(path, rows)
        runner.write(OUT_SEMANTIC / "summary.json", _semantic_summary(rows, units))
        print(row["status"], unit.name, row.get("reported_rate"), flush=True)
        if row["status"] != "passed":
            raise RuntimeError(f"71-unit semantic gate failed: {unit.name}: {row.get('failure_class')}")
        started += 1
        if max_runs is not None and started >= max_runs:
            return
    runner.write(OUT_SEMANTIC / "node-fingerprint-after.json", node_fingerprint.collect().to_dict())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("screen", "semantic"), required=True)
    parser.add_argument("--candidate-id", required=True, help="immutable sha256: image ID after build")
    parser.add_argument("--max-runs", type=int, default=None, help="semantic stage only; resume later")
    args = parser.parse_args()
    if args.max_runs is not None and (args.max_runs < 1 or args.stage != "semantic"):
        parser.error("--max-runs is positive and only valid for semantic stage")
    if sys.platform != "linux":
        parser.error("Run with the retained Linux T3 validation Python")
    if not args.candidate_id.startswith("sha256:") or len(args.candidate_id) != 71:
        parser.error("--candidate-id must be a full sha256: Docker image ID")
    runner.verify_current_verifier_source()
    if runner.inspect_image(PARENT_TAG) != PARENT_ID or runner.inspect_image(PARENT_ID) != PARENT_ID:
        raise RuntimeError("Frozen unlocked-queue parent image identity changed")
    if runner.inspect_image(CANDIDATE_TAG) != args.candidate_id:
        raise RuntimeError("Experimental scalar-clamp image identity changed")
    controls = _controls()
    if args.stage == "screen":
        _run_screen(args.candidate_id, controls)
    else:
        _run_semantic(args.candidate_id, controls, args.max_runs)


if __name__ == "__main__":
    main()
