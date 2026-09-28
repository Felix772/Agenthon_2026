"""Diagnostic upper-bound probe for ABIDES' single-threaded priority queue.

This runs inside the exact release image but never imports or edits the simulator.
The event tuples match the shape used by Kernel.messages; timestamps are unique.
"""

import hashlib
import heapq
import json
import queue
import statistics
import time


def run(which, initial_count, event_count):
    events = [(i, (i % 128, (i + 1) % 128, None))
              for i in range(initial_count + event_count)]
    if which == "priority_queue":
        q = queue.PriorityQueue()
        for item in events[:initial_count]:
            q.put(item)
        start = time.perf_counter()
        popped = []
        for item in events[initial_count:]:
            assert not q.empty()
            popped.append(q.get()[0])
            q.put(item)
        popped.extend(q.get()[0] for _ in range(initial_count))
    else:
        q = list(events[:initial_count])
        heapq.heapify(q)
        start = time.perf_counter()
        popped = []
        for item in events[initial_count:]:
            assert q
            popped.append(heapq.heappop(q)[0])
            heapq.heappush(q, item)
        popped.extend(heapq.heappop(q)[0] for _ in range(initial_count))
    elapsed = time.perf_counter() - start
    digest = hashlib.sha256(json.dumps(popped).encode()).hexdigest()
    return elapsed, digest


results = []
for depth in (32, 1024, 32768):
    times = {"priority_queue": [], "heapq": []}
    expected = None
    for repeat in range(6):
        for which in (("priority_queue", "heapq") if repeat % 2 else
                      ("heapq", "priority_queue")):
            elapsed, digest = run(which, depth, 100000)
            if expected is None:
                expected = digest
            assert digest == expected
            if repeat:
                times[which].append(elapsed)
    baseline = statistics.median(times["priority_queue"])
    fast = statistics.median(times["heapq"])
    results.append({"depth": depth, "events": 100000,
                    "seconds": times, "median_queue_sec": baseline,
                    "median_heap_sec": fast, "fraction_saved": 1 - fast / baseline,
                    "ordering_sha256": expected})

print(json.dumps({"diagnostic_only": True, "results": results}, indent=2))
