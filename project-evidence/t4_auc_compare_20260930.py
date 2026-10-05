"""Registered auction comparison. Real-data execution requires a separate gate artifact."""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import inspect
import json
import math
from pathlib import Path
import random
import statistics

from qfbench2_common.scoring.faithfulness import mean_interval_score

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "project-evidence/t14-experiments/A4/auction-release-v1"
DATASET_SHA = "8a5f2ca1ff0380722c758102ccbd8316afe247c5b2764aeda31224ee7202a22b"
SUMMARY_SHA = "0042e0b8e7e6a89ff3ad072f9c52987357bfa6c460ea3aa81b6660dd6c701486"
ADDENDUM_V2_SHA = "4cdc4d9faeb66ddb85ce893e49556024d599b634cfa2676b194bdafcd06ff008"
METHODS = ("median3", "last", "mean3")
TENORS = {2, 3, 5, 7, 10, 20, 30}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def write_new(path, value):
    payload = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(payload)


def official_metric_binding():
    assert importlib.metadata.version("qfbench2-common") == "2.5.0"
    assert tuple(inspect.signature(mean_interval_score).parameters) == ("lo", "hi", "y", "interval_level")
    return {"package": "qfbench2-common", "version": "2.5.0",
            "source_sha256": sha(Path(inspect.getfile(mean_interval_score))),
            "function": "qfbench2_common.scoring.faithfulness.mean_interval_score",
            "interval_level": .9}


def point(method, values):
    if len(values) < 3 or not all(math.isfinite(value) for value in values):
        raise ValueError("need at least three finite prior observations")
    if method == "median3":
        return float(statistics.median(values[-3:]))
    if method == "last":
        return float(values[-1])
    if method == "mean3":
        return sum(values[-3:]) / 3
    raise ValueError("unregistered method")


def available_values(ids, origin_day, tenor, by_id):
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate history")
    rows = [by_id[rid] for rid in ids]
    if any(row["publication"]["date"] >= origin_day or row["original_maturity_years"] != tenor for row in rows):
        raise ValueError("history unavailable at origin or wrong maturity")
    if [r["publication"]["date"] for r in rows] != sorted(r["publication"]["date"] for r in rows):
        raise ValueError("history not chronological")
    return [row["bid_to_cover_ratio"] for row in rows]


def predict(method, origin, by_id):
    tenor, origin_day = origin["original_maturity_years"], origin["origin_announcement_date"]
    history_ids = origin["feature_record_ids"]
    if origin["target_record_id"] in history_ids or len(history_ids) < 6:
        raise ValueError("ineligible or contaminated history")
    values = available_values(history_ids, origin_day, tenor, by_id)
    estimate = point(method, values)
    residuals = []
    for past in origin["interval_residual_roster"]:
        target_id = past["past_target_record_id"]
        if target_id not in history_ids or target_id in past["past_feature_record_ids"]:
            raise ValueError("unavailable or contaminated residual target")
        if by_id[target_id]["announcement_publication"]["date"] != past["past_origin_day"]:
            raise ValueError("past origin mismatch")
        previous = available_values(past["past_feature_record_ids"], past["past_origin_day"], tenor, by_id)
        residuals.append(by_id[target_id]["bid_to_cover_ratio"] - point(method, previous))
    if not residuals:
        raise ValueError("no eligible past residuals")
    return estimate, min(estimate, estimate + min(residuals)), max(estimate, estimate + max(residuals))


def evaluate(method, origins, by_id):
    output = []
    for origin in origins:
        estimate, lo, hi = predict(method, origin, by_id)
        truth = by_id[origin["target_record_id"]]["bid_to_cover_ratio"]
        output.append({"target_record_id": origin["target_record_id"], "month": origin["target_auction_date"][:7],
                       "maturity": origin["original_maturity_years"], "actual": truth,
                       "point": estimate, "lo": lo, "hi": hi,
                       "absolute_error": abs(estimate - truth), "outside_distance": max(lo - truth, truth - hi, 0)})
    return output


def metrics(rows):
    if not rows:
        raise ValueError("empty comparison")
    return {"pairs": len(rows), "MAE": sum(row["absolute_error"] for row in rows) / len(rows),
            "IS90": mean_interval_score([r["lo"] for r in rows], [r["hi"] for r in rows], [r["actual"] for r in rows], .9),
            "coverage": sum(r["lo"] <= r["actual"] <= r["hi"] for r in rows) / len(rows),
            "width": sum(r["hi"] - r["lo"] for r in rows) / len(rows),
            "worst_outside_distance": max(r["outside_distance"] for r in rows),
            "max_absolute_error": max(r["absolute_error"] for r in rows)}


def relative_damage_ok(control, alternative, allowed):
    return alternative == 0 if control == 0 else alternative <= control * (1 + allowed)


def damage_checks(control, alternative, label):
    return {f"{label}:{metric}": relative_damage_ok(control[metric], alternative[metric], allowed)
            for metric, allowed in (("MAE", .02), ("IS90", .02), ("worst_outside_distance", .05))}


def gates_from_metrics(control, alternative, control_tenors, alternative_tenors):
    checks = {"aggregate:MAE_improves_at_least_5pct": control["MAE"] > 0 and alternative["MAE"] <= .95 * control["MAE"],
              "aggregate:IS90": relative_damage_ok(control["IS90"], alternative["IS90"], .02),
              "aggregate:worst_outside_distance": relative_damage_ok(control["worst_outside_distance"], alternative["worst_outside_distance"], .05)}
    if set(control_tenors) != TENORS or set(alternative_tenors) != TENORS:
        raise ValueError("incomplete maturity gate roster")
    for tenor in sorted(TENORS):
        checks.update(damage_checks(control_tenors[tenor], alternative_tenors[tenor], f"maturity_{tenor}"))
    return {"passed": all(checks.values()), "checks": checks}


def summary(rows):
    return {"aggregate": metrics(rows), "by_maturity": {tenor: metrics([r for r in rows if r["maturity"] == tenor]) for tenor in sorted(TENORS)}}


def compare_summaries(control, alternative):
    return gates_from_metrics(control["aggregate"], alternative["aggregate"], control["by_maturity"], alternative["by_maturity"])


def select_method(control, alternatives):
    eligible = [method for method, report in alternatives.items() if compare_summaries(control, report)["passed"]]
    return min(eligible, key=lambda method: (alternatives[method]["aggregate"]["MAE"],
                                           alternatives[method]["aggregate"]["IS90"], ("last", "mean3").index(method))) if eligible else "median3"


def percentile(sorted_values, probability):
    position = (len(sorted_values) - 1) * probability
    lo, hi = math.floor(position), math.ceil(position)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (position - lo)


def bootstrap(control, alternative, *, serial=False, iterations=20000):
    left = {r["target_record_id"]: r for r in control}
    right = {r["target_record_id"]: r for r in alternative}
    if set(left) != set(right) or len(left) != len(control) or len(right) != len(alternative):
        raise ValueError("unpaired rows")
    monthly = defaultdict(list)
    monthly_tenors = defaultdict(set)
    for rid, a in left.items():
        b = right[rid]
        assert (a["month"], a["maturity"]) == (b["month"], b["maturity"])
        monthly[a["month"]].append(a["absolute_error"] - b["absolute_error"])
        monthly_tenors[a["month"]].add(a["maturity"])
    months = sorted(monthly)
    if months != [f"2024-{m:02}" for m in range(1, 11)] or any(len(monthly[month]) != 7 or monthly_tenors[month] != TENORS for month in months):
        raise ValueError("frozen confirmation requires ten complete seven-maturity months")
    rng = random.Random(20260931 if serial else 20260930)
    values = []
    for _ in range(iterations):
        if serial:
            indexes = []
            for _ in range(5):
                start = rng.randrange(10)
                indexes.extend((start, (start + 1) % 10))
        else:
            indexes = [rng.randrange(10) for _ in range(10)]
        sample = [delta for index in indexes for delta in monthly[months[index]]]
        values.append(sum(sample) / len(sample))
    values.sort()
    interval = [percentile(values, .025), percentile(values, .975)]
    return {"type": "circular_two_month" if serial else "one_month", "iterations": iterations,
            "seed": 20260931 if serial else 20260930, "nominal_percentile_95": interval,
            "positive_lower_bound": interval[0] > 0,
            "coverage_or_generalization_guarantee": False}


def require_selection_winner(selection):
    chosen = selection["selected_method"]
    if chosen == "median3":
        raise ValueError("selection stopped; confirmation loss computation is forbidden")
    if chosen not in METHODS[1:] or not selection["alternative_gates"][chosen]["passed"]:
        raise ValueError("selection winner did not pass all gates")
    return chosen


def load_authorized(stage, authorization_path):
    if authorization_path is None:
        raise ValueError("separate root/ChatGPT gate artifact required before real losses")
    auth = read(authorization_path)
    assert auth["experiment"] == "A4-AUC-v1" and auth["stage"] == stage
    assert auth["approved_by"] == "root_after_chatgpt" and auth["real_loss_execution_authorized"] is True
    assert auth["dataset_sha256"] == DATASET_SHA
    assert auth["comparison_source_sha256"] == sha(Path(__file__))
    assert auth["addendum_v2_sha256"] == ADDENDUM_V2_SHA
    for item in auth["accepted_independent_reviews"]:
        assert sha(ROOT / item["path"]) == item["sha256"]
    assert len(auth["accepted_independent_reviews"]) >= 2
    assert sha(DIR / "dataset-freeze-summary-v1.json") == SUMMARY_SHA
    frozen = read(DIR / "dataset-freeze-summary-v1.json")
    for name, expected in frozen["artifact_sha256"].items():
        assert sha(DIR / name) == expected
    assert sha(DIR / "comparison-pre-registration-addendum-v2.json") == ADDENDUM_V2_SHA
    dataset, roster = read(DIR / "normalized-dataset-v1.json"), read(DIR / "causal-origins-v1.json")
    for relative, expected in frozen["source_preservation_checks"].items():
        assert sha(ROOT / relative) == expected
    by_id = {f"{row['auction_date']}:{row['cusip']}": row for row in dataset["records"]}
    return auth, roster["origins"], by_id


def run(stage, authorization_path):
    auth, origins, by_id = load_authorized(stage, authorization_path)
    common = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "experiment": "A4-AUC-v1", "stage": stage,
              "authorization_sha256": sha(authorization_path), "dataset_sha256": DATASET_SHA,
              "comparison_source_sha256": sha(Path(__file__)), "addendum_v2_sha256": ADDENDUM_V2_SHA,
              "official_metric": official_metric_binding(), "participant_integration_authorized": False}
    if stage == "selection":
        selected_origins = [o for o in origins if o["eligible_history"] and o["split"] == "selection"]
        assert len(selected_origins) == 42
        paired_rows = {method: evaluate(method, selected_origins, by_id) for method in METHODS}
        reports = {method: summary(paired_rows[method]) for method in METHODS}
        chosen = select_method(reports["median3"], {m: reports[m] for m in METHODS[1:]})
        result = {**common, "method_summaries": reports, "paired_rows": paired_rows,
                  "alternative_gates": {method: compare_summaries(reports["median3"], reports[method]) for method in METHODS[1:]},
                  "selected_method": chosen, "confirmation_losses_computed": False,
                  "next_action": "stop_retain_control" if chosen == "median3" else "freeze_selection_then_obtain_confirmation_execution_gate"}
        output_path = DIR / "selection-report-v1.json"
    else:
        selection_path = DIR / "selection-report-v1.json"
        assert sha(selection_path) == auth["selection_report_sha256"]
        selection = read(selection_path)
        assert selection["comparison_source_sha256"] == sha(Path(__file__))
        chosen = require_selection_winner(selection)
        selected_origins = [o for o in origins if o["eligible_history"] and o["split"] == "confirmation"]
        assert len(selected_origins) == 70
        selection_available = max(by_id[o["target_record_id"]]["publication"]["date"] for o in origins if o["split"] == "selection")
        assert selection_available < min(o["origin_announcement_date"] for o in selected_origins)
        control_rows = evaluate("median3", selected_origins, by_id)
        alternative_rows = evaluate(chosen, selected_origins, by_id)
        control, alternative = summary(control_rows), summary(alternative_rows)
        magnitude = compare_summaries(control, alternative)
        independent_ci, serial_ci = bootstrap(control_rows, alternative_rows), bootstrap(control_rows, alternative_rows, serial=True)
        passed = magnitude["passed"] and independent_ci["positive_lower_bound"] and serial_ci["positive_lower_bound"]
        result = {**common, "selected_method": chosen, "selection_report_sha256": sha(selection_path),
                  "control_summary": control, "alternative_summary": alternative, "magnitude_and_damage_gates": magnitude,
                  "one_month_interval": independent_ci, "two_month_interval": serial_ci, "passed_research_gates": passed,
                  "paired_rows": {"median3": control_rows, chosen: alternative_rows},
                  "next_action": "independent_review_and_separate_integration_decision" if passed else "stop_retain_control",
                  "official_competition_score": None}
        output_path = DIR / "confirmation-report-v1.json"
    write_new(output_path, result)
    print(json.dumps({"report": str(output_path), "sha256": sha(output_path), "next_action": result["next_action"]}))


def selftest():
    binding = official_metric_binding()
    assert math.isclose(mean_interval_score([1, 1], [3, 3], [2, 5], .9), 22)
    vals = [1., 4., 2., 6., 3., 8., 5., 9.]
    records = {str(i): {"publication": {"date": f"2020-{i+1:02}-20"},
        "announcement_publication": {"date": f"2020-{i+1:02}-10"},
        "original_maturity_years": 2, "bid_to_cover_ratio": value} for i, value in enumerate(vals)}
    origin = {"target_record_id": "7", "original_maturity_years": 2, "origin_announcement_date": "2020-08-10",
              "feature_record_ids": [str(i) for i in range(7)], "interval_residual_roster": [
                  {"past_target_record_id": str(i), "past_origin_day": f"2020-{i+1:02}-10", "past_feature_record_ids": [str(j) for j in range(i)]} for i in range(3, 7)]}
    assert predict("median3", origin, records) == (5., 4., 10.)
    changed_target = {**records, "7": {**records["7"], "bid_to_cover_ratio": 100000.}}
    assert predict("median3", origin, changed_target) == (5., 4., 10.)
    # Current history includes later points. Each past residual still uses its own frozen roster.
    changed_later = {**records, "6": {**records["6"], "bid_to_cover_ratio": 20.}}
    assert predict("median3", origin, changed_later) == (8., 7., 22.)
    bad = {**origin, "interval_residual_roster": [{**origin["interval_residual_roster"][0], "past_feature_record_ids": ["0", "1", "2", "6"]}]}
    try:
        predict("median3", bad, records)
        raise AssertionError("future residual history was accepted")
    except ValueError:
        pass
    control = {"MAE": 1., "IS90": 10., "worst_outside_distance": 1.}
    better = {"MAE": .9, "IS90": 10., "worst_outside_distance": 1.}
    left = {t: dict(control) for t in TENORS}
    right = {t: dict(better) for t in TENORS}
    assert gates_from_metrics(control, better, left, right)["passed"]
    right[30]["MAE"] = 1.021
    assert not gates_from_metrics(control, better, left, right)["passed"]
    assert not relative_damage_ok(0, .000001, .05)
    assert relative_damage_ok(0, 0, .05)
    assert not gates_from_metrics({**control, "MAE": 0}, {**better, "MAE": 0}, left, left)["passed"]
    a, b = [], []
    for month in range(1, 11):
        for tenor in sorted(TENORS):
            row = {"target_record_id": f"{month}:{tenor}", "month": f"2024-{month:02}", "maturity": tenor, "absolute_error": 2.}
            a.append(row)
            b.append({**row, "absolute_error": 1.})
    assert bootstrap(a, b)["nominal_percentile_95"] == [1., 1.]
    assert bootstrap(a, b, serial=True)["nominal_percentile_95"] == [1., 1.]
    assert not bootstrap(a, a)["positive_lower_bound"]
    variable_left = [{**r, "absolute_error": 10.} for r in a]
    variable_right = [{**r, "absolute_error": 10. - (int(r["month"][-2:]) - 1)} for r in b]
    # Independent scalar-month oracle: five randrange(10) draws, sum(start+(start+1)%10)/10.
    serial_reference = bootstrap(variable_left, variable_right, serial=True)
    assert serial_reference["nominal_percentile_95"] == [2.3, 6.7]
    assert bootstrap(variable_left, list(reversed(variable_right)), serial=True) == serial_reference
    malformed = [{**r, "maturity": 2} for r in a]
    try:
        bootstrap(malformed, malformed)
        raise AssertionError("seven duplicate maturities accepted")
    except ValueError:
        pass
    # This gate is called before any confirmation evaluate() invocation.
    try:
        require_selection_winner({"selected_method": "median3"})
        raise AssertionError("stopped selection allowed confirmation")
    except ValueError:
        pass
    try:
        load_authorized("selection", None)
        raise AssertionError("missing authorization accepted")
    except ValueError:
        pass
    print(json.dumps({"synthetic_tests_passed": True, "official_metric": binding, "real_dataset_read": False,
                      "real_losses_computed": False, "comparison_source_sha256": sha(Path(__file__))}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("selftest", "selection", "confirmation"))
    parser.add_argument("--authorization", type=Path)
    args = parser.parse_args()
    selftest() if args.stage == "selftest" else run(args.stage, args.authorization)
