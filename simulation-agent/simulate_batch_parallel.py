"""Isolated process candidate for the Track 3 ``simulate-batch`` verb.

Only the batch adapter changes. Each process calls the unmodified single-market
simulator, whose scenario seed and class counters are reset on every call.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import multiprocessing
import os
import pathlib
import tempfile
import time
from typing import Any, Optional

from abides_fork.simulate import simulate


def _run_one(sub_path: pathlib.Path, out_dir: pathlib.Path) -> dict[str, Any]:
    """Run one market with private ABIDES relative-log scratch space."""
    original_cwd = pathlib.Path.cwd()
    with tempfile.TemporaryDirectory(prefix=f"t3-{sub_path.stem}-", dir="/tmp") as scratch:
        try:
            os.chdir(scratch)
            ev = simulate(sub_path, out_dir / sub_path.stem / "trace.parquet")
        finally:
            os.chdir(original_cwd)
    return {
        "sub": sub_path.stem,
        "n_events": int(ev["n_events"]),
        "trace_sha256": ev["trace_sha256"],
        "gpu_seconds": float(ev.get("gpu_seconds", 0.0)),
    }


def _container_peak_bytes() -> int | None:
    """Read the whole container's concurrent memory peak, when cgroups expose it."""
    for name in (
        "/sys/fs/cgroup/memory.peak",  # cgroup v2
        "/sys/fs/cgroup/memory/memory.max_usage_in_bytes",  # cgroup v1
    ):
        try:
            peak = int(pathlib.Path(name).read_text().strip())
        except (OSError, ValueError):
            continue
        if peak > 0:
            return peak
    return None


def simulate_batch(
    batch_dir: str | pathlib.Path,
    out_dir: str | pathlib.Path,
    workers: int = 2,
) -> dict[str, Any]:
    """Run sorted independent markets with an explicit Linux ``fork`` pool."""
    if workers not in (1, 2, 4):
        raise ValueError("workers must be one of 1, 2, 4")
    batch_dir = pathlib.Path(batch_dir).resolve()
    out_dir = pathlib.Path(out_dir).resolve()
    subs = sorted(batch_dir.glob("*.json"))
    if not subs:
        raise SystemExit(f"simulate-batch: no sub-scenarios (*.json) found in {batch_dir}")

    # Python 3.11 on the pinned Linux image supports fork. The parent is single
    # threaded; it has imported the simulator but has not run any market. Fork
    # avoids repeating the large import cost in every worker. Process isolation
    # prevents any RNG, order-ID or message-ID state from leaking between markets.
    context = multiprocessing.get_context("fork")
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=min(workers, len(subs)), mp_context=context) as pool:
        futures = [pool.submit(_run_one, sub_path, out_dir) for sub_path in subs]
        # Consume in sorted sub order even when workers complete out of order.
        results = [future.result() for future in futures]
    wall_clock_sec = time.perf_counter() - t0

    total_events = sum(result["n_events"] for result in results)
    batch_events: dict[str, Any] = {
        "n_scenarios": len(subs),
        "total_events": total_events,
        "wall_clock_sec": float(wall_clock_sec),
        "events_per_sec": float(total_events / wall_clock_sec) if wall_clock_sec > 0 else 0.0,
        "gpu_seconds": sum(result["gpu_seconds"] for result in results),
        "per_scenario": [
            {key: result[key] for key in ("sub", "n_events", "trace_sha256")}
            for result in results
        ],
    }
    peak_memory_bytes = _container_peak_bytes()
    if peak_memory_bytes is not None:
        batch_events["peak_memory_bytes"] = peak_memory_bytes
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "batch_events.json").write_text(json.dumps(batch_events, indent=2) + "\n")
    return batch_events


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="abides_fork.simulate_batch")
    ap.add_argument("verb", nargs="?", default="simulate-batch", choices=["simulate-batch"])
    ap.add_argument("--batch-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--workers", type=int, choices=(1, 2, 4), default=2)
    args = ap.parse_args(argv)
    print(json.dumps(simulate_batch(args.batch_dir, args.out_dir, args.workers)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
