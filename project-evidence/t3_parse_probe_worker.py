"""Diagnostic-only parse-logs probe mounted into the unchanged T3 release."""

import importlib
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

trace = importlib.import_module("abides_fork.trace")
simulate = importlib.import_module("abides_fork.simulate")
batch = importlib.import_module("abides_fork.simulate_batch")
original = trace.parse_logs_df
probes = []


def single_frame(end_state):
    records = []
    for agent in end_state["agents"]:
        for m in agent.log:
            row = {
                "EventTime": m[0] if isinstance(m[0], (int, np.int64)) else 0,
                "EventType": m[1],
            }
            event = m[2]
            if event == None:
                event = {"EmptyEvent": True}
            elif not isinstance(event, dict):
                event = {"ScalarEventValue": event}
            row.update(event)
            if row.get("agent_id") == None:
                row["agent_id"] = agent.id
            row["agent_type"] = agent.type
            records.append(row)
    return pd.DataFrame(records)


def checked_parse(end_state):
    probe = {}
    start = time.perf_counter()
    reference = original(end_state)
    probe["original_sec"] = time.perf_counter() - start
    start = time.perf_counter()
    alternate = single_frame(end_state)
    probe["single_frame_sec"] = time.perf_counter() - start
    probe["fraction_saved"] = 1 - probe["single_frame_sec"] / probe["original_sec"]
    probe["original_shape"] = reference.shape
    probe["single_frame_shape"] = alternate.shape
    probe["columns_identical"] = list(reference.columns) == list(alternate.columns)
    probe["dtypes_identical"] = (reference.dtypes.astype(str).tolist() ==
                                  alternate.dtypes.astype(str).tolist())
    probe["values_and_dtypes_identical"] = reference.reset_index(drop=True).equals(
        alternate.reset_index(drop=True))
    probes.append(probe)
    return reference


trace.parse_logs_df = checked_parse
start = time.perf_counter()
events = (batch.simulate_batch("/input/scenarios", "/output")
          if Path("/input/scenarios").exists()
          else simulate.simulate("/input/scenario.json", "/output/trace.parquet"))
report = {"process_sec": time.perf_counter() - start,
          "event_count": events.get("total_events", events.get("n_events")),
          "probes": probes,
          "original_sec": sum(p["original_sec"] for p in probes),
          "single_frame_sec": sum(p["single_frame_sec"] for p in probes),
          "all_values_and_dtypes_identical": all(p["values_and_dtypes_identical"] for p in probes)}
Path("/diagnostic/probe.json").write_text(json.dumps(report, indent=2) + "\n")
