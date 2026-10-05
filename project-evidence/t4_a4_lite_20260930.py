"""Evidence-only history audit. Never imports outcomes or changes participant sources."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import statistics
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "project-evidence/t14-experiments/A4/lite-v1"
REV = "febb5d2fb4cf8adcb6abc5a450cd5ae44c9a53d4"
SNAPSHOT = ROOT / ".validation/t4-origin-5.2.0-20260929"
FROZEN = ROOT / ".validation/t4-core-20260929-v1"
METHODS = ("median3", "last", "mean3")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def write_once(name, value):
    DEST.mkdir(parents=True, exist_ok=True)
    path = DEST / name
    with path.open("xb") as stream:
        stream.write(encode(value))
    return digest(path.read_bytes())


def git_blob(path):
    # Only participant-input and public-code paths are passed by this program.
    return subprocess.check_output(["git", "-C", str(ROOT / "track4-analysis-public"),
                                    "show", f"{REV}:{path}"])


def source_bindings():
    build = json.loads((ROOT / "project-evidence/t4-core-build-20260929-v1.json").read_bytes())
    rows = {}
    for relative, expected in build["context_files"].items():
        actual = digest((FROZEN / relative).read_bytes())
        if actual != expected:
            raise ValueError(f"frozen source/context changed: {relative}")
        rows[str((FROZEN / relative).relative_to(ROOT))] = actual
    for path in sorted((ROOT / "analysis-agent/analysis_agent").glob("*.py")):
        rows[str(path.relative_to(ROOT))] = digest(path.read_bytes())
    rows["qfbench-agent/agent/model_client.py"] = digest(
        (ROOT / "qfbench-agent/agent/model_client.py").read_bytes())
    return rows


def prepare():
    sys.path.insert(0, str(FROZEN))
    from analysis_agent.fallback import _series
    from analysis_agent.retrieval import RetrievalIndex
    from analysis_agent.contract import _task_contract

    bindings = source_bindings()
    unit_paths = subprocess.check_output(["git", "-C", str(ROOT / "track4-analysis-public"),
        "ls-tree", "-r", "--name-only", REV, "units"]).decode().splitlines()
    series_rows, unit_rows = [], []
    for task_path in sorted(p for p in unit_paths if p.endswith("/task.json")):
        unit = str(Path(task_path).parent).replace("\\", "/")
        task_bytes = git_blob(task_path)
        task = json.loads(task_bytes)
        if task["target"]["type"] != "regression":
            continue
        manifest_bytes = git_blob(unit + "/manifest.json")
        manifest = json.loads(manifest_bytes)
        for relative, payload in (("task.json", task_bytes), ("manifest.json", manifest_bytes)):
            if (SNAPSHOT / unit / relative).read_bytes() != payload:
                raise ValueError("snapshot and raw Git input differ")
        index = RetrievalIndex.load(SNAPSHOT / unit / "corpus", task["cutoff_date"])
        _, _, units, _ = _task_contract(task)
        for entity in task["entities"]:
            result = _series(task, entity, index)
            if result is None:
                continue
            doc, observations = result
            entry = next(row for row in manifest["files"]
                         if row["path"] == f"corpus/{doc.doc_id}.json")
            original = git_blob(unit + "/corpus/" + doc.doc_id + ".json")
            if digest(original) != entry["sha256"]:
                raise ValueError("Git corpus payload differs from trusted manifest")
            count = len(observations)
            dates = [row[0] for row in observations]
            values = [row[1] for row in observations]
            if not all(math.isfinite(value) for value in values):
                raise ValueError("nonfinite history")
            target_date = entity.get("auction_date", entity.get("release_date", task["resolution_date"]))
            family = "ratio_history" if units[entity["entity_id"]] == "bid_to_cover_ratio" else "monthly_mom_history"
            origin_rows = [
                {"target_index": i, "target_date": dates[i], "target_block": dates[i][:7],
                 "history_indices": [0, i], "residual_target_indices": [3, i],
                 "split": "selection" if i < 9 else "confirmation"}
                for i in range(6, count)
            ]
            series_rows.append({
                "series_id": entity["entity_id"], "family": family,
                "unit_path": unit, "task_id": task["task_id"], "unit": units[entity["entity_id"]],
                "source_doc_id": doc.doc_id, "source_sha256": entry["sha256"],
                "task_sha256": digest(task_bytes), "manifest_sha256": digest(manifest_bytes),
                "license": entry.get("license"), "redistributable": entry.get("redistributable"),
                "explicit_owner": list(doc.entity_ids) if doc.entity_ids is not None else None,
                "shared": doc.shared, "ownership_pass": doc.admits(entity["entity_id"]),
                "task_cutoff": task["cutoff_date"], "source_doc_date": doc.doc_date,
                "original_forecast_horizon_days": (date.fromisoformat(target_date) - date.fromisoformat(task["cutoff_date"])).days,
                "history_count": count, "dates": dates, "values": values,
                "series_sha256": digest(encode(list(zip(dates, values)))),
                "rolling_origins": origin_rows,
                "row_first_availability_verified": False,
                "vintage_note": ("Auction-date observations; per-row dated publication/correction history has not been verified."
                                 if family == "ratio_history" else
                                 "2024-10-31 snapshot vintage; only September is explicitly first print. Earlier first-print values unavailable."),
                "actual_task_cutoff_eligible": True,
                "historical_asof_quality_eligible": False,
            })
        unit_rows.append({"unit_path": unit, "task_sha256": digest(task_bytes),
                          "manifest_sha256": digest(manifest_bytes)})
    if len(series_rows) != 18 or sum(row["history_count"] for row in series_rows) != 190:
        raise ValueError("unexpected accepted-series inventory; do not widen post hoc")
    methods = {
        "median3": "median of the three preceding values; existing frozen-v1 control",
        "last": "most recent preceding value",
        "mean3": "arithmetic mean of the three preceding values",
    }
    families = {}
    for family in sorted({row["family"] for row in series_rows}):
        rows = [row for row in series_rows if row["family"] == family]
        selection = [origin for row in rows for origin in row["rolling_origins"] if origin["split"] == "selection"]
        confirm = [origin for row in rows for origin in row["rolling_origins"] if origin["split"] == "confirmation"]
        families[family] = {
            "series": len(rows), "observations": sum(row["history_count"] for row in rows),
            "selection_pairs_per_method": len(selection), "confirmation_pairs_per_method": len(confirm),
            "confirmation_month_blocks": sorted({row["target_block"] for row in confirm}),
            "historical_asof_quality_eligible": False,
            "blockers": ["No per-row first-availability/revision proof",
                         "Fewer than six confirmation month blocks",
                         "One public task snapshot; all source values already exposed, not blind"],
        }
    data_hash = write_once("series-inventory.json", {"source_revision": REV, "series": series_rows, "units_scanned": unit_rows})
    eligibility_hash = write_once("eligibility.json", {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Official input history only; no current task outcomes, hidden labels, House calls, or fitted participant artifact.",
        "series_inventory_sha256": data_hash,
        "families": families, "promotion_eligible": False,
        "snapshot_diagnostic_possible": True,
        "snapshot_diagnostic_meaning": "Mask later table rows within the already exposed pre-task-cutoff snapshot; no claim of historically available vintages or true prospective validation.",
        "provenance_rule": "Per-file manifest license governs; all fitting, selection and calibration availability must precede each task cutoff. Historical origin dates additionally need release/vintage proof for as-of validation.",
        "cpi_specific_blocker": "A single revised vintage cannot reconstruct historical first-print targets; do not substitute it silently.",
        "auction_specific_blocker": "Dated auction releases/correction history not established by auction_date alone.",
        "future_data_needed": "Separately approved provenance audit and longer pre-cutoff historical vintages, without reading actual task resolutions.",
    })
    prereg = {
        "registered_at_utc": datetime.now(timezone.utc).isoformat(),
        "experiment": "A4-lite-v1", "status": "frozen_before_comparing_losses",
        "methods": methods, "max_methods_including_control": 3,
        "method_selection_unit": "whole input-structure family; never entity/unit/doc IDs",
        "interval_rule": "For each method and origin i: point on values[:i]; residuals values[j]-method(values[:j]) for j=3..i-1; [min(point,point+min(residuals)),max(point,point+max(residuals))]. Only prior residuals, no fitted width/quantile, no 90% coverage guarantee.",
        "history_minimum": 6,
        "split_rule": "First three eligible origins are selection; every later eligible origin is confirmation. Splits are frozen by date/index before loss comparison.",
        "family_counts": families,
        "metrics": ["MAE", "official common2.5 mean_interval_score at .90", "coverage", "mean_width", "worst_outside_distance", "max_absolute_error"],
        "primary": "MAE",
        "candidate_gate": {"selection_mae_improvement_min": .05, "selection_is90_relative_worsening_max": .02,
            "confirmation_mae_improvement_min": .05, "confirmation_is90_relative_worsening_max": .02,
            "worst_family_relative_mae_or_is90_worsening_max": .02,
            "worst_outside_distance_relative_worsening_max": .05,
            "minimum_confirmation_calendar_month_blocks": 6,
            "minimum_confirmation_pairs_per_family": 30,
            "minimum_series_per_family": 3,
            "availability_and_vintage_proof_required": True,
            "paired_uncertainty": "Month blocks, never pretend correlated series are independent; confirmation primary loss improvement must have a positive 95% paired block interval; do not compute inferential interval below six month blocks.",
            "zero_control_loss": "Alternative must also have zero loss; no division by an epsilon and no invented relative improvement.",
            "tie_break": "Keep control; among eligible alternatives smaller selection MAE, then smaller IS90, then fixed method order."},
        "exposure": "Public history values have already been read during development. Registration precedes this experiment's numerical comparison, not prior human/model exposure; confirmation is a mechanical date holdout, not blind data.",
        "input_surface": "Numeric (date,value) prefixes only. Never pass full corpus text, trailing NOTES, future rows, target task entity features, or later publications to a historical predictor.",
        "runtime_integration": False, "online_submission": False, "A3_enabled": False,
        "official_score_claimed": False, "strict_5_2_ownership_unchanged": True,
        "source_revision": REV, "toolkit": importlib.metadata.version("qfbench2-common"),
        "source_bindings": bindings, "harness_sha256": digest(Path(__file__).read_bytes()),
        "series_inventory_sha256": data_hash, "eligibility_sha256": eligibility_hash,
    }
    prereg_hash = write_once("pre-registration.json", prereg)
    if source_bindings() != bindings:
        raise ValueError("participant source changed during inventory")
    print(json.dumps({"prepared": True, "series": len(series_rows), "preregistration_sha256": prereg_hash,
                      "eligibility_sha256": eligibility_hash, "promotion_eligible": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare",))
    args = parser.parse_args()
    prepare()
