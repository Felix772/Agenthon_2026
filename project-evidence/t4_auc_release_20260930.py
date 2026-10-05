"""Acquire dated Treasury releases for a registered audit; never compute model losses."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import urllib.request

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "project-evidence/t14-experiments/A4/auction-release-v1"
REG_SHA = "f712a3d20de1df1bc01fec47ce753e6ad8dd269a5fc6e0b52ad800e3db44418a"
BASE = "https://www.treasurydirect.gov/instit/annceresult/press/preanre/"
START, END = "2022-01-01", "2024-10-31"
TENORS = (2, 3, 5, 7, 10, 20, 30)
MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
DAY = MONTH + r"\s+\d{1,2},?\s+\d{4}"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def once(path, payload):
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"refuse overwrite: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)


def registration():
    raw = (DIR / "pre-registration.json").read_bytes()
    if sha(raw) != REG_SHA:
        raise ValueError("registration changed")
    return json.loads(raw)


def url_for(filename):
    match = re.fullmatch(r"(?:A|R|SPL)_(\d{8})_[A-Za-z0-9_-]+\.pdf", filename)
    if not match:
        raise ValueError(f"unrecognized filename: {filename}")
    day = datetime.strptime(match[1], "%Y%m%d").date().isoformat()
    if day > END:
        raise ValueError("release is after hard cutoff")
    return BASE + day[:4] + "/" + filename


def plan():
    registration()
    raw = (DIR / "metadata-index-2022-202410.json").read_bytes()
    all_rows = json.loads(raw)
    rows, locators, index_excluded = [], {}, []
    for row in all_rows:
        if row["z3a"] not in ("Note", "Bond") or row["t3a"] not in {f"{v}-Year" for v in TENORS}:
            continue
        auction = row["i"][:10]
        if not START <= auction <= END:
            raise ValueError("index escaped date bound")
        if row["d3"] != row["t3a"]:
            index_excluded.append({"cusip": row["a"], "auction_date": auction,
                "original_term": row["d3"], "normalized_term": row["t3a"],
                "reason": "Conflicting maturity metadata; excluded by registered whitelist before reading release values."})
            continue
        record = {
            "cusip": row["a"], "auction_date": auction, "announcement_date": row["h"][:10],
            "index_type": row["z3a"], "original_maturity_years": int(row["t3a"].split("-")[0]),
            "index_reported_term": row["d"], "announcement_pdf": row["e3"], "result_pdf": row["f3"],
            "special_pdf": [name.strip() for name in row.get("f32", "").split(",") if name.strip()],
            "index_updated_timestamp_not_first_availability": row.get("d4"),
        }
        for filename in [record["announcement_pdf"], record["result_pdf"], *record["special_pdf"]]:
            locators[filename] = url_for(filename)
        rows.append(record)
    rows.sort(key=lambda row: (row["auction_date"], row["original_maturity_years"], row["cusip"]))
    if len(rows) > 300 or len({(r["auction_date"], r["cusip"]) for r in rows}) != len(rows):
        raise ValueError("record cap or uniqueness failed")
    result = {
        "created_at_utc": now(), "registration_sha256": REG_SHA,
        "metadata_index_url": "https://www.treasurydirect.gov/TA_WS/securities/search?startDate=2022-01-01&endDate=2024-10-31&compact=true&dateFieldName=auctionDate&format=json",
        "metadata_index_sha256": sha(raw), "index_rows_received": len(all_rows),
        "index_caveat": "compact response includes numerical fields; request was after registration. These values are not used as target observations. Only locator/type/date fields enter this plan.",
        "eligible_auction_count": len(rows), "file_count": len(locators), "records": rows,
        "index_excluded": index_excluded,
        "locators": locators, "parser_source_sha256": sha(Path(__file__).read_bytes()),
    }
    once(DIR / "acquisition-plan.json", encoded(result))
    print(json.dumps({"auction_records": len(rows), "files": len(locators), "plan_sha256": sha(encoded(result))}))


def fetch_one(filename, url):
    raw_path, meta_path = DIR / "raw" / filename, DIR / "download-metadata" / (filename + ".json")
    if meta_path.exists():
        meta = json.loads(meta_path.read_bytes())
        if sha(raw_path.read_bytes()) != meta["raw_sha256"] or meta["source_locator"] != url:
            raise ValueError("download cache binding changed")
        return meta
    attempts = []
    for attempt in range(2):
        started = now()
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "HistoricalResearch/1.0"})
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = response.read(3_000_001)
                status = response.status
                headers = {key: response.headers.get(key) for key in ("Content-Type", "Last-Modified", "ETag")}
            if len(payload) > 3_000_000 or not payload.startswith(b"%PDF-"):
                raise ValueError("response is not a bounded PDF")
            once(raw_path, payload)
            meta = {"source_locator": url, "retrieved_at_utc": now(), "started_at_utc": started,
                    "http_status": status, "raw_sha256": sha(payload), "raw_bytes": len(payload),
                    "http_headers_not_publication_proof": headers, "attempts": attempts,
                    "license": "US Treasury government release; public domain; source retained",
                    "parser_source_sha256": sha(Path(__file__).read_bytes())}
            once(meta_path, encoded(meta))
            return meta
        except Exception as exc:
            attempts.append({"started_at_utc": started, "exception": type(exc).__name__, "message": str(exc)[:300]})
    return {"source_locator": url, "filename": filename, "error": attempts}


def fetch():
    registration()
    current = json.loads((DIR / "acquisition-plan.json").read_bytes())
    results, errors = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch_one, key, value): key for key, value in current["locators"].items()}
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            (errors if "error" in result else results).append(result)
            if i % 40 == 0:
                print(json.dumps({"downloaded_or_cached": i, "total": len(futures), "errors": len(errors)}), flush=True)
    report = {"completed_at_utc": now(), "downloaded_or_cached": len(results), "errors": errors,
              "file_count": len(current["locators"]), "registration_sha256": REG_SHA}
    once(DIR / f"acquisition-status-{len(results)}-{len(errors)}.json", encoded(report))
    print(json.dumps(report))


def extract(filename):
    raw = (DIR / "raw" / filename).read_bytes()
    metadata = json.loads((DIR / "download-metadata" / (filename + ".json")).read_bytes())
    if sha(raw) != metadata["raw_sha256"]:
        raise ValueError("raw release hash changed")
    reader = PdfReader(io.BytesIO(raw))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    once(DIR / "text" / (filename + ".txt"), text.encode())
    return text, dict(reader.metadata or {}), metadata


def day_value(text):
    return datetime.strptime(re.sub(r"\s+", " ", text).replace(",", ""), "%B %d %Y").date()


def field_date(text, name):
    match = re.search(re.escape(name) + r"\s+(" + DAY + ")", text)
    return day_value(match[1]) if match else None


def publication(text, meta):
    # Date is asserted on the official release, not synthesized from its filename/API timestamp.
    match = re.search(r"(?P<header>For Immediate Release|Embargoed Until [^\n]+).{0,100}?\n(?P<day>" + DAY + ")", text)
    if not match:
        raise ValueError("no dated release heading")
    day = day_value(match["day"])
    if day.isoformat() > END:
        raise ValueError("post-cutoff publication")
    creation = str(meta.get("/CreationDate", ""))
    creation_day = re.match(r"D:(\d{4})(\d{2})(\d{2})", creation)
    if not creation_day:
        raise ValueError("no PDF creation date")
    created = date(*(int(x) for x in creation_day.groups()))
    if created != day:
        raise ValueError("PDF creation differs from stated release date; possible replacement")
    modified = str(meta.get("/ModDate", ""))
    changed = re.match(r"D:(\d{4})(\d{2})(\d{2})", modified)
    if changed and date(*(int(x) for x in changed.groups())) > day:
        raise ValueError("PDF modified after its stated release date")
    return {"date": day.isoformat(), "publication_time": None,
            "release_heading": match["header"],
            "availability_bound": day.isoformat() + "T23:59:59.999999 America/New_York",
            "basis": "Official archive claims release on this date; conservative end-of-day availability. Embargo time, if present, is preserved in release_heading but is not independently proven upload time. PDF creation date agrees. Creation/API timestamps are not first-publication time.",
            "pdf_creation_timestamp": creation, "pdf_modification_timestamp": modified or None}


def normalize():
    registration()
    plan_data = json.loads((DIR / "acquisition-plan.json").read_bytes())
    records, excluded = [], []
    for spec in plan_data["records"]:
        try:
            text, pdfmeta, downloaded = extract(spec["result_pdf"])
            announce, annmeta, ann_download = extract(spec["announcement_pdf"])
            pub, annpub = publication(text, pdfmeta), publication(announce, annmeta)
            if pub["date"] != spec["auction_date"] or annpub["date"] != spec["announcement_date"]:
                raise ValueError("release heading and index dates disagree")
            if annpub["date"] >= pub["date"]:
                raise ValueError("announcement must precede result")
            if field_date(announce, "Auction Date") != date.fromisoformat(spec["auction_date"]):
                raise ValueError("announcement auction date differs")
            for source_text in (text, announce):
                found = re.search(r"CUSIP Number\s+(\w{9})", source_text)
                if not found or found[1] != spec["cusip"]:
                    raise ValueError("CUSIP missing or inconsistent")
                if re.search(r"Inflation-Protected|Floating Rate", source_text, re.I):
                    raise ValueError("excluded security type in PDF")
            term = re.search(r"Term and Type of Security\s+([^\n]+)", text)
            if not term:
                raise ValueError("security term missing")
            maturity = field_date(text, "Maturity Date")
            original = field_date(text, "Original Issue Date") or field_date(text, "Issue Date")
            if not maturity or not original:
                raise ValueError("original maturity cannot be independently derived")
            years = (maturity - original).days / 365.25
            closest = min(TENORS, key=lambda tenor: abs(tenor - years))
            if abs(closest - years) > .13 or closest != spec["original_maturity_years"]:
                raise ValueError("date-derived original maturity disagrees")
            btc = re.search(r"Bid[-–]to[-–]Cover Ratio:\s*\$?([\d,]+(?:\.\d+)?)\s*/\s*\$?([\d,]+(?:\.\d+)?)\s*=\s*(\d+(?:\.\d+)?)", text, re.I)
            if not btc:
                raise ValueError("bid-to-cover result footnote missing")
            tendered, accepted, value = (float(v.replace(",", "")) for v in btc.groups())
            if accepted <= 0 or abs(tendered / accepted - value) > .00501:
                raise ValueError("reported BTC differs from its stated arithmetic")
            correction_hits = []
            for name in spec["special_pdf"]:
                special, specialmeta, special_download = extract(name)
                correction_hits.append({"filename": name, "raw_sha256": special_download["raw_sha256"],
                    "mentions_correction": bool(re.search(r"correct(?:ion|ed)|revis(?:ion|ed)|supersed", special, re.I)),
                    "relation": "API associated special announcement; no assumption that it corrects the result"})
            if any(item["mentions_correction"] for item in correction_hits):
                raise ValueError("associated special release mentions correction; relation requires explicit vintage review")
            if re.search(r"correct(?:ion|ed)|revis(?:ion|ed)|supersed", text + "\n" + announce, re.I):
                raise ValueError("possible corrected result or announcement requires explicit vintage review")
            records.append({**spec, "reported_security_term": term[1].strip(),
                "date_derived_original_maturity_years": years,
                "publication": pub, "announcement_publication": annpub,
                "bid_to_cover_ratio": value, "bid_to_cover_numerator": tendered,
                "bid_to_cover_denominator": accepted, "raw_sha256": downloaded["raw_sha256"],
                "source_locator": downloaded["source_locator"], "retrieved_at_utc": downloaded["retrieved_at_utc"],
                "http_status": downloaded["http_status"],
                "announcement_raw_sha256": ann_download["raw_sha256"],
                "announcement_source_locator": ann_download["source_locator"],
                "license": downloaded["license"], "parser_source_sha256": sha(Path(__file__).read_bytes()),
                "correction_relation": "No explicit corrected/superseded marker in result; no claim of exhaustive unavailable versions.",
                "associated_special_releases": correction_hits,
                "split": "warmup" if spec["auction_date"] < "2023-07-01" else "selection" if spec["auction_date"] < "2024-01-01" else "confirmation",
                "eligible_for_review": True})
        except Exception as exc:
            excluded.append({**spec, "reason": str(exc), "exception": type(exc).__name__})
    output = {"created_at_utc": now(), "registration_sha256": REG_SHA,
              "acquisition_plan_sha256": sha((DIR / "acquisition-plan.json").read_bytes()),
              "parser_sha256": sha(Path(__file__).read_bytes()), "records": records, "excluded": excluded,
              "paired_losses_computed": False, "independent_review_pending": True,
              "historical_availability_limit": "Original dated official releases with conservative end-of-day availability. Web archive delivery today does not cryptographically prove first upload bytes; API updatedTimestamp is not used as first availability.",
              "integration_authorized": False}
    once(DIR / "normalized-dataset-v1.json", encoded(output))
    print(json.dumps({"records": len(records), "excluded": len(excluded), "dataset_sha256": sha(encoded(output)),
                      "exclusion_reasons": sorted({row["reason"] for row in excluded})}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("plan", "fetch", "normalize"))
    action = parser.parse_args().action
    {"plan": plan, "fetch": fetch, "normalize": normalize}[action]()
