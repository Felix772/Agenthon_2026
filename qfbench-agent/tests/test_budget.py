import json
import hashlib
import math
import os
from pathlib import Path
import tempfile
import sys
import subprocess
import time
import unittest
from unittest.mock import patch

from agent.budget import budget_for, configured_cap, read_card_timeout
from agent.cli import main
from agent.solver import solve
from test_agent import make_task
from test_tools import load_tool
from mock_server import mock_model


class BudgetTests(unittest.TestCase):
    def test_default_and_explicit_candidate_respect_every_card(self):
        for card in (10, 600, 1200, 2400):
            for cap in (360, 600):
                with self.subTest(card=card, cap=cap):
                    budget = budget_for(card, cap)
                    self.assertEqual(budget.ceiling, min(card, cap))
                    self.assertLess(budget.work_deadline(100), budget.child_deadline(100))
                    self.assertLess(budget.child_deadline(100), budget.parent_deadline(100))
                    self.assertLess(budget.parent_deadline(100), 100 + budget.ceiling)
                    self.assertGreater(budget.work_deadline(100), 100)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(configured_cap(), 360)
        with patch.dict(os.environ, {"AGENT_SOFT_TIMEOUT_SEC": "600"}):
            self.assertEqual(configured_cap(), 600)

    def test_invalid_configuration_and_card_are_refused(self):
        for value in ("nan", "inf", "0", "-1", "", "bad"):
            with self.subTest(value=value), patch.dict(os.environ, {"AGENT_SOFT_TIMEOUT_SEC": value}):
                with self.assertRaises(ValueError):
                    configured_cap()
        for value in (None, True, "600", 0, -1, math.inf, math.nan):
            with self.subTest(value=value), self.assertRaises(ValueError):
                budget_for(value, 600)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(OSError):
                read_card_timeout(root)
            for text in ('[agent]\ntimeout_sec=true', '[agent]\ntimeout_sec="600"',
                         '[agent]\ntimeout_sec=nan', '[agent]\ntimeout_sec=0', '[task]\nid="x"'):
                (root / "card.toml").write_text(text)
                with self.subTest(text=text), self.assertRaises(ValueError):
                    read_card_timeout(root)
            (root / "card.toml").write_text(" " * 256001)
            with self.assertRaisesRegex(ValueError, "size"):
                read_card_timeout(root)

    def test_parent_passes_one_start_to_child_and_respects_short_card(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"AGENT_SOFT_TIMEOUT_SEC": "600", "AGENT_E1_SEMANTIC": "0"}):
            root = Path(temp)
            make_task(root / "input", timeout=10)
            before = time.monotonic()
            with patch("agent.cli.run_guarded", return_value=0) as guarded:
                self.assertEqual(main(["solve", "--task-dir", str(root / "input"), "--out", str(root / "out")]), 0)
            command, _, deadline = guarded.call_args.args
            started = float(command[command.index("--internal-started") + 1])
            self.assertGreaterEqual(started, before)
            self.assertAlmostEqual(deadline, budget_for(10, 600).parent_deadline(started))

    @unittest.skipUnless(sys.platform == "linux", "Linux input file types")
    def test_parent_rejects_link_and_fifo_without_blocking(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "target"
            target.write_text("[agent]\ntimeout_sec=600\n")
            card = root / "card.toml"
            card.symlink_to(target)
            with self.assertRaises(ValueError):
                read_card_timeout(root)
            card.unlink()
            os.mkfifo(card)
            with self.assertRaises(ValueError):
                read_card_timeout(root)

    def test_child_does_not_restart_expired_parent_budget(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"AGENT_SOFT_TIMEOUT_SEC": "600"}):
            root = Path(temp)
            make_task(root / "input", timeout=10)
            with patch("agent.solver.ModelClient") as model:
                with self.assertRaises(TimeoutError):
                    solve(root / "input", root / "output", started=time.monotonic() - 20)
            model.assert_not_called()
            report = json.loads((root / "output/.agent/run.json").read_text())
            self.assertEqual(report["status"], "failed")

    def test_discovery_time_reduces_shared_budget(self):
        from agent.task_reader import read_task
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"AGENT_SOFT_TIMEOUT_SEC": "600"}):
            root = Path(temp)
            make_task(root / "input", timeout=10)
            origin = time.monotonic() - 2
            timers = []
            class Timer:
                daemon = True
                def __init__(self, interval, callback):
                    timers.append(interval)
                def start(self):
                    pass
                def cancel(self):
                    pass
            with patch("agent.solver.threading.Timer", Timer), patch("agent.solver.read_task", side_effect=read_task), \
                    patch("agent.solver.ModelClient", side_effect=RuntimeError("stop before HTTP")):
                with self.assertRaisesRegex(RuntimeError, "stop before HTTP"):
                    solve(root / "input", root / "output", started=origin)
            self.assertLessEqual(timers[-1], budget_for(10, 600).child_deadline(origin) - origin - 2)

    def test_local_evaluator_uses_same_ceiling_and_names_extra_launch_time(self):
        evaluator = load_tool("evaluate")
        with tempfile.TemporaryDirectory() as temp:
            roots = []
            for value in (10, 600, 1200, 2400):
                root = Path(temp) / str(value)
                make_task(root, timeout=value)
                roots.append(root)
            result = evaluator.budget_worksheet(roots, 600)
            self.assertEqual([row["solve_cap_sec"] for row in result["units"]], [10, 600, 600, 600])
            self.assertEqual(result["local_docker_launch_allowance_sec"], 15)
            self.assertIn("Development", result["scope"])
            for row in result["units"]:
                self.assertEqual(row["work_budget_sec"], budget_for(row["card_timeout_sec"], 600).work_deadline(0))

    @unittest.skipUnless(sys.platform == "linux", "Actual guarded worker requires Linux")
    def test_explicit_600_candidate_runs_real_cli_without_changing_house_limits(self):
        code = ('import csv,json,os\nfrom pathlib import Path\n'
                'rows=csv.DictReader((Path(os.environ["TASK_DIR"])/"data.csv").open())\n'
                'total=sum(int(row["value"]) for row in rows)\n'
                '(Path(os.environ["OUTPUT_DIR"])/"results.json").write_text(json.dumps({"total":total}))')
        response = json.dumps({"code": code, "deliverables": ["results.json"]})
        for card in (600, 1200, 2400):
            with self.subTest(card=card), tempfile.TemporaryDirectory() as temp, mock_model([response]) as (url, requests):
                root = Path(temp)
                make_task(root / "input", timeout=card)
                env = {**os.environ, "AGENT_SOFT_TIMEOUT_SEC": "600", "AGENT_E1_SEMANTIC": "0",
                       "MODEL_ENDPOINT": url, "MODEL_NAME": "synthetic"}
                result = subprocess.run([sys.executable, "-m", "agent", "solve", "--task-dir", str(root / "input"),
                                         "--out", str(root / "output")], env=env, capture_output=True, text=True, timeout=40)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads((root / "output/results.json").read_text()), {"total": 5})
                report = json.loads((root / "output/.agent/run.json").read_text())
                self.assertEqual(report["effective_timeout_sec"], 600)
                self.assertEqual(report["work_budget_sec"], 576)
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0]["payload"]["max_tokens"], 4000)

    @unittest.skipUnless(sys.platform == "linux", "Actual guarded worker requires Linux")
    def test_tiny_positive_card_fails_closed_before_house(self):
        for card in (1e-9, .001):
            with self.subTest(card=card), tempfile.TemporaryDirectory() as temp, mock_model([]) as (url, requests):
                root = Path(temp)
                make_task(root / "input", timeout=card)
                env = {**os.environ, "AGENT_SOFT_TIMEOUT_SEC": "600", "AGENT_E1_SEMANTIC": "1",
                       "MODEL_ENDPOINT": url, "MODEL_NAME": "synthetic"}
                result = subprocess.run([sys.executable, "-m", "agent", "solve", "--task-dir", str(root / "input"),
                                         "--out", str(root / "output")], env=env, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertEqual(requests, [])
                self.assertFalse((root / "output/.agent/semantic-fallback.json").exists())

    @unittest.skipUnless(sys.platform == "linux", "Linux supervision and output permissions")
    def test_delayed_parent_recovery_waits_for_children_and_finalizes_permissions(self):
        from agent.semantic import restore_baseline
        original_popen = subprocess.Popen
        baseline = b'{"total": 5}'
        for fail_restore in (False, True):
            with self.subTest(fail_restore=fail_restore), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                make_task(root / "input", timeout=600)
                out, orphan_file = root / "output", root / "orphan-pid"
                processes, recoveries = [], []
                def launch(command, **kwargs):
                    receipt_fd = int(command[command.index("--internal-output-fd") + 1])
                    recovery_fd = int(command[command.index("--internal-semantic-fd") + 1])
                    script = (
                        'import os,subprocess,sys\nfrom pathlib import Path\nfrom agent import workspace,semantic\n'
                        f'workspace._output_acceptance_fd={receipt_fd}\nsemantic._fallback_fd={recovery_fd}\n'
                        f'out=workspace.prepare_output(Path({str(root / "input")!r}),Path({str(out)!r}))\n'
                        f'(out/"results.json").write_bytes({baseline!r})\n'
                        'semantic.freeze_baseline(out,["results.json"])\n'
                        '(out/"results.json").write_text("partial optional output")\n'
                        'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"],start_new_session=True)\n'
                        f'Path({str(orphan_file)!r}).write_text(str(child.pid))\n'
                        'os._exit(7)\n')
                    process = original_popen([sys.executable, "-c", script], **kwargs)
                    processes.append(process)
                    return process
                def delayed_restore(directory, proof):
                    self.assertIsNotNone(processes[0].poll(), "supervised child must exit before recovery")
                    orphan = int(orphan_file.read_text())
                    self.assertFalse(Path(f"/proc/{orphan}").exists(), "adopted worker must be reaped before recovery")
                    time.sleep(1.25)  # Deliberately longer than the normal 1-second watchdog gap.
                    recoveries.append(1)
                    if fail_restore:
                        raise OSError("synthetic delayed recovery failure")
                    return restore_baseline(directory, proof)
                with patch.dict(os.environ, {"AGENT_E1_SEMANTIC": "1", "AGENT_SOFT_TIMEOUT_SEC": "360"}), \
                        patch("agent.cli.subprocess.Popen", side_effect=launch), \
                        patch("agent.semantic.restore_baseline", side_effect=delayed_restore):
                    result = main(["solve", "--task-dir", str(root / "input"), "--out", str(out)])
                self.assertEqual(recoveries, [1])
                self.assertEqual(result, 7 if fail_restore else 0)
                if not fail_restore:
                    self.assertEqual(hashlib.sha256((out / "results.json").read_bytes()).digest(),
                                     hashlib.sha256(baseline).digest())
                self.assertEqual((out / "results.json").stat().st_mode & 0o777, 0o644)
                self.assertEqual(out.stat().st_mode & 0o777, 0o755)


if __name__ == "__main__":
    unittest.main()
