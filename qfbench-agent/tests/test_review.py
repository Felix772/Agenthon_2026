"""Unit time budget, review round and input previews. Responses are scripted, not a model."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from agent.task_reader import describe_files, read_task
from mock_server import mock_model
from test_agent import CODE, make_task

GOOD = json.dumps({"code": CODE, "deliverables": ["results.json"]})
FIXED = json.dumps({"code": CODE.replace('int(data["value"].sum())', 'int(data["value"].sum()) * 10'),
                    "deliverables": ["results.json"]})
BROKEN = json.dumps({"code": "raise SystemExit(3)", "deliverables": ["results.json"]})
OK = json.dumps({"verdict": "ok"})


def run(root, responses, **env_extra):
    with mock_model(responses) as (url, requests):
        env = {**os.environ, "MODEL_ENDPOINT": url, "MODEL_NAME": "synthetic-test",
               "NO_PROXY": "127.0.0.1", **env_extra}
        result = subprocess.run([sys.executable, "-m", "agent", "solve", "--task-dir", str(root / "task"),
                                 "--out", str(root / "out")], env=env, capture_output=True, text=True,
                                timeout=60)
        return result, list(requests)


@unittest.skipUnless(sys.platform == "linux", "Execution integration tests run in Linux Docker")
class ReviewTests(unittest.TestCase):
    def solve(self, responses, **env):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        make_task(root / "task")
        result, requests = run(root, responses, **env)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((root / "out/.agent/run.json").read_text())
        return json.loads((root / "out/results.json").read_text()), report, requests

    def test_review_ok_keeps_answer_and_sees_output_preview(self):
        answer, report, requests = self.solve([GOOD, OK])
        self.assertEqual(answer, {"total": 5})
        self.assertEqual(report["reviews"][0]["status"], "accepted_as_is")
        self.assertIn('\\"total\\": 5', requests[1]["payload"]["messages"][-1]["content"])

    def test_review_revision_is_adopted_only_when_it_runs(self):
        answer, report, _ = self.solve([GOOD, FIXED])
        self.assertEqual(answer, {"total": 50})
        self.assertEqual(report["reviews"][0]["status"], "revised")
        answer, report, _ = self.solve([GOOD, BROKEN])
        self.assertEqual(answer, {"total": 5})
        self.assertEqual(report["reviews"][0]["status"], "revision_failed_kept_original")
        answer, report, _ = self.solve([GOOD, "not json"])
        self.assertEqual(answer, {"total": 5})
        self.assertIn("error", report["reviews"][0])

    def test_review_can_be_disabled_and_budget_caps_card_timeout(self):
        _, report, requests = self.solve([GOOD, OK], AGENT_REVIEW_ROUNDS="0", AGENT_UNIT_BUDGET_SEC="25")
        self.assertEqual(len(requests), 1)
        self.assertEqual(report["reviews"], [])
        self.assertEqual(report["budget_sec"], 25.0)
        self.assertEqual(report["timeout_sec"], 30.0)


class PreviewTests(unittest.TestCase):
    def test_parquet_inputs_get_schema_and_head(self):
        import pandas as pd
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "task"
            make_task(root)
            pd.DataFrame({"ticker": ["A", "B"], "px": [1.5, 2.5]}).to_parquet(root / "prices.parquet")
            items = {i["path"]: i for i in describe_files(read_task(root))}
            preview = json.loads(items["prices.parquet"]["preview"])
            self.assertEqual(preview["num_rows"], 2)
            self.assertIn("px: double", preview["schema"])
            self.assertEqual(preview["head"][0]["ticker"], "A")
            self.assertNotIn("checks/hidden.txt", items)


if __name__ == "__main__":
    unittest.main()
