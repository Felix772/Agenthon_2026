"""Causal scripted engineering fixtures; these are not House quality results."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from agent.model_client import ModelClient, ModelError
from agent.review import apply_patch, attempt_repair, bounded_complete, bounded_validate, observe_outputs
from agent.solver import solve
from mock_server import mock_model
from test_agent import make_task


def repair(before, after, function="<module>"):
    return json.dumps({"action": "repair", "observed_defect": "observed_failure", "file": "solution.py",
                       "function": function, "before": before, "after": after})


def solution(code):
    return json.dumps({"code": code, "deliverables": ["results.json"]})


def scripted_execute(script, task, output, deadline):
    code = script.read_text()
    if "BAD_RUNTIME" in code:
        return {"returncode": 1, "timed_out": False, "elapsed_sec": .001,
                "log": "NameError: synthetic-private-error secret-canary"}
    if "MISSING" not in code:
        (output / "results.json").write_text('{"total":NaN}' if "NONFINITE" in code else '{"total":5}')
    return {"returncode": 0, "timed_out": False, "elapsed_sec": .001, "log": ""}


class ReviewTests(unittest.TestCase):
    def run_solve(self, root, responses, executor=scripted_execute, **env):
        make_task(root / "task", timeout=1200)
        with mock_model(responses) as (url, requests):
            values = {"MODEL_ENDPOINT": url, "MODEL_NAME": "synthetic", "AGENT_MAX_ATTEMPTS": "1",
                      "AGENT_C3_REVIEW": "1", **env}
            with patch.dict(os.environ, values), patch("agent.solver.execute", side_effect=executor):
                try:
                    solve(root / "task", root / "output")
                except RuntimeError:
                    pass
            return json.loads((root / "output/.agent/run.json").read_text()), requests

    def test_compile_runtime_missing_and_nonfinite_are_causally_repaired(self):
        for code, replacement, stage in [("x = (", "x = 5", "compile"),
                ("BAD_RUNTIME = True", "GOOD = True", "execute"),
                ("MISSING = True", "GOOD = True", "artifact"),
                ("NONFINITE = True", "GOOD = True", "artifact")]:
            with self.subTest(stage=stage, code=code), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                report, requests = self.run_solve(root, [solution(code), repair(code, replacement)])
                self.assertEqual(report["status"], "completed_unverified")
                self.assertEqual(report["attempts"][0]["failure_stage"], stage)
                self.assertTrue(report["attempts"][0]["review"]["accepted"])
                self.assertEqual(report["model_usage"]["sends"], 2)
                self.assertFalse(report["model_usage"]["sends_include_unconfirmed_reservations"])
                self.assertEqual(len(requests), 2)
                self.assertEqual((root / "output/results.json").read_bytes(), b'{"total":5}')
                self.assertNotIn("synthetic-private-error", json.dumps(report))
                self.assertNotIn("secret-canary", json.dumps(report))

    def test_valid_candidate_has_identical_bytes_and_no_review_send(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report, requests = self.run_solve(root, [solution("GOOD = True"), repair("GOOD", "STYLE")])
            self.assertEqual(report["status"], "completed_unverified")
            self.assertEqual(len(requests), 1)
            self.assertNotIn("review", report["attempts"][0])
            self.assertEqual(hashlib.sha256((root / "output/results.json").read_bytes()).digest(),
                             hashlib.sha256(b'{"total":5}').digest())

    def test_feature_is_off_by_default_and_original_retry_path_is_used(self):
        with tempfile.TemporaryDirectory() as temp:
            report, requests = self.run_solve(Path(temp), [solution("MISSING = True"), solution("GOOD = True")],
                AGENT_C3_REVIEW="0", AGENT_MAX_ATTEMPTS="2")
            self.assertEqual(report["status"], "completed_unverified")
            self.assertEqual(len(requests), 2)
            self.assertFalse(report["c3"]["enabled"])
            self.assertNotIn("review", report["attempts"][0])

    def test_bad_repair_rolls_back_then_core_retry_can_succeed(self):
        with tempfile.TemporaryDirectory() as temp:
            report, requests = self.run_solve(Path(temp), [solution("MISSING = True"),
                repair("MISSING = True", "BAD_RUNTIME = True"), solution("GOOD = True")], AGENT_MAX_ATTEMPTS="2")
            self.assertEqual(report["status"], "completed_unverified")
            self.assertEqual(report["attempts"][0]["review"]["status"], "rolled_back")
            self.assertEqual(len(requests), 3)

    def test_review_terminal_http_stops_regeneration(self):
        with tempfile.TemporaryDirectory() as temp:
            report, requests = self.run_solve(Path(temp), [solution("MISSING = True"), (403, {}),
                solution("GOOD = True")], AGENT_MAX_ATTEMPTS="3")
            self.assertEqual(report["status"], "failed")
            self.assertTrue(report["attempts"][0]["terminal"])
            self.assertEqual(report["attempts"][0]["review"]["reason"], "http_permanent")
            self.assertEqual(len(requests), 2)
            self.assertEqual(report["model_usage"]["sends"], 2)
            self.assertEqual(report["model_usage"]["requests"], 1)

    def test_insufficient_remaining_time_skips_review(self):
        with tempfile.TemporaryDirectory() as temp:
            report, requests = self.run_solve(Path(temp), [solution("MISSING = True")], AGENT_SOFT_TIMEOUT_SEC="80")
            self.assertEqual(report["attempts"][0]["review"]["status"], "skipped")
            self.assertEqual(len(requests), 1)
            self.assertEqual(report["publication_reserve_sec"], 4)

    def test_named_function_and_unique_patch_are_enforced(self):
        code = "def solve():\n    return 1\nx = 2\n"
        self.assertIn("return 3", apply_patch(code, repair("return 1", "return 3", "solve")))
        for response in [repair("return 1", "return 3"), repair("x = 2", "x = 3", "solve"),
                         repair("missing", "x"), repair("return 1", "x" * 8193, "solve")]:
            with self.assertRaises(ValueError):
                apply_patch(code, response)
        with self.assertRaises(ValueError):
            apply_patch("x=1\nx=1", repair("x=1", "x=2"))
        with self.assertRaises(ValueError):
            apply_patch(code, repair("    return 1", "    return 1\ndef injected():\n    pass", "solve"))
        with self.assertRaises(ValueError):
            apply_patch(code, repair("x = 2", "def injected():\n    pass\nx = 3"))

    def test_oversized_original_does_not_bypass_core_cleanup_and_retry(self):
        def oversized_first(script, task, output, deadline):
            result = scripted_execute(script, task, output, deadline)
            if "OVERSIZED" in script.read_text():
                with (output / "large.bin").open("wb") as handle:
                    handle.truncate(64 * 1024 * 1024)
            return result
        with tempfile.TemporaryDirectory() as temp:
            report, requests = self.run_solve(Path(temp), [solution("OVERSIZED = True"), solution("GOOD = True")],
                executor=oversized_first, AGENT_MAX_ATTEMPTS="2")
            self.assertEqual(report["status"], "completed_unverified")
            self.assertEqual(report["attempts"][0]["review"]["reason"], "observation_failed")
            self.assertEqual(len(requests), 2)

    @unittest.skipUnless(sys.platform == "linux", "Linux symlink fixture")
    def test_linked_original_does_not_bypass_core_cleanup_and_retry(self):
        def linked_first(script, task, output, deadline):
            result = scripted_execute(script, task, output, deadline)
            if "LINK" in script.read_text():
                (output / "link").symlink_to(output / "results.json")
            return result
        with tempfile.TemporaryDirectory() as temp:
            report, _ = self.run_solve(Path(temp), [solution("LINK = True"), solution("GOOD = True")],
                executor=linked_first, AGENT_MAX_ATTEMPTS="2")
            self.assertEqual(report["status"], "completed_unverified")
            self.assertEqual(report["attempts"][0]["review"]["reason"], "observation_failed")

    def test_malformed_csv_original_still_allows_core_retry(self):
        def csv_execute(script, task, output, deadline):
            result = scripted_execute(script, task, output, deadline)
            (output / "table.csv").write_text('a\n"' if "BROKEN_CSV" in script.read_text() else "a\n1\n")
            return result
        responses = [json.dumps({"code": code, "deliverables": ["results.json", "table.csv"]})
                     for code in ("BROKEN_CSV = True", "GOOD = True")]
        with tempfile.TemporaryDirectory() as temp, patch("agent.review.bounded_complete", return_value='{"action":"accept"}'):
            report, requests = self.run_solve(Path(temp), responses, executor=csv_execute, AGENT_MAX_ATTEMPTS="2")
            self.assertEqual(report["status"], "completed_unverified")
            self.assertEqual(report["attempts"][0]["error_type"], "Error")
            self.assertEqual(len(requests), 2)

    def direct_attempt(self, root, client, **overrides):
        attempt = root / "attempt"
        attempt.mkdir()
        output = attempt / "output"
        output.mkdir()
        (output / "results.json").write_text('{"total":5}')
        task = type("Task", (), {"root": root, "instruction": "Write results.json."})()
        arguments = dict(client=client, solution={"code": "BAD_RUNTIME = True", "deliverables": ["results.json"]},
            task=task, directory=output, attempt_dir=attempt, whole_output=root, contract={"required_artifacts": []},
            stage="execute", error=ValueError("runtime failure"),
            execution={"returncode": 1, "timed_out": False, "elapsed_sec": .001},
            work_deadline=time.monotonic() + 200, executor=scripted_execute, redact=lambda value: value)
        arguments.update(overrides)
        return attempt_repair(**arguments), output

    def test_review_error_timeout_and_bad_candidate_preserve_complete_original_bytes(self):
        for error in [ModelError("timeout", category="review_timeout"), ModelError("error", category="transport")]:
            with self.subTest(error=error.category), tempfile.TemporaryDirectory() as temp:
                client = ModelClient("http://localhost:1", "synthetic", "synthetic-token")
                with patch("agent.review.bounded_complete", side_effect=error):
                    (candidate, record), output = self.direct_attempt(Path(temp), client)
                self.assertIsNone(candidate)
                self.assertEqual(record["status"], "rolled_back")
                self.assertEqual(record["preserved_nonempty_artifacts"], ["results.json"])
                self.assertEqual(output.joinpath("results.json").read_bytes(), b'{"total":5}')

    def test_candidate_that_loses_complete_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            client = ModelClient("http://localhost:1", "synthetic", "synthetic-token")
            with patch("agent.review.bounded_complete", return_value=repair("BAD_RUNTIME", "MISSING")):
                (candidate, record), output = self.direct_attempt(Path(temp), client)
            self.assertIsNone(candidate)
            self.assertEqual(record["status"], "rolled_back")
            self.assertEqual(output.joinpath("results.json").read_bytes(), b'{"total":5}')
            self.assertFalse(output.parent.joinpath("review-output").exists())

    def test_combined_output_tree_limit_rejects_repair_and_preserves_original(self):
        def oversized(script, task, output, deadline):
            (output / "results.json").write_text('{"total":5}')
            with (output / "large.bin").open("wb") as handle:
                handle.truncate(64 * 1024 * 1024)
            return {"returncode": 0, "timed_out": False, "elapsed_sec": .001, "log": ""}
        with tempfile.TemporaryDirectory() as temp:
            client = ModelClient("http://localhost:1", "synthetic", "synthetic-token")
            with patch("agent.review.bounded_complete", return_value=repair("BAD_RUNTIME", "GOOD")):
                (candidate, record), output = self.direct_attempt(Path(temp), client, executor=oversized)
            self.assertIsNone(candidate)
            self.assertEqual(record["status"], "rolled_back")
            self.assertEqual(output.joinpath("results.json").read_bytes(), b'{"total":5}')
            self.assertFalse(output.parent.joinpath("review-output").exists())

    def test_summary_samples_structure_without_storing_values_or_raw_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "data.csv").write_text("a,b\nsecret-value,NaN\n")
            summary = observe_outputs(root, ["data.csv"], {}, stage="artifact", error_type="ValueError")
            self.assertEqual(summary["outputs"][0]["sample_rows"], 1)
            self.assertEqual(summary["outputs"][0]["sampled_nonfinite_cells"], 1)
            self.assertNotIn("secret-value", json.dumps(summary))
            (root / "data.csv").write_text("x\n" + "1\n" * 200000)
            summary = observe_outputs(root, ["data.csv"], {}, stage="artifact", error_type="ValueError")
            self.assertLessEqual(summary["outputs"][0]["sample_bytes"], 131072)
            self.assertEqual(summary["outputs"][0]["sample_rows"], 200)

    def test_child_max_one_send_and_global_limit_are_not_double_counted(self):
        with mock_model(['{"action":"accept"}']) as (url, requests):
            with patch.dict(os.environ, {"MODEL_ENDPOINT": url, "MODEL_NAME": "synthetic"}):
                client = ModelClient()
                client.sends = client.requests = 24
                self.assertEqual(bounded_complete(client, [], time.monotonic() + 5), '{"action":"accept"}')
                self.assertEqual(client.sends, 25)
                self.assertEqual(client.remaining_sends, 0)
                self.assertEqual(client.review_send_reservations, 0)
                with self.assertRaises(ModelError):
                    bounded_complete(client, [], time.monotonic() + 5)
            self.assertEqual(len(requests), 1)

    def test_child_is_killed_at_review_deadline_not_socket_inactivity_timeout(self):
        real_popen = subprocess.Popen
        def hung(*args, **kwargs):
            return real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        client = ModelClient("http://localhost:1", "synthetic", "synthetic-token")
        started = time.monotonic()
        with patch("agent.review.subprocess.Popen", side_effect=hung), self.assertRaisesRegex(ModelError, "deadline"):
            bounded_complete(client, [], time.monotonic() + .2)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(client.sends, 1)
        self.assertEqual(client.review_send_reservations, 1)

    def test_real_slow_response_child_stops_and_confirms_one_send(self):
        seen = threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                seen.set()
                self.send_response(200)
                self.end_headers()
                try:
                    for _ in range(500):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(.01)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            values = {"MODEL_ENDPOINT": f"http://127.0.0.1:{server.server_port}", "MODEL_NAME": "synthetic",
                      "MODEL_TOKEN": "synthetic-token", "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
            with patch.dict(os.environ, values):
                client = ModelClient()
                started = time.monotonic()
                with self.assertRaises(ModelError):
                    bounded_complete(client, [], time.monotonic() + 2)
                self.assertLess(time.monotonic() - started, 4)
                self.assertTrue(seen.is_set())
                self.assertEqual(client.sends, 1)
                self.assertEqual(client.review_send_reservations, 0)
                self.assertEqual(client.unknown_usage_requests, 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_expensive_final_validation_has_its_own_kill_boundary(self):
        real_popen = subprocess.Popen
        def hung(*args, **kwargs):
            return real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        started = time.monotonic()
        with patch("agent.review.subprocess.Popen", side_effect=hung), self.assertRaisesRegex(ValueError, "timed out"):
            bounded_validate(Path("synthetic"), [], {}, Path("synthetic"), time.monotonic() + .2)
        self.assertLess(time.monotonic() - started, 3)


if __name__ == "__main__":
    unittest.main()
