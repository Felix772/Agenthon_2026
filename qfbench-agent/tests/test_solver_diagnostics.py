import json
import os
import subprocess
import sys
import tempfile
import unittest
import time
from pathlib import Path
from unittest.mock import patch

from agent.solver import solve
from agent.cli import run_guarded
from mock_server import mock_model
from test_agent import make_task
from test_tools import load_tool

GOOD = json.dumps({"code": "print('synthetic')", "deliverables": ["results.json"]})


def produce(script, task, output, deadline):
    (output / "results.json").write_text('{"total": 5}')
    return {"returncode": 0, "timed_out": False, "elapsed_sec": .001, "log": ""}


class DiagnosticTests(unittest.TestCase):
    def test_parent_watchdog_stops_child_blocked_before_task_discovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".agent").mkdir()
            (root / ".agent/run.json").write_text('{"stage":"startup"}')
            started = time.monotonic()
            result = run_guarded([sys.executable, "-c", "import time; time.sleep(30)"], root, started + .5)
            self.assertEqual(result, 124)
            self.assertLess(time.monotonic() - started, 3)
            self.assertEqual(json.loads((root / ".agent/run.json").read_text())["stage"], "startup")
            self.assertTrue(json.loads((root / ".agent/outer-watchdog.json").read_text())["timed_out"])

    @unittest.skipUnless(sys.platform == "linux", "Linux child-tree cleanup")
    def test_parent_watchdog_kills_worker_in_separate_process_group(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".agent").mkdir()
            marker = root / "worker-survived"
            worker = "import pathlib,time; time.sleep(2); pathlib.Path(" + repr(str(marker)) + ").write_text('bad')"
            launcher = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(worker) + "], start_new_session=True); time.sleep(30)"
            result = run_guarded([sys.executable, "-c", launcher], root, time.monotonic() + .5)
            self.assertEqual(result, 124)
            time.sleep(2)
            self.assertFalse(marker.exists())

    def run_case(self, root, responses, executor=produce, attempts="3", **env):
        make_task(root / "input", timeout=1200)
        with mock_model(responses) as (url, requests):
            values = {"MODEL_ENDPOINT": url, "MODEL_NAME": "synthetic", "AGENT_MAX_ATTEMPTS": attempts, **env}
            with patch.dict(os.environ, values), patch("agent.solver.execute", side_effect=executor):
                try:
                    solve(root / "input", root / "output")
                except (ValueError, RuntimeError, OSError):
                    pass
            report = json.loads((root / "output/.agent/run.json").read_text())
            return report, requests

    def test_startup_failure_is_durable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(OSError):
                solve(root / "missing", root / "output")
            report = json.loads((root / "output/.agent/run.json").read_text())
            self.assertEqual((report["stage"], report["status"]), ("startup", "failed"))

    def test_terminal_http_stops_outer_regeneration_and_counts_actual_send(self):
        with tempfile.TemporaryDirectory() as temp:
            report, requests = self.run_case(Path(temp), [(403, {"message": "secret-canary"}), GOOD])
            self.assertEqual(len(requests), 1)
            self.assertEqual(report["model_usage"]["sends"], 1)
            self.assertEqual(report["model_usage"]["requests"], 0)
            self.assertTrue(report["attempts"][0]["terminal"])
            self.assertEqual(report["attempts"][0]["failure_stage"], "http")
            self.assertNotIn("secret-canary", json.dumps(report))

    def test_parse_repair_and_success_clear_previous_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            report, _ = self.run_case(Path(temp), ["not json", GOOD])
            self.assertEqual(report["attempts"][0]["failure_stage"], "parse")
            self.assertEqual(report["status"], "completed_unverified")
            self.assertIsNone(report["failure_category"])
            self.assertEqual(report["model_usage"]["sends"], 2)
            self.assertEqual(report["effective_timeout_sec"], 360)
            self.assertEqual(report["publication_reserve_sec"], 15)

    def test_compile_stage_is_separate(self):
        bad = json.dumps({"code": "def broken(", "deliverables": ["results.json"]})
        with tempfile.TemporaryDirectory() as temp:
            report, _ = self.run_case(Path(temp), [bad, GOOD])
            self.assertEqual(report["attempts"][0]["failure_stage"], "compile")
            self.assertEqual(report["attempts"][0]["error_type"], "SyntaxError")

    def test_execution_metadata_has_exception_type_without_raw_error_text(self):
        def fail(*args):
            return {"returncode": 1, "timed_out": False, "elapsed_sec": .001,
                    "log": "Traceback:\nValueError: secret-canary synthetic-private-error-detail\n"}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report, _ = self.run_case(root, [GOOD], executor=fail, attempts="1")
            self.assertEqual(report["attempts"][0]["failure_stage"], "execute")
            self.assertEqual(report["attempts"][0]["execution"]["reported_exception_type"], "ValueError")
            for path in (root / "output").rglob("*.json"):
                self.assertNotIn("synthetic-private-error-detail", path.read_text())
                self.assertNotIn("secret-canary", path.read_text())

    def test_model_cannot_omit_instruction_required_artifact(self):
        omission = json.dumps({"code": "print('synthetic')", "deliverables": ["other.json"]})
        def other(script, task, output, deadline):
            (output / "other.json").write_text("{}")
            return {"returncode": 0, "timed_out": False, "elapsed_sec": .001, "log": ""}
        with tempfile.TemporaryDirectory() as temp:
            report, _ = self.run_case(Path(temp), [omission], executor=other, attempts="1")
            self.assertEqual(report["attempts"][0]["failure_stage"], "artifact")
            self.assertEqual(report["status"], "failed")

    def test_publish_failure_has_its_own_stage(self):
        with tempfile.TemporaryDirectory() as temp, patch("agent.solver.publish_outputs", side_effect=OSError("synthetic")):
            report, _ = self.run_case(Path(temp), [GOOD], attempts="1")
            self.assertEqual(report["attempts"][0]["failure_stage"], "publish")

    def test_exhausted_time_sends_no_http(self):
        with tempfile.TemporaryDirectory() as temp:
            report, requests = self.run_case(Path(temp), [GOOD], AGENT_SOFT_TIMEOUT_SEC="0.8")
            self.assertEqual(requests, [])
            self.assertEqual(report["model_usage"]["sends"], 0)
            self.assertEqual(report["failure_category"], "deadline")

    def test_outer_watchdog_persists_even_without_agent_report(self):
        tool = load_tool("evaluate")
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "agent.log"
            with patch.object(tool.subprocess, "run", side_effect=[subprocess.TimeoutExpired("synthetic", 2), subprocess.CompletedProcess([], 0)]):
                result = tool.run_container(["synthetic"], "synthetic-test", 2, log)
            self.assertTrue(result["timed_out"])
            self.assertEqual(json.loads(log.with_suffix(".status.json").read_text())["status"], "outer_timeout")

    def test_full_roster_budget_worksheet_is_not_a_measured_release_gate(self):
        tool = load_tool("evaluate")
        with tempfile.TemporaryDirectory() as temp:
            units = []
            for i in range(86):
                root = Path(temp) / str(i)
                root.mkdir()
                (root / "card.toml").write_text("[agent]\ntimeout_sec=1200\n")
                units.append(root)
            result = tool.budget_worksheet(units)
            self.assertEqual(result["solve_cap_sum_sec"], 30960)
            self.assertTrue(result["within_internal_solve_target"])
            self.assertIsNone(result["setup_pull_ingestion_overhead_sec"])
            self.assertEqual(result["internal_solve_target_sec"], 34560)


if __name__ == "__main__":
    unittest.main()
