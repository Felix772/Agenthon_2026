"""Paired practice diagnostics, excluding outcomes without exact archived values.

This does not recreate the sealed official composite or pool different units.
The input prediction files must be frozen before this script reads any outcomes.
"""
import argparse
import hashlib
import json
from pathlib import Path

from qfbench2_common.scoring.faithfulness import mean_interval_score


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    before = {r["entity_id"]: r for r in load(args.before)["entity_predictions"]}
    after = {r["entity_id"]: r for r in load(args.after)["entity_predictions"]}
    labels = load(args.labels)
    assert set(before) == set(after) == {r["entity_id"] for r in labels["rows"]}
    rows, excluded = [], []
    for label in labels["rows"]:
        entity = label["entity_id"]
        y = label["realized_value"]
        if y is None:
            excluded.append({"entity_id": entity, "reason": "Exact archived value unavailable at task precision"})
            continue
        assert label["status"] == "verified_at_task_precision"
        a, b = before[entity], after[entity]
        assert a["label"] == b["label"]
        assert b["point_forecast"] == label["latest_precutoff_estimate"]
        item = {"entity_id": entity, "series": label["series_id"], "units": label["units"],
                "outcome": y, "source_id": label["source_id"],
                "candidate_point_absolute_error": abs(b["point_forecast"] - y),
                "point_comparison": "Baseline omitted point; candidate equals input persistence"}
        for prefix, prediction in (("before", a), ("after", b)):
            band = prediction["interval"]
            item[prefix + "_raw_interval_score"] = mean_interval_score(
                [band["lo"]], [band["hi"]], [y], band["level"])
            item[prefix + "_covered"] = band["lo"] <= y <= band["hi"]
        item["interval_score_improved"] = item["after_raw_interval_score"] < item["before_raw_interval_score"]
        rows.append(item)
    report = {"date": "2026-10-09", "task_id": labels["task_id"], "practice_in_sample": True,
              "prediction_sha256": {"before": sha256(args.before), "after": sha256(args.after)},
              "labels_sha256": sha256(args.labels), "matched_exact_rows": len(rows),
              "excluded_rows": excluded, "interval_score_improved_rows": sum(r["interval_score_improved"] for r in rows),
              "before_covered_rows": sum(r["before_covered"] for r in rows),
              "after_covered_rows": sum(r["after_covered"] for r in rows),
              "direction_changed": False, "official_score": None,
              "limitations": ["Exposed practice data, not held-out or Final evidence.",
                              "Intervals use an uncalibrated plus/minus half-anchor width fixed before label collection.",
                              "No numeric baseline point comparison is possible because it omitted points.",
                              "Do not average raw errors or interval scores across incompatible units.",
                              "Sealed naive-answer parameters and two exact INDPRO values are unavailable."],
              "sources": labels["sources"], "rows": rows}
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"sources", "rows"}}))


if __name__ == "__main__":
    main()
