"""Dry-run the frozen six-batch scoring schedule without Docker."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def load_runner():
    path = Path(__file__).with_name("run_parallel_batch_experiment.py")
    spec = importlib.util.spec_from_file_location("t3_parallel_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    runner = load_runner()
    runner.verify_current_verifier_source()
    units = runner.roster()
    assert len(units) == 6
    assert len(runner.schedule(units, "smoke")) == 24
    assert len(runner.schedule(units, "timing")) == 120
    for unit in units:
        assert len(runner.stable_paths_for(unit)) == 2 * len(
            list((unit / "scenarios").glob("*.json"))
        )
    assert runner.docker_duration_ns(
        "2026-09-27T22:00:00.000000001Z", "2026-09-27T22:00:01.000000003Z"
    ) == 1_000_000_002
    records = []
    rates = {"baseline": 100.0, "workers-1": 105.0, "workers-2": 120.0, "workers-4": 90.0}
    for unit in units:
        for variant in runner.VARIANTS:
            for repeat in range(6):
                records.append({
                    "key": runner.key(unit.name, variant, repeat),
                    "unit": unit.name, "variant": variant, "repeat": repeat,
                    "status": "passed", "rate": rates[variant],
                })
    full = runner.summary(records, units)
    assert full["smoke_passed"] and full["timing_complete"] and full["semantic_gate"]
    assert full["variants"]["workers-2"]["exploratory_gate"]
    assert not full["variants"]["workers-4"]["exploratory_gate"]
    assert full["variants"]["workers-2"]["six_unit_mean_of_medians"] == 120.0
    failed = next(
        row for row in records
        if row["unit"] == units[0].name and row["variant"] == "workers-2" and row["repeat"] == 3
    )
    failed.update(status="failed", failure_class="participant_repeat", rate=0.0)
    with_zero = runner.summary(records, units)
    assert with_zero["timing_complete"] and not with_zero["semantic_gate"]
    assert with_zero["variants"]["workers-2"]["unit_median_rates"][units[0].name] == 0.0
    assert with_zero["variants"]["workers-2"]["six_unit_mean_of_medians"] == 100.0
    assert not with_zero["variants"]["workers-2"]["exploratory_gate"]
    print("current verifier, roster, schedule, timer and failure-zero summary: PASS")


if __name__ == "__main__":
    main()
