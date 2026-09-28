"""Focused process-isolation test; run with Linux Python, no ABIDES install needed."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types


def fake_simulate(config_path: Path, out_path: Path) -> dict:
    scenario = json.loads(config_path.read_text())
    time.sleep(0.08)  # Ensure overlapping work is available to the worker pool.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    content = f"{scenario['id']}:{scenario['seed']}\n".encode()
    out_path.write_bytes(content)
    (out_path.parent / "message_trace.parquet").write_bytes(content + b"messages\n")
    (out_path.parent / "worker.json").write_text(
        json.dumps({"pid": os.getpid(), "cwd": os.getcwd()})
    )
    result = {
        "n_events": scenario["seed"],
        "trace_sha256": hashlib.sha256(content).hexdigest(),
        "gpu_seconds": 0.0,
    }
    (out_path.parent / "events.json").write_text(json.dumps(result))
    return result


def load_candidate():
    package = types.ModuleType("abides_fork")
    package.__path__ = []
    single = types.ModuleType("abides_fork.simulate")
    single.simulate = fake_simulate
    sys.modules["abides_fork"] = package
    sys.modules["abides_fork.simulate"] = single
    path = Path(__file__).with_name("simulate_batch_parallel.py")
    spec = importlib.util.spec_from_file_location("abides_fork.simulate_batch", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    module = load_candidate()
    with tempfile.TemporaryDirectory() as root:
        root = Path(root)
        inputs = root / "scenarios"
        inputs.mkdir()
        names = ("zeta", "alpha", "mu", "beta", "eta", "gamma")
        for i, name in enumerate(names, 1):
            (inputs / f"{name}.json").write_text(json.dumps({"id": name, "seed": i}))
        hashes = []
        for workers in (1, 2, 4):
            output = root / f"out-{workers}"
            result = module.simulate_batch(inputs, output, workers)
            assert [row["sub"] for row in result["per_scenario"]] == sorted(names)
            assert result["total_events"] == sum(range(1, len(names) + 1))
            assert result["events_per_sec"] == result["total_events"] / result["wall_clock_sec"]
            observed = []
            pids = set()
            scratch = set()
            for name in sorted(names):
                sub = output / name
                meta = json.loads((sub / "worker.json").read_text())
                pids.add(meta["pid"])
                scratch.add(meta["cwd"])
                assert not Path(meta["cwd"]).exists(), "worker scratch must be removed"
                observed.append(
                    (
                        (sub / "trace.parquet").read_bytes(),
                        (sub / "message_trace.parquet").read_bytes(),
                    )
                )
            assert len(scratch) == len(names), "each market needs separate scratch space"
            assert len(pids) == workers, "all requested workers should perform markets"
            hashes.append(observed)
        assert hashes[0] == hashes[1] == hashes[2], "stable output changed with scheduling"
    print("batch process isolation, sorted aggregate and stable outputs: PASS")


if __name__ == "__main__":
    main()
