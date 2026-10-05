"""Finalize immutable auction evidence after an audited path-only freeze interruption."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "project-evidence/t14-experiments/A4/auction-release-v1"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name):
    return json.loads((DIR / name).read_bytes())


def once(name, value):
    path = DIR / name
    if path.exists():
        raise ValueError(f"refuse overwrite {name}")
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return sha(path)


dataset = load("normalized-dataset-v1.json")
origins = load("causal-origins-v1.json")["origins"]
provenance = load("release-provenance-v1.json")
registration = load("pre-registration.json")
plan = load("acquisition-plan.json")
assert sha(DIR / "normalized-dataset-v1.json") == "8a5f2ca1ff0380722c758102ccbd8316afe247c5b2764aeda31224ee7202a22b"
rows = dataset["records"]
by_id = {f"{r['auction_date']}:{r['cusip']}": r for r in rows}
assert len(by_id) == len(rows) == len(origins) == 237
for origin in origins:
    assert origin["target_record_id"] not in origin["feature_record_ids"]
    for rid in origin["feature_record_ids"]:
        assert by_id[rid]["publication"]["date"] < origin["origin_announcement_date"]
    for residual in origin["interval_residual_roster"]:
        assert residual["past_target_record_id"] not in residual["past_feature_record_ids"]
        assert by_id[residual["past_target_record_id"]]["publication"]["date"] < origin["origin_announcement_date"]
        for rid in residual["past_feature_record_ids"]:
            assert by_id[rid]["publication"]["date"] < residual["past_origin_day"]
assert len(provenance["releases"]) == len(plan["locators"]) == 507
for release in provenance["releases"]:
    assert release["raw_sha256"] == sha(DIR / "raw" / release["filename"])
    assert release["extracted_text_sha256"] == sha(DIR / "text" / (release["filename"] + ".txt"))
    assert release["download_metadata_sha256"] == sha(DIR / "download-metadata" / (release["filename"] + ".json"))
    assert release["correction_marker"] is False
    assert release["release_date_claimed_in_heading"] <= "2024-10-31"
source_checks = {}
for relative, expected in registration["participant_source_bindings"].items():
    actual = sha(ROOT / relative)
    assert actual == expected, relative
    source_checks[relative] = actual
eligible = [o for o in origins if o["eligible_history"]]
selection = [o for o in eligible if o["split"] == "selection"]
confirm = [o for o in eligible if o["split"] == "confirmation"]
months = sorted({o["target_auction_date"][:7] for o in confirm})
assert len(selection) == 42 and len(confirm) == 70 and len(months) == 10
assert {o["original_maturity_years"] for o in confirm} == {2, 3, 5, 7, 10, 20, 30}
rules = ROOT / "project-evidence/t14-experiments/rules-refresh-20260930T005045Z.json"
assert sha(rules) == "36bf00b3e4501261d20514847a85a133091f315be767cb6c71a903a3e7bcffad"
addendum = {
    "experiment": "A4-AUC-v1", "registered_at_utc": datetime.now(timezone.utc).isoformat(),
    "status": "Frozen before any prediction loss or method comparison; no comparison authorization implied",
    "parent_registration_sha256": sha(DIR / "pre-registration.json"),
    "dataset_sha256": sha(DIR / "normalized-dataset-v1.json"),
    "causal_origins_sha256": sha(DIR / "causal-origins-v1.json"),
    "scope_clarification": {
        "minimum_confirmation_pairs": "At least 30 in the aggregate auction ratio-history family, not 30 per maturity.",
        "worst_family_guard": "Apply the registered maximum 2% relative MAE or IS90 worsening separately to every one of the seven maturity series. This tightens the ambiguous family wording before losses; it does not weaken an existing gate.",
        "worst_outside_guard": "Apply the registered maximum 5% relative worst outside-distance worsening both in aggregate and separately to each maturity series.",
        "sample": "70 confirmation pairs, 10 calendar month blocks, 7 series; unchanged splits.",
        "zero_control": "If a comparator control metric is zero, alternative must also be zero; no epsilon denominator. A zero aggregate MAE cannot establish the required 5% improvement, so retain control."},
    "selection": {
        "allowed_methods": ["median3", "last", "mean3"], "control": "median3",
        "method_scope": "One method for the entire auction ratio-history family; never choose by maturity, unit, CUSIP, document, or realized target.",
        "eligibility": "On the selection split require >=5% aggregate MAE improvement, <=2% aggregate IS90 worsening, <=2% MAE and IS90 worsening for each maturity, and <=5% worst outside-distance worsening aggregate and each maturity.",
        "ordering": "Among eligible alternatives choose smallest unrounded selection MAE, then smallest unrounded selection IS90, then fixed order last before mean3. If a metric ties control or no alternative meets all gates, keep median3.",
        "freeze_before_confirmation": "Write chosen method and selection report hash before computing confirmation losses. Only the selected alternative and control are evaluated on confirmation; no fallback to another alternative after a confirmation failure.",
        "no_retune": "No changes to dates, whitelist, sample gates, interval rule, or method hyperparameters after losses."},
    "paired_uncertainty": {
        "statistic": "Mean paired absolute-error reduction, MAE(control)-MAE(selected), across all auction observations in sampled months.",
        "cluster": "Target auction calendar month, preserving all seven maturities and their paired control/alternative outputs together.",
        "split": "Confirmation only, only if selection promotes a candidate; at least six month blocks required.",
        "resampling": "Sample M month labels with replacement from the M sorted confirmation month labels; include all observations from each sampled month including repeated blocks.",
        "generator": "Python random.Random(20260930); each draw uses randrange(M), M draws per replicate in sequential order.",
        "iterations": 20000, "seed": 20260930,
        "interval": "Two-sided percentile 95%; sorted replicate statistics with linear interpolation at positions (B-1)*0.025 and (B-1)*0.975.",
        "gate": "Lower endpoint must be strictly above zero, in addition to all registered confirmation magnitude and damage gates. No claim of independence across maturity series.",
        "limitation": "Ten historical month clusters give limited uncertainty resolution and do not establish out-of-period or competition performance."},
    "metrics_definition": {
        "MAE": "Unweighted arithmetic mean absolute error across paired eligible auction observations.",
        "IS90": "Current qfbench2_common 2.5.0 mean_interval_score at alpha=0.1 / level90 as its actual API specifies; verify the installed function signature before execution.",
        "coverage": "Fraction with lower<=actual<=upper.",
        "width": "Mean upper-lower.",
        "worst_outside_distance": "max(max(lower-actual, actual-upper, 0)) across paired observations.",
        "max_absolute_error": "Maximum absolute point error."},
    "stopping": ["Source or independent eligibility failure: no losses; metrics remain null.",
        "No selection alternative passes every gate: stop, retain median3, confirmation losses remain uncomputed.",
        "Chosen alternative fails any confirmation gate or its paired interval lower bound: stop, retain median3, do not search another method.",
        "Only a passing confirmation plus independent review and separate ChatGPT integration authorization may lead to participant changes."],
    "losses_already_computed": False, "participant_change_authorized": False}
addendum_sha = once("comparison-pre-registration-addendum-v1.json", addendum)
summary = {
    "experiment": "A4-AUC-v1", "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
    "stage": "Dataset frozen for independent provenance/as-of review; no prediction losses computed",
    "artifact_sha256": {name: sha(DIR / name) for name in ["pre-registration.json", "acquisition-plan.json", "normalized-dataset-v1.json", "causal-origins-v1.json", "release-provenance-v1.json", "acquisition-status-507-0.json", "comparison-pre-registration-addendum-v1.json"]},
    "source_sha256": {name: sha(ROOT / "project-evidence" / name) for name in ["t4_auc_release_20260930.py", "t4_auc_freeze_20260930.py", "t4_auc_finalize_20260930.py"]},
    "fresh_rules_audit": {"path": str(rules.relative_to(ROOT)), "sha256": sha(rules)},
    "counts": {"accepted_auctions": len(rows), "PDF_releases": 507, "index_excluded": len(plan["index_excluded"]),
        "normalization_excluded": len(dataset["excluded"]), "eligible_origins": len(eligible),
        "records_by_maturity": dict(Counter(str(r["original_maturity_years"]) for r in rows)),
        "records_by_split": dict(Counter(r["split"] for r in rows)),
        "eligible_origins_by_split": dict(Counter(o["split"] for o in eligible)),
        "selection_pairs": len(selection), "confirmation_pairs": len(confirm), "confirmation_months": months},
    "gates": {"release_hash_binding_and_dates_passed": True, "strict_prior_day_features_and_residuals_passed": True,
        "structural_minimum_sample_gates_passed": True, "independent_review_pending": True,
        "quality_gate": "not evaluated", "integration_authorized": False},
    "source_preservation_checks": source_checks,
    "metrics": {"MAE": None, "IS90": None, "coverage": None, "width": None, "worst_miss": None},
    "paired_losses_computed": False,
    "freeze_attempt_history": "Original freeze script successfully wrote causal origins and release provenance, then failed when locating a rules-audit JSON under an incorrect R0 subdirectory. Original script and outputs were preserved. This finalizer rechecks their hashes and causal invariants against the actual fresh audit before writing this summary.",
    "exposure": "Raw pre-cutoff values downloaded and parsed, warmup result/announcement inspected. No selection/confirmation method comparison; do not call the confirmation perfectly blind.",
    "limits": ["Archive claims the release day; no cryptographic proof of first uploaded bytes.",
        "Date-only chronology is conservative: same-day unknown-order results excluded from features.",
        "No correction markers observed; historical version archive is not known exhaustive.",
        "One preregistered maturity conflict excluded creates a warmup gap; no imputation.",
        "One auction ratio-history family only; no conclusion about competition score or unseen families.",
        "Release/auction hard ceiling 2024-10-31; no November2024 target labels acquired."]}
summary_sha = once("dataset-freeze-summary-v1.json", summary)
print(json.dumps({"summary_sha256": summary_sha, "addendum_sha256": addendum_sha, "counts": summary["counts"], "losses_computed": False}))
