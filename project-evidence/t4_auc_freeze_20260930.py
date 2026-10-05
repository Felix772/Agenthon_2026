"""Freeze a dated-release dataset and causal origin roster without computing losses."""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "project-evidence/t14-experiments/A4/auction-release-v1"
EXPECTED_DATASET = "8a5f2ca1ff0380722c758102ccbd8316afe247c5b2764aeda31224ee7202a22b"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name):
    return json.loads((DIR / name).read_bytes())


def save(name, value):
    path = DIR / name
    data = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    if path.exists():
        raise ValueError(f"refuse overwrite: {path}")
    path.write_bytes(data)
    return sha(path)


def record_id(row):
    return f"{row['auction_date']}:{row['cusip']}"


def main():
    dataset_path = DIR / "normalized-dataset-v1.json"
    assert sha(dataset_path) == EXPECTED_DATASET
    dataset, plan, registration = load(dataset_path.name), load("acquisition-plan.json"), load("pre-registration.json")
    assert dataset["paired_losses_computed"] is False
    assert not dataset["excluded"]
    rows = dataset["records"]
    assert len(rows) == 237 and len({record_id(row) for row in rows}) == len(rows)
    by_id = {record_id(row): row for row in rows}
    observed_sources = {}
    for path, expected in registration["participant_source_bindings"].items():
        actual = sha(ROOT / path)
        assert actual == expected, f"participant source changed: {path}"
        observed_sources[path] = actual
    for row in rows:
        assert "2022-01-01" <= row["auction_date"] == row["publication"]["date"] <= "2024-10-31"
        assert row["announcement_publication"]["date"] < row["publication"]["date"]
        assert row["index_type"] in ("Note", "Bond")
        assert row["original_maturity_years"] in (2, 3, 5, 7, 10, 20, 30)
        assert not any(item["mentions_correction"] for item in row["associated_special_releases"])
    origins = []
    for target in rows:
        origin_day = target["announcement_publication"]["date"]
        history = sorted((row for row in rows if row["original_maturity_years"] == target["original_maturity_years"]
                          and row["publication"]["date"] < origin_day), key=lambda row: row["publication"]["date"])
        past_targets = []
        for past in history:
            past_history = [row for row in history if row["publication"]["date"] < past["announcement_publication"]["date"]]
            if len(past_history) >= 3:
                past_targets.append({"past_target_record_id": record_id(past),
                                     "past_origin_day": past["announcement_publication"]["date"],
                                     "past_feature_record_ids": [record_id(row) for row in past_history]})
        origins.append({"target_record_id": record_id(target), "target_auction_date": target["auction_date"],
                        "target_result_available_date": target["publication"]["date"],
                        "original_maturity_years": target["original_maturity_years"],
                        "origin_announcement_date": origin_day, "split": target["split"],
                        "feature_record_ids": [record_id(row) for row in history],
                        "feature_count": len(history), "interval_residual_roster": past_targets,
                        "eligible_history": len(history) >= 6})
    for origin in origins:
        assert origin["target_record_id"] not in origin["feature_record_ids"]
        for feature in origin["feature_record_ids"]:
            assert by_id[feature]["publication"]["date"] < origin["origin_announcement_date"]
        for residual in origin["interval_residual_roster"]:
            assert residual["past_target_record_id"] not in residual["past_feature_record_ids"]
            for feature in residual["past_feature_record_ids"]:
                assert by_id[feature]["publication"]["date"] < residual["past_origin_day"]
    origin_sha = save("causal-origins-v1.json", {
        "dataset_sha256": EXPECTED_DATASET, "origins": origins,
        "policy": "Feature result release date must be strictly before target announcement date. Unknown same-day order is excluded. Each past residual prediction also uses its own origin's prior releases.",
        "prediction_values_or_losses_computed": False})
    releases = []
    for filename, locator in plan["locators"].items():
        raw_path = DIR / "raw" / filename
        metadata_path = DIR / "download-metadata" / (filename + ".json")
        text_path = DIR / "text" / (filename + ".txt")
        meta = json.loads(metadata_path.read_bytes())
        assert sha(raw_path) == meta["raw_sha256"] and locator == meta["source_locator"]
        assert meta["http_status"] == 200
        linked = []
        for row in rows:
            role = "result" if row["result_pdf"] == filename else "announcement" if row["announcement_pdf"] == filename else "special" if filename in row["special_pdf"] else None
            if role:
                linked.append({"auction_record_id": record_id(row), "auction_date": row["auction_date"],
                    "original_maturity_years": row["original_maturity_years"], "security_type": row["index_type"],
                    "role": role, "bid_to_cover_ratio": row["bid_to_cover_ratio"] if role == "result" else None,
                    "ratio_null_reason": None if role == "result" else "Not a result release; not used as a target or feature value."})
        assert linked
        text = text_path.read_text(encoding="utf-8")
        pdfmeta = dict(PdfReader(raw_path).metadata or {})
        date_match = re.search(r"(?:For Immediate Release|Embargoed Until)[^\n]*\n([A-Za-z]+\s+\d{1,2},?\s+\d{4})", text, re.I)
        assert date_match, filename
        stated = datetime.strptime(re.sub(r"\s+", " ", date_match[1]).replace(",", ""), "%B %d %Y").date().isoformat()
        assert stated <= "2024-10-31"
        correction = bool(re.search(r"correct(?:ion|ed)|revis(?:ion|ed)|supersed", text, re.I))
        assert not correction, filename
        releases.append({**meta, "filename": filename, "download_metadata_sha256": sha(metadata_path),
            "extracted_text_sha256": sha(text_path), "release_date_claimed_in_heading": stated,
            "publication_time_verified": None, "pdf_creation_timestamp": pdfmeta.get("/CreationDate"),
            "pdf_modification_timestamp": pdfmeta.get("/ModDate"), "linked_auctions": linked,
            "correction_marker": correction,
            "correction_relation": "No explicit correction marker found in this release. This is not an exhaustive archive of all historical versions.",
            "normalization_parser_sha256": dataset["parser_sha256"],
            "freeze_parser_sha256": sha(Path(__file__))})
    release_sha = save("release-provenance-v1.json", {
        "dataset_sha256": EXPECTED_DATASET, "release_count": len(releases), "releases": releases,
        "license_basis": {"attribution": "U.S. Department of the Treasury, TreasuryDirect auction announcements/results",
            "notice": "US federal government factual releases; public domain. Treasury marks/logos have separate trademark rules and are not used in participant artifacts.",
            "competition_source": ".validation/t4-origin-5.2.0-20260929/THIRD-PARTY-NOTICES.md",
            "competition_source_sha256": sha(ROOT / ".validation/t4-origin-5.2.0-20260929/THIRD-PARTY-NOTICES.md"),
            "primary_reference": "https://www.copyright.gov/history/copyright-exhibit/lifecycle/"},
        "first_availability_limit": "Archive claims publication on the heading date; the live downloaded bytes are not cryptographic proof of the first uploaded version. API updatedTimestamp/HTTP Last-Modified/PDF CreationDate are never used as first-publication timestamps."})
    eligible = [row for row in origins if row["eligible_history"]]
    selection = [row for row in eligible if row["split"] == "selection"]
    confirm = [row for row in eligible if row["split"] == "confirmation"]
    months = sorted({row["target_auction_date"][:7] for row in confirm})
    structural = len(confirm) >= 30 and len(months) >= 6 and len({row["original_maturity_years"] for row in confirm}) >= 3
    assert structural and len(selection) == 42 and len(confirm) == 70
    summary = {
        "experiment": "A4-AUC-v1", "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "stage": "Dataset frozen for independent as-of/provenance review; no model losses computed",
        "preregistration_sha256": sha(DIR / "pre-registration.json"),
        "acquisition_plan_sha256": sha(DIR / "acquisition-plan.json"),
        "normalized_dataset_sha256": EXPECTED_DATASET, "causal_origins_sha256": origin_sha,
        "release_provenance_sha256": release_sha, "acquisition_status_sha256": sha(DIR / "acquisition-status-507-0.json"),
        "acquisition_parser_sha256": sha(ROOT / "project-evidence/t4_auc_release_20260930.py"),
        "freeze_parser_sha256": sha(Path(__file__)),
        "fresh_rules_audit_sha256": sha(ROOT / "project-evidence/t14-experiments/R0/rules-refresh-20260930T005045Z.json"),
        "counts": {"records": len(rows), "release_pdfs": len(releases), "index_excluded": len(plan["index_excluded"]),
            "normalization_excluded": len(dataset["excluded"]), "eligible_origins": len(eligible),
            "records_by_maturity": dict(Counter(str(row["original_maturity_years"]) for row in rows)),
            "records_by_split": dict(Counter(row["split"] for row in rows)),
            "eligible_origins_by_split": dict(Counter(row["split"] for row in eligible)),
            "selection_pairs": len(selection), "confirmation_pairs": len(confirm), "confirmation_months": months},
        "gates": {"date_maturity_type_and_correction_checks_passed": True,
            "strict_prior_day_causal_features_and_residuals_passed": True,
            "minimum_structural_sample_gates_passed": structural, "independent_review_pending": True,
            "quality_promotion_gate": "not evaluated", "participant_integration_authorized": False},
        "metrics": {"MAE": None, "IS90": None, "coverage": None, "width": None, "worst_miss": None},
        "paired_losses_computed": False,
        "source_preservation_checks": observed_sources,
        "exposure": "Raw pre-cutoff values were downloaded and parsed. A warmup sample was visually inspected. No selection/confirmation prediction comparison was run; do not describe the calendar confirmation as perfectly blind.",
        "limitations": ["Official dated archive evidence is not cryptographic first-upload proof.",
            "Publication times are not independently verified; features must be from an earlier calendar day.",
            "Possible correction markers in result, announcement, or associated special release are quarantined; none observed in the accepted release set.",
            "One metadata conflict excluded before release reading may create a warmup gap; no rows are filled or interpolated.",
            "Single ratio-history family research does not establish official competition score or unseen-family improvement.",
            "All release dates and auction dates are no later than 2024-10-31; no November2024 target labels acquired."]}
    result_sha = save("dataset-freeze-summary-v1.json", summary)
    print(json.dumps({"summary_sha256": result_sha, "counts": summary["counts"], "losses_computed": False}))


if __name__ == "__main__":
    main()
