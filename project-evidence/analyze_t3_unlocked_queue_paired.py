"""Summarize the frozen T3 71-unit paired run; no Docker execution.

Unit and family resampling are sensitivity diagnostics, not uncertainty of
the competition's fixed 71-unit roster or a substitute for independent runs.
"""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import random
import re
import statistics


ROOT = Path(__file__).resolve().parent
DIR = ROOT / "t3-unlocked-queue-paired-20260928"
HORIZON = "t3-gb-horizon-240s"
FINGERPRINT_FIELDS = ("cpu_model", "cpu_count", "memory_bytes", "gpu_name", "gpu_count")


def family(unit: str) -> str:
    key = unit.removeprefix("t3-")
    if key.startswith("gbatch-"):
        return "gbatch"
    if re.match(r"as\d", key):
        return "as"
    if key.startswith("ca-"):
        return "ca"
    if key.startswith("eq-") or key.startswith("eq001-"):
        return "eq"
    if key.startswith("fastlob-"):
        return "fastlob"
    if key.startswith("gb-"):
        return "gb-single"
    if re.match(r"mp\d", key):
        return "mp"
    if key.startswith("mr-"):
        return "mr"
    if re.match(r"ra\d", key):
        return "ra"
    if re.match(r"s\d", key):
        return "s"
    if key.startswith("sf-"):
        return "sf"
    if re.match(r"st\d", key):
        return "st"
    return "misc-single"


def interval(values: list[float], groups: list[str] | None = None) -> list[float]:
    rng = random.Random(20260928 + len(values) + (1 if groups else 0))
    samples = []
    if groups is None:
        for _ in range(20_000):
            samples.append(statistics.mean(rng.choices(values, k=len(values))))
    else:
        by_group: dict[str, list[float]] = defaultdict(list)
        for group, value in zip(groups, values, strict=True):
            by_group[group].append(value)
        blocks = list(by_group.values())
        for _ in range(20_000):
            draw = [value for block in rng.choices(blocks, k=len(blocks)) for value in block]
            samples.append(statistics.mean(draw))
    samples.sort()
    return [samples[499], samples[19_499]]


def metrics(names: list[str], units: dict[str, dict]) -> dict:
    rows = [units[name] for name in names]
    result = {"count": len(rows)}
    for metric in ("reported", "container"):
        base = [row[f"incumbent_{metric}_rate"] for row in rows]
        candidate = [row[f"unlocked_{metric}_rate"] for row in rows]
        differences = [b - a for a, b in zip(base, candidate, strict=True)]
        result[metric] = {
            "incumbent_mean_rate": statistics.mean(base),
            "candidate_mean_rate": statistics.mean(candidate),
            "absolute_difference": statistics.mean(differences),
            "relative_difference": statistics.mean(candidate) / statistics.mean(base) - 1,
            "improved_units": sum(value > 0 for value in differences),
            "slowed_units": sum(value < 0 for value in differences),
            "unit_bootstrap95_difference": interval(differences),
            "family_block_bootstrap95_difference": interval(differences, [family(name) for name in names])
            if len(set(map(family, names))) > 1 else None,
        }
    return result


def main() -> None:
    plan = json.loads((DIR / "plan.json").read_text())
    summary = json.loads((DIR / "summary.json").read_text())
    records = json.loads((DIR / "records.json").read_text())
    post = json.loads((DIR / "node-fingerprint-after.json").read_text())
    names = plan["units"]
    units = summary["units"]
    if not summary["complete"] or len(names) != 71 or len(units) != 71 or set(names) != set(units):
        raise RuntimeError("The paired roster is incomplete")
    if len(records) != 426 or len({row["key"] for row in records}) != 426:
        raise RuntimeError("Expected 426 distinct scheduled runs")
    if any(row["status"] != "passed" for row in records):
        raise RuntimeError("A paired attempt did not pass")
    if any(plan["fingerprint"][field] != post[field] for field in FINGERPRINT_FIELDS):
        raise RuntimeError("Node fingerprint differs at the end")
    singles = [name for name in names if not units[name]["is_batch"]]
    batches = [name for name in names if units[name]["is_batch"]]
    if len(singles) != 65 or len(batches) != 6 or HORIZON not in singles:
        raise RuntimeError("Unexpected 65/6 roster or horizon unit")
    result = {
        "scope": "local Development self-report and Docker Final proxy; nonrankable",
        "admission": {"complete": True, "strict_runs": len(records), "units": len(names), "failures": 0,
                      "stable_hashes_and_event_counts_equal": True},
        "node_fingerprint_start_end_equal": True,
        "families": {name: family(name) for name in names},
        "all_71": metrics(names, units),
        "single_65": metrics(singles, units),
        "batch_6": metrics(batches, units),
        "exclude_overlap_horizon_70": metrics([name for name in names if name != HORIZON], units),
        "exclude_overlap_horizon_singles_64": metrics([name for name in singles if name != HORIZON], units),
        "horizon_unit": units[HORIZON],
        "family_mean_differences": {},
        "leave_one_family_out_all_71": {},
    }
    for group in sorted({family(name) for name in names}):
        members = [name for name in names if family(name) == group]
        result["family_mean_differences"][group] = metrics(members, units)
        result["leave_one_family_out_all_71"][group] = metrics(
            [name for name in names if family(name) != group], units
        )
    (DIR / "analysis.json").write_text(json.dumps(result, indent=2) + "\n")
    for label in ("all_71", "single_65", "batch_6", "exclude_overlap_horizon_70"):
        row = result[label]
        print(label, row["count"], "reported", row["reported"]["absolute_difference"],
              row["reported"]["relative_difference"], "container", row["container"]["absolute_difference"],
              row["container"]["relative_difference"],
              "family95", row["container"]["family_block_bootstrap95_difference"])


if __name__ == "__main__":
    main()
