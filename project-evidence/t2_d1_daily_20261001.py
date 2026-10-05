"""Freeze and evaluate three daily forecasting arms without changing production.

Historical row dates precede each task cutoff, but revision vintages and previously
inspected project outcomes prevent promotion. These are exploratory proxy scores.
"""

from collections import Counter, defaultdict
from contextlib import redirect_stdout
from datetime import datetime, timezone
import argparse
import csv
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
import tomllib

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "track2-forecasting-public"))
sys.path.insert(0, str(ROOT / "forecast-agent"))
import forecast as production
from joint_model import joint_samples
from online_model import _folds as previously_used_folds, _realized
from qfbench2_common.scoring import crps
from qfbench2_track_forecasting.scoring import _composite, _main as score_main
from qfbench2_track_forecasting.tail import tail_pinball

OUT = ROOT / "project-evidence/t2-d1-20261001"
STAGED = ROOT / "project-evidence/t2-monthly-trend-dev-roster-20260928"
ROSTER = ROOT / "project-evidence/t2-codabench-scored-950513-20260928.csv"
ARMS = ("incumbent", "vol63-half", "vol126-half")
SEEDS = (7101, 7102, 7103)
TAILS = (.01, .05, .95, .99)
REFS = {
    "Agenthon2026-public": "84221f1b553475b1283cb653145051e916377dd0",
    "track1-coding-public": "177058dff9630bc53456b21e35bc5a28a0ad02b6",
    "track2-forecasting-public": "3654fe3ced199df08862231a893c958e2a05ebef",
    "track3-simulation-public": "e9e42cc25fb840462f4cd97b23107b87619ddb85",
    "track4-analysis-public": "ede7381d8c1ba9d8c84068f9d142f5e093a33892",
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def load_unit(unit):
    directory = STAGED / "retry-inputs" / unit
    if not directory.is_dir():
        directory = STAGED / "inputs" / unit
    card = tomllib.loads((directory / "card.toml").read_text(encoding="utf-8"))
    cutoff = str(card["provenance"]["data_cutoff"])
    _, grid, spec = production.load_contract(directory / "panels", cutoff)
    histories = production.load_histories(directory / "panels", grid, cutoff)
    return directory, card, grid, spec, histories


def chronological_folds(histories, grid):
    """Keep five windows before the entire previously exposed three-fold region.

    Dates choose folds; values and candidate losses cannot affect membership.
    Each target window ends at least five complete observed rows before the
    next origin. Transfer-panel gaps greater than seven days are excluded.
    """
    wide = pd.DataFrame({a: histories[a] for a in grid.assets}).dropna()
    old = previously_used_folds(histories, grid, grid.horizons)
    if len(old) != 3:
        raise ValueError("Cannot identify all three previously exposed windows")
    horizon = int(max(grid.horizons))
    boundary = wide.index.get_loc(old[0][0].index[-1])
    rows = []
    for origin in range(boundary - horizon - 6, 299, -(horizon + 6)):
        dates = wide.index[origin:origin + horizon + 1]
        gaps = np.diff(dates.values).astype("timedelta64[D]").astype(int)
        if len(dates) != horizon + 1 or np.any(gaps > 7):
            continue
        rows.append({"origin": origin, "cutoff": str(dates[0].date()),
                     "target_start": str(dates[1].date()),
                     "target_end": str(dates[-1].date()),
                     "calendar_block": str(dates[-1].year)})
        if len(rows) == 5:
            break
    rows.reverse()
    for index, row in enumerate(rows):
        row["index"] = index
        row["phase"] = "inner" if index < 2 else "outer"
    return rows, [str(past.index[-1].date()) for past, _ in old]


def vol_samples(histories, grid, steps, *, arm, target, seed, draws=500):
    base, fit = joint_samples(histories, grid, steps, target=target, monthly=False,
                              draws=draws, seed=seed, method="gaussian",
                              shrinkage=.1, drift=False, window=252)
    return adjust_volatility(base, fit, histories, grid, steps, arm, target)


def adjust_volatility(base, fit, histories, grid, steps, arm, target):
    if arm not in ARMS:
        raise ValueError("Unregistered arm")
    if arm == "incumbent":
        return base, fit
    recent = 63 if arm == "vol63-half" else 126
    changes = {}
    for asset in grid.assets:
        s = histories[asset]
        if target == "log_return":
            if (s <= -1).any():
                raise ValueError("Invalid simple return")
            changes[asset] = np.log1p(s)
        else:
            gaps = np.diff(s.index.values).astype("timedelta64[D]").astype(int)
            changes[asset] = s.diff().where(np.r_[False, gaps <= 7])
    aligned = pd.DataFrame(changes).dropna().tail(252)
    if len(aligned) < 30:
        raise ValueError("Insufficient volatility history")
    old = aligned.var(ddof=1).to_numpy(dtype=float)
    new = aligned.tail(recent).var(ddof=1).to_numpy(dtype=float)
    scales = np.clip(np.sqrt((.5 * old + .5 * new) / np.maximum(old, 1e-12)), .75, 1.25)
    anchor = np.asarray([histories[a].iloc[-1] if target == "level" else 0.
                         for a in grid.assets], dtype=float)
    center = anchor[:, None] + np.asarray(fit["applied_drift"])[:, None] * steps
    samples = center[None] + (base - center[None]) * scales[None, :, None]
    return samples, {**fit, "daily_experiment": arm, "recent_rows": recent,
                     "variance_mix": .5, "scale_bounds": [.75, 1.25],
                     "marginal_scales": scales.tolist(),
                     "dependence": "same draws and correlation as incumbent"}


def components(samples, realized):
    flat = samples.reshape(len(samples), -1)
    y = realized.reshape(-1)
    parts = _composite(flat, y, weights=(.5, .3, .2), tail_levels=TAILS,
                       joint="variogram", tail_metric="pinball", ref_scale=None)
    return {**parts, "tail_by_level": {
        str(q): tail_pinball(flat, y, (q,)) for q in TAILS}}


def metric_slices(samples, realized, grid, target):
    result = {}
    masks = {"all": (list(range(len(grid.assets))), list(range(len(grid.horizons))))}
    for kind in ("rates_level", "fx_level", "factor_log_return"):
        indices = [i for i, a in enumerate(grid.assets)
                   if ("factor_log_return" if target == "log_return" else
                       "rates_level" if a.startswith("UST") else "fx_level") == kind]
        if indices:
            masks["class:" + kind] = (indices, list(range(len(grid.horizons))))
    for i, horizon in enumerate(grid.horizons):
        masks["horizon:" + str(horizon)] = (list(range(len(grid.assets))), [i])
    for name, (assets, horizons) in masks.items():
        subset = samples[:, assets][:, :, horizons]
        observed = realized[assets][:, horizons]
        result[name] = {"cells": len(assets) * len(horizons), "parts": components(subset, observed)}
    result["target:" + target] = result["all"]
    result["assets:single" if len(grid.assets) == 1 else "assets:multi"] = result["all"]
    result["cells:single" if len(grid.assets) * len(grid.horizons) == 1 else "cells:multi"] = result["all"]
    return result


def proxy(parts, scales, cells):
    weights = (5 / 7, 0., 2 / 7) if cells == 1 else (.5, .3, .2)
    value = sum(w * parts[k] / scales[k]
                for w, k in zip(weights, ("marginal", "joint", "tail")) if w)
    return float(min(4., value))


def choose_inner(records, cells):
    base = [r for r in records if r["arm"] == "incumbent"]
    indices = sorted({r["fold_index"] for r in base})
    if len(indices) != 2:
        return "incumbent", None
    scales = {k: max(float(np.mean([r["parts"][k] for r in base])), 1e-12)
              for k in ("marginal", "joint", "tail")}
    means = {arm: float(np.mean([proxy(r["parts"], scales, cells)
                                for r in records if r["arm"] == arm])) for arm in ARMS}
    arm = min(ARMS, key=lambda a: (means[a], ARMS.index(a)))
    wins = all(np.mean([proxy(r["parts"], scales, cells) for r in records
                         if r["arm"] == arm and r["fold_index"] == i])
               < np.mean([proxy(r["parts"], scales, cells) for r in base
                           if r["fold_index"] == i]) for i in indices)
    chosen = arm if means[arm] <= .98 * means["incumbent"] and wins else "incumbent"
    return chosen, scales


def group_names(card, grid):
    names = {"all", "target:" + card["targets"]["target_type"],
             "assets:single" if len(grid.assets) == 1 else "assets:multi",
             "cells:single" if len(grid.assets) * len(grid.horizons) == 1 else "cells:multi"}
    # Asset names classify reporting only; they never select a forecasting arm.
    kinds = {"rates_level" if a.startswith("UST") else "fx_level" for a in grid.assets}
    if card["targets"]["target_type"] == "log_return":
        kinds = {"factor_log_return"}
    names.update("class:" + k for k in kinds)
    names.update("horizon:" + str(h) for h in grid.horizons)
    return sorted(names)


def freeze():
    OUT.mkdir(exist_ok=False)
    with ROSTER.open(newline="") as f:
        # Only the identifier column is read. No Development score is used.
        units = [r["unit"] for r in csv.DictReader(f)]
    if len(units) != 71 or len(set(units)) != 71:
        raise ValueError("Expected exactly 71 unique inputs")
    prior = json.loads((STAGED / "report.json").read_text())
    passed = {r["unit"]: r for r in prior["records"] if r["status"] == "passed"}
    official_repo = ROOT / "track2-forecasting-public"
    official_ref = REFS["track2-forecasting-public"]
    head = subprocess.check_output(["git", "-C", str(official_repo), "rev-parse", "HEAD"], text=True).strip()
    if head != official_ref:
        raise ValueError("Executable T2 checkout is not the freshly reviewed commit")
    package_paths = subprocess.check_output(["git", "-C", str(official_repo), "ls-tree", "-r", "--name-only",
                                            official_ref, "qfbench2_track_forecasting"], text=True).splitlines()
    for name in (p for p in package_paths if p.endswith(".py")):
        expected = subprocess.check_output(["git", "-C", str(official_repo), "show", f"{official_ref}:{name}"])
        if (official_repo / name).read_bytes().replace(b"\r\n", b"\n") != expected:
            raise ValueError("Executable official package differs from committed source")
    docs = {}
    for repo, ref in REFS.items():
        repo_dir = ROOT / repo
        actual = subprocess.check_output(["git", "-C", str(repo_dir), "rev-parse", "origin/main"], text=True).strip()
        if actual != ref:
            raise ValueError("Upstream changed; consult instructions again")
        paths = ["README.md", "AGENTS.md", "CONTRIBUTING.md"]
        if repo == "track2-forecasting-public":
            paths += ["SUBMISSION_CLI.md", "docs/ARTIFACT-POLICY.md", "docs/M0-BASELINE.md"]
        docs[repo] = {"sha": ref, "consulted": {p: hashlib.sha256(subprocess.check_output(
            ["git", "-C", str(repo_dir), "show", f"{ref}:{p}"])).hexdigest() for p in paths}}
    rows = []
    for unit in units:
        directory, card, grid, _, histories = load_unit(unit)
        scoring_card = ROOT / ".validation/t2-current-20260928/units" / unit / "card.toml"
        current_card = subprocess.check_output(["git", "-C", str(official_repo), "show",
                                               f"{official_ref}:units/{unit}/card.toml"])
        if scoring_card.read_bytes() != current_card:
            raise ValueError("Scoring card is not byte-identical to current official source: " + unit)
        files = []
        for member in passed[unit]["staged_files"]:
            p = directory / member["path"]
            if p.is_symlink() or digest(p) != member["sha256"]:
                raise ValueError("Previously verified input changed")
            files.append({"path": p.relative_to(ROOT).as_posix(), "sha256": digest(p)})
        folds, old = chronological_folds(histories, grid)
        rows.append({"unit": unit, "cutoff": str(card["provenance"]["data_cutoff"]),
                     "target": card["targets"]["target_type"], "assets": list(grid.assets),
                     "horizons": list(grid.horizons), "cells": len(grid.assets) * len(grid.horizons),
                     "scoring_card_sha256_current": digest(scoring_card),
                     "groups": group_names(card, grid), "files": files, "folds": folds,
                     "previously_exposed_origins_excluded": old,
                     "enough_folds": len(folds) >= 3})
    source_paths = [Path(__file__), ROOT / "forecast-agent/forecast.py",
                    ROOT / "forecast-agent/joint_model.py", ROOT / "forecast-agent/online_model.py",
                    ROOT / "forecast-agent/text_events.py", Path(crps.__file__),
                    ROOT / "track2-forecasting-public/qfbench2_track_forecasting/scoring.py",
                    ROOT / "track2-forecasting-public/qfbench2_track_forecasting/tail.py"]
    plan = {"experiment": "T2-D1", "created_utc": datetime.now(timezone.utc).isoformat(),
            "upstream": docs, "roster_sha256": digest(ROSTER), "roster_score_fields_used": False,
            "executed_t2_head": head, "executed_t2_package_matches_commit": True,
            "current_scoring_cards_byte_verified": 71,
            "source_hashes": {str(p): digest(p) for p in source_paths},
            "incumbent_image": "sha256:461ad3fb69becc4084744879ba8f9bf53ae7e4de64f49785f14fae3a217aa8a3",
            "python": sys.version, "platform": platform.platform(),
            "packages": {k: importlib.metadata.version(k) for k in
                         ("numpy", "pandas", "pyarrow", "qfbench2-common")},
            "arms": {"incumbent": "unchanged Gaussian 252 innovations, .1 shrinkage, level drift zero",
                     "vol63-half": "50% 252-row +50% 63-row marginal variance, scale clipped .75..1.25",
                     "vol126-half": "50% 252-row +50% 126-row marginal variance, scale clipped .75..1.25"},
            "all_arms": "Same incumbent drift, sample shocks and cross-asset/horizon correlation; no task-ID branches",
            "draws": 500, "seeds": list(SEEDS), "inner_selection": "oldest two folds; >=2% mean gain and win both; else incumbent",
            "outer_evaluation": "remaining up to three folds, frozen before losses; no outer veto or retuning",
            "proxy": "inner-incumbent mean raw component denominators; single-cell redistributed weights; cap4",
            "uncertainty": "paired bootstrap of calendar target-end year blocks, 20000 samples, seed20261001; descriptive only",
            "gates": {"relative_gain": .02, "group_max_degradation": .02, "bootstrap_lower_bound": 0.,
                      "minimum_calendar_blocks": 5, "complete_roster": 71, "vintages_certified": False},
            "promotion_blockers": ["Historical release/revision vintages absent", "Prior project outcomes already inspected; new folds are not globally untouched"],
            "no_external_data": True, "no_house_calls": True, "no_publication_or_upload": True,
            "units": rows}
    save(OUT / "plan.json", plan)
    print(json.dumps({"frozen": 71, "fold_counts": dict(Counter(len(r["folds"]) for r in rows)),
                      "quality_eligible": sum(r["enough_folds"] for r in rows)}, indent=2), flush=True)


def read_plan():
    plan = json.loads((OUT / "plan.json").read_text())
    for path, expected in plan["source_hashes"].items():
        if digest(path) != expected:
            raise ValueError("Frozen experiment source changed: " + path)
    return plan


def evaluate():
    plan = read_plan()
    records_path = OUT / "metrics.jsonl"
    done = set()
    if records_path.exists():
        done = {(r["unit"], r["fold_index"], r["seed"], r["arm"])
                for r in map(json.loads, records_path.read_text().splitlines())}
    with records_path.open("a", encoding="utf-8") as output:
        for row in plan["units"]:
            _, card, grid, _, histories = load_unit(row["unit"])
            wide = pd.DataFrame(histories).dropna()
            for fold in row["folds"]:
                past = wide.iloc[:fold["origin"] + 1]
                future = wide.iloc[fold["origin"] + 1:fold["origin"] + 1 + max(grid.horizons)]
                if str(past.index[-1].date()) != fold["cutoff"] or str(future.index[-1].date()) != fold["target_end"]:
                    raise ValueError("Frozen dates changed")
                hist = {a: past[a] for a in grid.assets}
                for seed in SEEDS:
                    # Generate every arm from training data before accessing outcomes.
                    steps = np.tile(grid.horizons, (len(grid.assets), 1))
                    base, fit = vol_samples(hist, grid, steps, arm="incumbent", target=row["target"], seed=seed)
                    predictions = {arm: adjust_volatility(base, fit, hist, grid, steps, arm, row["target"])[0]
                                   for arm in ARMS}
                    y = _realized(future, grid, row["target"])
                    for arm, samples in predictions.items():
                        key = (row["unit"], fold["index"], seed, arm)
                        if key in done:
                            continue
                        record = {"unit": row["unit"], "fold_index": fold["index"], "phase": fold["phase"],
                                  "cutoff": fold["cutoff"], "target_end": fold["target_end"],
                                  "calendar_block": fold["calendar_block"], "seed": seed, "arm": arm,
                                  "parts": components(samples, y),
                                  "slices": metric_slices(samples, y, grid, row["target"]),
                                  "prediction_sha256": hashlib.sha256(samples.tobytes()).hexdigest()}
                        output.write(json.dumps(record, allow_nan=False) + "\n")
                        output.flush()
            print("EVALUATED", row["unit"], len(row["folds"]), "folds", flush=True)
            if (OUT / "STOP").exists():
                print("STOP requested at card boundary", flush=True)
                return


def contracts():
    plan = read_plan()
    report_path = OUT / "contracts.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else {"records": []}
    done = {(r["unit"], r["arm"]) for r in report["records"]}
    for row in plan["units"]:
        directory, card, grid, _, _ = load_unit(row["unit"])
        for arm in ARMS:
            if (row["unit"], arm) in done:
                continue
            out = OUT / "outputs" / row["unit"] / arm
            original = production.joint_samples

            def experimental(histories, grid, steps, **kwargs):
                base, fit = original(histories, grid, steps, **kwargs)
                return adjust_volatility(base, fit, histories, grid, steps, arm, kwargs["target"])

            start = time.perf_counter()
            record = {"unit": row["unit"], "arm": arm, "passed": False}
            try:
                production.joint_samples = experimental
                stream = io.StringIO()
                with redirect_stdout(stream):
                    code = production.main(["forecast", "--panels", str(directory / "panels"),
                                            "--text", str(directory / "text"), "--asof", row["cutoff"],
                                            "--out", str(out / "forecast.parquet"), "--seed", str(SEEDS[0])])
                record["forecast_stdout"] = stream.getvalue()
                stream = io.StringIO()
                with redirect_stdout(stream):
                    gate_code = score_main(["score", "--card", str(ROOT / ".validation/t2-current-20260928/units" / row["unit"] / "card.toml"),
                                            "--forecast", str(out / "forecast.parquet")])
                verdict = json.loads(stream.getvalue())
                record.update(passed=code == 0 and gate_code == 0 and verdict["admissible"], verdict=verdict,
                              output_hashes={p.name: digest(p) for p in out.iterdir()},
                              output_bytes=sum(p.stat().st_size for p in out.iterdir()))
            except Exception as exc:
                record.update(error_type=type(exc).__name__, error=str(exc))
            finally:
                production.joint_samples = original
            record["elapsed_seconds"] = time.perf_counter() - start
            report["records"].append(record)
            save(report_path, report)
            print("CONTRACT", row["unit"], arm, record["passed"], flush=True)
        if (OUT / "STOP").exists():
            print("STOP requested at card boundary", flush=True)
            return
    report["passed_by_arm"] = {a: sum(r["passed"] for r in report["records"] if r["arm"] == a) for a in ARMS}
    report["scope"] = "Host CLI and official current T2 g0-g3 only; no Docker/resource/isolation certification"
    save(report_path, report)


def summarize():
    plan = read_plan()
    records = [json.loads(s) for s in (OUT / "metrics.jsonl").read_text().splitlines()]
    paired = []
    selections = []
    for row in plan["units"]:
        own = [r for r in records if r["unit"] == row["unit"]]
        expected = len(row["folds"]) * len(SEEDS) * len(ARMS)
        if len(own) != expected:
            raise ValueError("Incomplete frozen metric denominator")
        selected, scales = choose_inner([r for r in own if r["phase"] == "inner"], row["cells"])
        selections.append({"unit": row["unit"], "arm": selected, "scales": scales,
                           "outer_folds": max(0, len(row["folds"]) - 2)})
        for fold in (f for f in row["folds"] if f["phase"] == "outer"):
            subset = [r for r in own if r["fold_index"] == fold["index"]]
            for arm in (*ARMS[1:], "inner-selector"):
                chosen = selected if arm == "inner-selector" else arm
                for group in row["groups"]:
                    inner_base = [r["slices"][group] for r in own if r["phase"] == "inner" and r["arm"] == "incumbent"]
                    group_scales = {k: max(float(np.mean([r["parts"][k] for r in inner_base])), 1e-12)
                                    for k in ("marginal", "joint", "tail")}
                    cells = inner_base[0]["cells"]
                    controls = [r["slices"][group] for r in subset if r["arm"] == "incumbent"]
                    candidates = [r["slices"][group] for r in subset if r["arm"] == chosen]
                    base = np.mean([proxy(r["parts"], group_scales, cells) for r in controls])
                    candidate = np.mean([proxy(r["parts"], group_scales, cells) for r in candidates])
                    paired.append({"unit": row["unit"], "arm": arm, "fold": fold["index"],
                                   "block": fold["calendar_block"], "groups": [group],
                                   "baseline": float(base), "candidate": float(candidate),
                                   "improvement": float(base - candidate),
                                   "raw_baseline": {k: float(np.mean([r["parts"][k] for r in controls])) for k in ("marginal", "joint", "tail")},
                                   "raw_candidate": {k: float(np.mean([r["parts"][k] for r in candidates])) for k in ("marginal", "joint", "tail")},
                                   "tail_baseline": {str(q): float(np.mean([r["parts"]["tail_by_level"][str(q)] for r in controls])) for q in TAILS},
                                   "tail_candidate": {str(q): float(np.mean([r["parts"]["tail_by_level"][str(q)] for r in candidates])) for q in TAILS}})
    groups = {}
    for arm in (*ARMS[1:], "inner-selector"):
        groups[arm] = {}
        for group in sorted({g for r in paired for g in r["groups"]}):
            rows = [r for r in paired if r["arm"] == arm and group in r["groups"]]
            units = defaultdict(list)
            blocks = defaultdict(list)
            for r in rows:
                units[r["unit"]].append(r)
                blocks[r["block"]].append(r["improvement"])
            base = float(np.mean([np.mean([r["baseline"] for r in rs]) for rs in units.values()]))
            candidate = float(np.mean([np.mean([r["candidate"] for r in rs]) for rs in units.values()]))
            block_values = np.asarray([np.mean(v) for v in blocks.values()])
            rng = np.random.default_rng(20261001)
            boot = block_values[rng.integers(0, len(block_values), (20000, len(block_values)))].mean(axis=1)
            groups[arm][group] = {"cards": len(units), "outer_folds": len(rows), "calendar_blocks": len(blocks),
                                  "baseline_proxy": base, "candidate_proxy": candidate,
                                  "relative_gain": 1. - candidate / base,
                                  "block_mean_improvement": float(block_values.mean()),
                                  "block_improvement_ci95": np.quantile(boot, [.025, .975]).tolist(),
                                  **{field: {k: float(np.mean([np.mean([r[field][k] for r in rs]) for rs in units.values()]))
                                             for k in rows[0][field]} for field in
                                     ("raw_baseline", "raw_candidate", "tail_baseline", "tail_candidate")}}
    contract_path = OUT / "contracts.json"
    contracts_report = json.loads(contract_path.read_text()) if contract_path.exists() else {}
    result = {"experiment": "T2-D1", "records": len(records), "quality_cards": sum(r["outer_folds"] > 0 for r in selections),
              "selections": selections, "groups": groups, "paired": paired,
              "contracts": contracts_report.get("passed_by_arm", {}),
              "promote": False, "decision": "retain 950513; exploratory only",
              "promotion_blockers": plan["promotion_blockers"],
              "notes": ["Official M0 normalization scales are sealed; these scores are proxies", "Class/horizon rows score their actual cell subsets using inner-only group denominators", "relative_gain averages folds within card then weights cards equally; block_mean_improvement/CI weights calendar years equally and is a distinct descriptive estimand", "Calendar blocks and cards share histories; bootstrap is descriptive, not independence certification"]}
    save(OUT / "results.json", result)
    print(json.dumps({k: result[k] for k in ("records", "quality_cards", "contracts", "promote", "decision")}, indent=2), flush=True)
    for arm in groups:
        print(arm, json.dumps(groups[arm]["all"]), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("freeze", "evaluate", "contracts", "summarize"))
    args = parser.parse_args()
    globals()[args.phase]()
