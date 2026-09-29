"""Exercise the installed queue's exact ordering, including timestamp ties."""

from __future__ import annotations

import random
from queue import PriorityQueue

from abides_core.kernel import _SingleOwnerEventQueue


def test_matches_priority_queue_under_interleaved_operations():
    rng = random.Random(20260928)
    baseline = PriorityQueue()
    candidate = _SingleOwnerEventQueue()
    next_id = 0
    for _ in range(100_000):
        if baseline.empty() or rng.random() < 0.65:
            item = (rng.randrange(0, 23), (rng.randrange(0, 8), next_id))
            next_id += 1
            baseline.put(item)
            candidate.put(item)
        else:
            assert candidate.get() == baseline.get()
        assert candidate.empty() == baseline.empty()
        assert len(candidate.queue) == len(baseline.queue)
    while not baseline.empty():
        assert candidate.get() == baseline.get()
    assert candidate.empty()
