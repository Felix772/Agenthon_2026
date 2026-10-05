"""Preregistered exact-set checks for ambiguous output instructions."""

import json
import unittest
from pathlib import Path

from agent.contracts import instruction_contract


CASES = Path(__file__).parent / "fixtures" / "contract_adversarial.json"


class ContractAdversarialTests(unittest.TestCase):
    def test_exact_required_artifacts(self):
        for case in json.loads(CASES.read_text(encoding="utf-8")):
            with self.subTest(case=case["name"]):
                actual = [item["path"] for item in instruction_contract(case["instruction"])["required_artifacts"]]
                self.assertEqual(actual, case["required"])


if __name__ == "__main__":
    unittest.main()
