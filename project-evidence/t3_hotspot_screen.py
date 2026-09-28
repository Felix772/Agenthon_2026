"""Read retained exact-image cProfile files without touching the simulator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import pstats


ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "t3-exact-profile-20260927-v2"
ROSTER = [
    "t3-eq-deterministic-baseline",
    "t3-gb-pop-128-agents",
    "t3-gbatch-dense-3",
    "t3-cancelmodify-lifecycle",
    "t3-as01-base-mix",
]

# Each named function's inclusive cost is an intentionally impossible upper
# bound: the required operation cannot be removed wholesale. Rows overlap and
# must never be added together. Self time is exclusive within cProfile.
TARGETS = {
    "kernel_send": ("abides_core/kernel.py", "send_message"),
    "latency_draw_and_clip": ("abides_fork/config.py", "get_latency"),
    "queue_put": ("queue.py", "put"),
    "queue_get": ("queue.py", "get"),
    "queue_empty": ("queue.py", "empty"),
    "exchange_receive": ("abides_markets/agents/exchange_agent.py", "receive_message"),
    "trace_extract": ("abides_fork/trace.py", "extract_trace"),
    "ledger_extract": ("abides_fork/trace.py", "extract_message_trace"),
    "log_parse": ("abides_core/utils.py", "parse_logs_df"),
    "price_level_sum": ("abides_markets/price_level.py", "total_quantity"),
    "order_to_dict": ("abides_markets/orders.py", "to_dict"),
    "order_str": ("abides_markets/orders.py", "__str__"),
    "fmt_ts": ("abides_core/utils.py", "fmt_ts"),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, default=PROFILE)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "t3-hotspot-screen-20260927.json")
    args = parser.parse_args()
    cases = []
    for unit in ROSTER:
        prof = args.profile / unit / "1-baseline" / "diagnostic" / "cpu.prof"
        stats = pstats.Stats(str(prof))
        functions = {}
        for label, (path, name) in TARGETS.items():
            matches = [(key, value) for key, value in stats.stats.items()
                       if key[0].endswith(path) and key[2] == name]
            if len(matches) != 1:
                raise ValueError((unit, label, len(matches)))
            key, value = matches[0]
            functions[label] = {
                "calls": value[1], "self_sec": value[2],
                "inclusive_sec": value[3], "file": key[0], "line": key[1],
            }
        cases.append({"unit": unit, "profile_cpu_sec": stats.total_tt,
                      "functions": functions})

    total = sum(c["profile_cpu_sec"] for c in cases)
    aggregate = {}
    for label in TARGETS:
        self_sec = sum(c["functions"][label]["self_sec"] for c in cases)
        inclusive_sec = sum(c["functions"][label]["inclusive_sec"] for c in cases)
        aggregate[label] = {
            "self_sec": self_sec, "self_fraction": self_sec / total,
            "inclusive_sec": inclusive_sec, "inclusive_fraction": inclusive_sec / total,
        }
    output = {
        "source": "Five existing exact-published-image cProfile runs; diagnostic only",
        "image": "sha256:c5724d34e2a8c951e4645b7c9aed1b95e3eabf1bd3dc24c0195055700cdbd45d",
        "source_directory": str(args.profile),
        "profile_cpu_total_sec": total,
        "caveat": "cProfile inflates per-call costs. Inclusive rows overlap, include required semantic work and are not additive or achievable speedups. No performance claim follows without a paired unprofiled experiment.",
        "cases": cases, "aggregate": aggregate,
    }
    dest = args.output
    dest.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    for label, row in sorted(aggregate.items(), key=lambda pair: -pair[1]["inclusive_fraction"]):
        print(f"{label:23s} self={100*row['self_fraction']:5.2f}% inclusive={100*row['inclusive_fraction']:5.2f}%")
    print(f"Wrote {dest}")


if __name__ == "__main__":
    main()
