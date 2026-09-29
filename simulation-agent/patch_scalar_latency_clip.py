"""Experimental scalar clipping for the pinned Track 3 latency adapter.

This patch is deliberately separate from the submitted four-worker image and
the unlocked-queue candidate.  It only changes the per-message scalar clip;
the random draw, its order, and the final Python ``round``/``int`` stay put.

For finite scalar float64 inputs and finite ordered float64 bounds, NumPy 1.26
defines ``clip`` as ``minimum(high, maximum(value, low))``.  Other values and
types retain the original NumPy call, including NaNs, infinities, reversed
bounds, and unusual scalar subclasses.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


TARGET = Path("/opt/abides_fork/config.py")
EXPECTED_SHA256 = "5cd0ecfd25b999ff44f961111c84b5da718be2a548cfd2b6ea725bcedf8cccbd"

IMPORT_NEEDLE = b"import numpy as np\n"
CLASS_NEEDLE = b"class ScenarioLatencyModel(LatencyModel):"
RETURN_NEEDLE = b"        return int(round(float(np.clip(value, self._min_ns, self._max_ns))))"

HELPER_SOURCE = b'''\
def _clip_latency_scalar(value: float, low: float, high: float) -> int:
    """Fast finite-float64 path; preserve NumPy behavior outside that domain."""
    if (
        (type(value) is float or type(value) is np.float64)
        and type(low) is float
        and type(high) is float
        and math.isfinite(value)
        and math.isfinite(low)
        and math.isfinite(high)
        and low <= high
    ):
        clipped = low if value < low else high if value > high else value
        return int(round(float(clipped)))
    return int(round(float(np.clip(value, low, high))))


'''


def patch_source(source: bytes) -> bytes:
    actual = hashlib.sha256(source).hexdigest()
    if actual != EXPECTED_SHA256:
        raise RuntimeError(f"Unexpected installed Track 3 config.py: {actual}")
    for needle in (IMPORT_NEEDLE, CLASS_NEEDLE, RETURN_NEEDLE):
        if source.count(needle) != 1:
            raise RuntimeError(f"Ambiguous Track 3 latency patch site: {needle!r}")
    updated = source.replace(IMPORT_NEEDLE, b"import math\n" + IMPORT_NEEDLE, 1)
    updated = updated.replace(CLASS_NEEDLE, HELPER_SOURCE + CLASS_NEEDLE, 1)
    updated = updated.replace(
        RETURN_NEEDLE,
        b"        return _clip_latency_scalar(value, self._min_ns, self._max_ns)",
        1,
    )
    return updated


def main() -> None:
    original = TARGET.read_bytes()
    patched = patch_source(original)
    TARGET.write_bytes(patched)
    print("config.py before", hashlib.sha256(original).hexdigest())
    print("config.py after", hashlib.sha256(patched).hexdigest())


if __name__ == "__main__":
    main()
