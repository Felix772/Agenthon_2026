"""Strict paired public-unit screen for the T3 single-owner event queue.

Uses the current public developer verifier and the same launcher as the
retained 71-unit experiment. Both participant-reported Development rate and
Docker-measured start-to-exit rate are recorded. Nothing is uploaded.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import subprocess
import sys

import run_parallel_batch_experiment as screen
import run_t3_full_roster_parallel4 as full
from throughput import node_fingerprint


OUT = screen.ROOT / "project-evidence/t3-unlocked-queue-screen-20260928"
PARENT = "sha256:df5e1b9d96abf06947a33cf499c3b4feed271fa31f6439de8ce49ff98a834489"
CANDIDATE_TAG = "simulation-agent:unlocked-queue-dev-20260928"
CANDIDATE = "sha256:94bda24a509530e2bf2dfb0f079a0c052b29422c87bc62163aac1701dfe194bc"
UNITS = (
    "t3-eq-deterministic-baseline",  # short single, equal-time priorities
    "t3-as01-base-mix",              # ordinary mixed agents
    "t3-gb-pop-128-agents",          # large single, message-heavy
    "t3-gbatch-homog-4",             # scored multi-market batch
)
VARIANTS = ("incumbent", "unlocked")


def _summary(records: list[dict]) -> dict:
    passed = {(r["unit"], r["variant"], r["repeat"]): r for r in records if r["status"] == "passed"}
    result: dict = {"complete": len(passed) == len(UNITS) * len(VARIANTS) * 3, "units": {}}
    for unit in UNITS:
        group = {variant: [passed.get((unit, variant, repeat)) for repeat in (1, 2)]
                 for variant in VARIANTS}
        if not all(all(rows) for rows in group.values()):
            continue
        baseline, candidate = group.values()
        result["units"][unit] = {
            "incumbent_reported_median": statistics.median(r["reported_rate"] for r in baseline),
            "candidate_reported_median": statistics.median(r["reported_rate"] for r in candidate),
            "incumbent_container_median": statistics.median(r["rate"] for r in baseline),
            "candidate_container_median": statistics.median(r["rate"] for r in candidate),
        }
        result["units"][unit]["reported_ratio"] = (
            result["units"][unit]["candidate_reported_median"] /
            result["units"][unit]["incumbent_reported_median"]
        )
        result["units"][unit]["container_ratio"] = (
            result["units"][unit]["candidate_container_median"] /
            result["units"][unit]["incumbent_container_median"]
        )
    if result["complete"]:
        result["equal_unit_reported_mean_ratio"] = (
            statistics.mean(v["candidate_reported_median"] for v in result["units"].values()) /
            statistics.mean(v["incumbent_reported_median"] for v in result["units"].values())
        )
        result["equal_unit_container_mean_ratio"] = (
            statistics.mean(v["candidate_container_median"] for v in result["units"].values()) /
            statistics.mean(v["incumbent_container_median"] for v in result["units"].values())
        )
    return result


def main() -> None:
    if sys.platform != "linux":
        raise RuntimeError("Run with the retained Linux T3 validation Python")
    screen.verify_current_verifier_source()
    if screen.inspect_image(CANDIDATE_TAG) != CANDIDATE or screen.inspect_image(PARENT) != PARENT:
        raise RuntimeError("Candidate or incumbent image identity changed")
    roster = {p.name: p for p in full.roster()}
    units = [roster[name] for name in UNITS]
    OUT.mkdir(parents=True, exist_ok=True)
    input_hashes = full.stage_inputs(units, OUT)
    fingerprint = node_fingerprint.collect().to_dict()
    co_residents = subprocess.check_output(["docker", "ps", "--format", "{{.Names}} {{.Status}} {{.Image}}"], text=True).splitlines()
    plan = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "incumbent": PARENT, "candidate": CANDIDATE,
        "rules_ref": "f910de231209ebbca060efee47fe2c41aabe4bce",
        "verifier_archive_sha256": screen.VERIFIER_ARCHIVE_SHA256,
        "units": list(UNITS), "variants": list(VARIANTS),
        "input_hashes": input_hashes, "fingerprint": fingerprint,
        "co_residents_at_start": co_residents,
        "protocol": "one warm-up per image/unit, then two alternating paired measured repeats",
        "execution": "strict 4 CPU/16GiB/no-swap/nonroot/read-only/no-network/64MiB tmpfs; 300s hard stop",
        "development_rate": "reported n_events / measured simulation wall_clock_sec; no fabricated times",
        "rankable": False,
    }
    plan_path = OUT / "plan.json"
    if plan_path.exists():
        old = json.loads(plan_path.read_text())
        for key in ("incumbent", "candidate", "rules_ref", "verifier_archive_sha256", "units", "variants", "input_hashes", "fingerprint", "protocol", "execution", "development_rate", "rankable"):
            if old[key] != plan[key]:
                raise RuntimeError(f"Frozen screen plan changed: {key}")
    else:
        screen.write(plan_path, plan)
    records_path = OUT / "records.json"
    records = json.loads(records_path.read_text()) if records_path.exists() else []
    for unit in units:
        for repeat in (0, 1, 2):
            order = VARIANTS if repeat != 2 else tuple(reversed(VARIANTS))
            for variant in order:
                key = screen.key(unit.name, variant, repeat)
                if any(row["key"] == key and row["status"] == "passed" for row in records):
                    continue
                if any(row["key"] == key for row in records):
                    raise RuntimeError(f"Failed attempt requires diagnosis before retry: {key}")
                image = PARENT if variant == "incumbent" else CANDIDATE
                run = screen.run_one if full.is_batch(unit) else full.run_single
                row = run(unit, variant, repeat, image, OUT, 1, fingerprint)
                if row["status"] == "passed":
                    accepted = OUT / "attempts" / unit.name / variant / str(repeat) / "1" / "accepted" / unit.name
                    events_path = accepted / ("batch_events.json" if full.is_batch(unit) else "events.json")
                    row["reported_rate"] = float(json.loads(events_path.read_text())["events_per_sec"])
                    first = next((r for r in records if r["unit"] == unit.name and r["variant"] == variant and r["repeat"] == 0 and r["status"] == "passed"), None)
                    if first and (row["stable"] != first["stable"] or row["event_count"] != first["event_count"]):
                        row["status"] = "failed"
                        row["failure_class"] = "participant_repeat"
                    opposite = next((r for r in records if r["unit"] == unit.name and r["variant"] != variant and r["status"] == "passed"), None)
                    if opposite and (row["stable"] != opposite["stable"] or row["event_count"] != opposite["event_count"]):
                        row["status"] = "failed"
                        row["failure_class"] = "participant_semantic_difference"
                records.append(row)
                screen.write(records_path, records)
                screen.write(OUT / "summary.json", _summary(records))
                print(row["status"], key, row.get("reported_rate"), row.get("rate"), flush=True)
                if row["status"] != "passed":
                    raise RuntimeError(f"Screen failed: {key}: {row.get('failure_class')}")
    screen.write(OUT / "summary.json", _summary(records))


if __name__ == "__main__":
    main()
