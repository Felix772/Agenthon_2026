"""Focused equivalence tests for the unbuilt scalar-latency experiment.

The tests execute the helper text that the patch inserts, comparing its result
or exception with the original expression.  Final validation must repeat them
inside the pinned NumPy 1.26.4 image before any image timing or promotion.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import math
from pathlib import Path
import random
import unittest
from unittest import mock

import numpy as np


PATCH_PATH = Path(__file__).with_name("patch_scalar_latency_clip.py")
spec = importlib.util.spec_from_file_location("patch_scalar_latency_clip", PATCH_PATH)
assert spec is not None and spec.loader is not None
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


def installed_helper():
    tree = ast.parse(patch.HELPER_SOURCE.decode("utf-8"))
    namespace = {"math": math, "np": np}
    exec(compile(tree, str(PATCH_PATH), "exec"), namespace)
    return namespace["_clip_latency_scalar"]


def original(value, low, high):
    return int(round(float(np.clip(value, low, high))))


def outcome(function, value, low, high):
    try:
        return ("value", function(value, low, high))
    except Exception as exc:  # compare the exact original failure class as well
        return ("error", type(exc), str(exc))


class ScalarLatencyClipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = staticmethod(installed_helper())

    def assert_equivalent(self, value, low, high):
        self.assertEqual(
            outcome(self.helper, value, low, high),
            outcome(original, value, low, high),
            (value, low, high),
        )

    def test_finite_float64_edges_and_rounding_ties(self):
        values = [
            -1e100, -3.5, -2.5, -1.5, -0.5, -0.0, 0.0, 0.5,
            1.5, 2.5, 3.5, 1e100,
            math.nextafter(0.0, -math.inf),
            math.nextafter(0.0, math.inf),
            math.nextafter(0.5, 0.0),
            math.nextafter(0.5, math.inf),
            math.nextafter(1e12, -math.inf),
            math.nextafter(1e12, math.inf),
        ]
        bounds = [(-10.0, 10.0), (0.0, 1.0), (-0.0, 0.0),
                  (0.5, 2.5), (100.0, 1e12)]
        for low, high in bounds:
            for raw in values:
                for value in (float(raw), np.float64(raw)):
                    with self.subTest(value=value, low=low, high=high):
                        self.assert_equivalent(value, low, high)

    def test_seeded_finite_scalar_samples(self):
        rng = random.Random(20260928)
        for _ in range(1000):
            low = rng.uniform(-1e6, 1e6)
            high = low + rng.uniform(0.0, 1e6)
            raw = rng.uniform(low - 1e6, high + 1e6)
            value = np.float64(raw) if rng.randrange(2) else float(raw)
            self.assert_equivalent(value, low, high)

    def test_nonfinite_reversed_and_other_types_fall_back(self):
        cases = [
            (math.nan, 0.0, 10.0), (math.inf, 0.0, 10.0),
            (-math.inf, 0.0, 10.0), (np.float64(math.nan), 0.0, 10.0),
            (3.0, 10.0, 0.0), (3.0, math.nan, 10.0),
            (3.0, 0.0, math.nan), (3.0, -math.inf, 10.0),
            (3.0, 0.0, math.inf), (np.float32(1.25), 0.0, 10.0),
        ]
        for value, low, high in cases:
            with self.subTest(value=value, low=low, high=high):
                self.assert_equivalent(value, low, high)

    def test_nonfinite_cases_use_original_numpy_branch(self):
        actual_clip = np.clip
        with mock.patch.object(np, "clip", wraps=actual_clip) as clip:
            for value, low, high in [
                (math.nan, 0.0, 10.0),
                (math.inf, 0.0, 10.0),
                (1.0, math.nan, 10.0),
                (1.0, 10.0, 0.0),
                (np.float32(1.0), 0.0, 10.0),
            ]:
                outcome(self.helper, value, low, high)
            self.assertEqual(clip.call_count, 5)

    def test_source_guard_and_single_site_replacement(self):
        source = (
            b"import numpy as np\n"
            b"class ScenarioLatencyModel(LatencyModel):\n"
            b"    def get_latency(self):\n"
            + patch.RETURN_NEEDLE + b"\n"
        )
        self.assertRaises(RuntimeError, patch.patch_source, source)
        with mock.patch.object(patch, "EXPECTED_SHA256", hashlib.sha256(source).hexdigest()):
            updated = patch.patch_source(source)
        self.assertEqual(updated.count(b"def _clip_latency_scalar("), 1)
        self.assertEqual(updated.count(b"return _clip_latency_scalar("), 1)
        self.assertNotIn(patch.RETURN_NEEDLE, updated)
        ast.parse(updated.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
