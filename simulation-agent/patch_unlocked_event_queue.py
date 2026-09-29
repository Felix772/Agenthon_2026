"""Install a single-owner event heap over the exact admitted ABIDES kernel.

The kernel creates, fills, and drains one queue within a single market process.
There are no producer threads: independent markets use separate processes.
The standard PriorityQueue uses the same heapq ordering but locks on every
message.  Keep the tuple comparison and heap operations unchanged.
"""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path

import abides_core.kernel


EXPECTED_SHA256 = "eccaf701dd118e484fda08879be808a63ab4ea2eed46e280a812bdb73ca16177"
path = Path(inspect.getfile(abides_core.kernel))
source = path.read_bytes()
actual = hashlib.sha256(source).hexdigest()
if actual != EXPECTED_SHA256:
    raise RuntimeError(f"Unexpected installed ABIDES kernel: {actual}")

needle = b"        self.messages: queue.PriorityQueue[(int, str, Message)] = queue.PriorityQueue()"
if source.count(needle) != 1:
    raise RuntimeError("ABIDES event-queue construction is ambiguous")

addition = b'''\
import heapq as _participant_heapq


class _SingleOwnerEventQueue:
    """PriorityQueue ordering without thread synchronization.

    Every market owns this queue from one kernel thread.  Batch markets fork
    separate processes.  The exposed ``queue`` list retains the sole direct
    inspection used by ABIDES diagnostics.
    """

    __slots__ = ("queue",)

    def __init__(self):
        self.queue = []

    def put(self, item):
        _participant_heapq.heappush(self.queue, item)

    def get(self):
        return _participant_heapq.heappop(self.queue)

    def empty(self):
        return not self.queue


'''
marker = b"import queue\n"
if source.count(marker) != 1:
    raise RuntimeError("ABIDES queue import is ambiguous")
updated = source.replace(marker, marker + addition, 1).replace(
    needle,
    b"        self.messages: _SingleOwnerEventQueue = _SingleOwnerEventQueue()",
    1,
)
path.write_bytes(updated)
print("kernel.py before", actual)
print("kernel.py after", hashlib.sha256(updated).hexdigest())
