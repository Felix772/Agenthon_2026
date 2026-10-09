import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.execution import execute
from agent.model_client import ModelClient, ModelError
from agent.prompts import initial_messages
from agent.solver import parse_solution
from agent.task_reader import read_task
from agent.workspace import deliverable_name, prepare_output
from agent.workspace import validate_outputs
from mock_server import mock_model


def make_task(root, timeout=30):
    root.mkdir()
    (root / "instruction.md").write_text("Sum value in data.csv. Write results.json with total. Test sentinel: secret-canary", encoding="utf-8")
    (root / "card.toml").write_text(f'[agent]\ntimeout_sec = {timeout}\n[contamination]\ncanary_guid = "secret-canary"\n', encoding="utf-8")
    (root / "data.csv").write_text("value\n2\n3\n", encoding="utf-8")
    (root / "checks").mkdir()
    (root / "checks" / "hidden.txt").write_text("DO NOT READ", encoding="utf-8")


CODE = '''import json, os
from pathlib import Path
import pandas as pd
data = pd.read_csv(Path(os.environ["TASK_DIR"]) / "data.csv")
out = Path(os.environ["OUTPUT_DIR"])
(out / "results.json").write_text(json.dumps({"total": int(data["value"].sum())}))
'''


class ReaderAndClientTests(unittest.TestCase):
    def test_task_context_excludes_checks_and_canary(self):
        with tempfile.TemporaryDirectory() as temp:
            make_task(Path(temp) / "task", timeout=123)
            task = read_task(Path(temp) / "task")
            context = json.dumps(initial_messages(task))
            self.assertEqual(task.timeout, 123)
            self.assertNotIn("DO NOT READ", context)
            self.assertNotIn("secret-canary", context)
            self.assertNotIn("checks/hidden.txt", context)

    def test_input_errors_and_overlap(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(OSError):
                read_task(root / "missing")
            make_task(root / "task", timeout=0)
            with self.assertRaises(ValueError):
                read_task(root / "task")
            with self.assertRaises(ValueError):
                prepare_output(root, root / "output")

    def test_output_names_and_response_validation(self):
        for name in ("../escape", "/absolute", "reward.json", "nested/pytest_report.json", "x\\y", ".agent/run.json"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                deliverable_name(name)
        for text in ("not json", '{"code": "print(1)"}', '{"code": "oops (", "deliverables": ["x"]}'):
            with self.assertRaises((ValueError, SyntaxError)):
                parse_solution(text)

    def test_single_complete_json_fence_variants(self):
        payload = json.dumps({"code": "print(1)", "deliverables": ["results.json"]})
        for response in (f"```json\r\n{payload}\r\n```", f"```JSON\n{payload}\n```",
                         f"```\n{payload}\n```"):
            with self.subTest(response=response[:12]):
                self.assertEqual(parse_solution(response)["deliverables"], ["results.json"])
        for response in (f"reasoning\n```json\n{payload}\n```",
                         f"```json\n{payload}\n```\nextra",
                         f"```json\n{payload}\n```\n```json\n{payload}\n```"):
            with self.subTest(response=response[:20]), self.assertRaises(ValueError):
                parse_solution(response)

    def test_client_retry_and_environment_contract(self):
        with mock_model([(429, {}), "solution"]) as (url, requests):
            client = ModelClient(url, "test-model")
            self.assertEqual(client.complete([{"role": "user", "content": "task"}], time.monotonic() + 10), "solution")
            self.assertEqual(len(requests), 2)
            self.assertEqual(requests[0]["path"], "/v1/chat/completions")
            self.assertEqual(requests[0]["authorization"], "Bearer synthetic-test-token")
            payload = requests[0]["payload"]
            self.assertNotIn("tools", payload)
            self.assertEqual(payload["temperature"], 0)
            self.assertEqual(payload["model"], "test-model")
            self.assertEqual(payload["max_tokens"], 4000)
            self.assertGreater(client.output_tokens, 50)  # failed call reservation retained

    def test_token_required_and_origin_only(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ModelError, "MODEL_TOKEN"):
                ModelClient("http://localhost:8000", "test")
            with self.assertRaisesRegex(ModelError, "origin"):
                ModelClient("http://localhost:8000/v1", "test", "test-token")

    def test_injected_proxy_routes_request_with_both_credentials(self):
        for key in ('HTTP_PROXY', 'http_proxy'):
            with mock_model(['solution'], proxy=True) as (url, requests):
                proxy_url = url.replace('http://', 'http://proxy-user:proxy-pass@')
                with patch.dict(os.environ, {key: proxy_url, 'MODEL_TOKEN': 'synthetic-test-token'}, clear=True):
                    client = ModelClient('http://house.invalid:8443', 'house')
                    self.assertEqual(client.complete([], time.monotonic() + 5), 'solution')
                self.assertEqual(requests[0]['path'], 'http://house.invalid:8443/v1/chat/completions')
                self.assertEqual(requests[0]['authorization'], 'Bearer synthetic-test-token')
                self.assertEqual(requests[0]['proxy_authorization'], 'Basic cHJveHktdXNlcjpwcm94eS1wYXNz')

    def test_request_budget_counts_retry(self):
        with mock_model([(500, {}), "unused"]) as (url, requests):
            client = ModelClient(url, "test")
            client.requests = 24
            with self.assertRaisesRegex(ModelError, "request budget"):
                client.complete([], time.monotonic() + 10)
            self.assertEqual(client.requests, 25)
            self.assertEqual(len(requests), 1)

    def test_auth_refusals_do_not_spend_budget(self):
        for status in (401, 403):
            with mock_model([(status, {})]) as (url, requests):
                client = ModelClient(url, "test")
                with self.assertRaises(ModelError):
                    client.complete([], time.monotonic() + 5)
                self.assertEqual((client.requests, client.input_tokens, client.output_tokens), (0, 0, 0))
                self.assertEqual(len(requests), 1)

    def test_client_bad_response_and_no_config(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ModelError):
            ModelClient()
        for response in ((401, {}), (200, {}), (200, {"choices": []})):
            with mock_model([response]) as (url, requests):
                with self.assertRaises(ModelError):
                    ModelClient(url, "test").complete([], time.monotonic() + 5)
                self.assertEqual(len(requests), 1)

    def test_invalid_json_deliverable_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            (output / "results.json").write_text("not valid JSON")
            with self.assertRaises(ValueError):
                validate_outputs(output, ["results.json"])


@unittest.skipUnless(sys.platform == "linux", "Execution integration tests run in Linux Docker")
class ExecutionTests(unittest.TestCase):
    def test_worker_computes_with_installed_econometrics_libraries(self):
        snippets = {
            "statsmodels_adf": '''import numpy as np
from statsmodels.tsa.stattools import adfuller
series = np.random.default_rng(17).normal(size=120)
statistic, pvalue, usedlag, *_ = adfuller(series, maxlag=1, autolag=None)
assert np.isfinite(statistic) and 0 <= pvalue <= 1 and usedlag == 1
''',
            "arch_garch": '''import numpy as np
from arch import arch_model
series = np.random.default_rng(17).normal(size=120)
fit = arch_model(series, mean="Zero", vol="GARCH", p=1, q=1).fit(disp="off")
assert fit.convergence_flag == 0 and np.isfinite(fit.params).all()
variance = fit.forecast(horizon=1).variance.iloc[-1, 0]
assert np.isfinite(variance) and variance > 0
''',
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_task(root / "task")
            for name, code in snippets.items():
                with self.subTest(library=name):
                    out = root / name
                    out.mkdir()
                    script = root / (name + ".py")
                    script.write_text(code, encoding="utf-8")
                    result = execute(script, root / "task", out, time.monotonic() + 30)
                    self.assertEqual(result["returncode"], 0, result["log"])
                    self.assertFalse(result["timed_out"])

    def test_parquet_output_and_missing_deliverable_repair(self):
        missing = json.dumps({"code": "print('no output yet')", "deliverables": ["results.json"]})
        code = CODE + '\ndata.to_parquet(out / "data.parquet", index=False)\n'
        good = json.dumps({"code": code, "deliverables": ["results.json", "data.parquet"]})
        with tempfile.TemporaryDirectory() as temp, mock_model([missing, good]) as (url, requests):
            root = Path(temp)
            make_task(root / "task")
            env = {**os.environ, "MODEL_ENDPOINT": url, "MODEL_NAME": "synthetic-test", "NO_PROXY": "127.0.0.1"}
            result = subprocess.run([sys.executable, "-m", "agent", "solve", "--task-dir", str(root / "task"), "--out", str(root / "out")], env=env, capture_output=True, text=True, timeout=35)
            self.assertEqual(result.returncode, 0, result.stderr)
            import pandas as pd
            self.assertEqual(pd.read_parquet(root / "out/data.parquet")["value"].tolist(), [2, 3])
            self.assertIn("Missing or empty", requests[1]["payload"]["messages"][-1]["content"])

    def test_real_cli_repairs_and_preserves_inputs(self):
        bad = json.dumps({"code": "raise ValueError('intentional repair test')", "deliverables": ["results.json"]})
        good = json.dumps({"code": CODE, "deliverables": ["results.json"]})
        with tempfile.TemporaryDirectory() as temp, mock_model([bad, good]) as (url, requests):
            root = Path(temp)
            make_task(root / "task")
            before = {str(p): p.read_bytes() for p in (root / "task").rglob("*") if p.is_file()}
            env = {**os.environ, "MODEL_ENDPOINT": url, "MODEL_NAME": "synthetic-test", "NO_PROXY": "127.0.0.1"}
            result = subprocess.run([sys.executable, "-m", "agent", "solve", "--task-dir", str(root / "task"), "--out", str(root / "out")], env=env, capture_output=True, text=True, timeout=35)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((root / "out/results.json").read_text()), {"total": 5})
            report = json.loads((root / "out/.agent/run.json").read_text())
            self.assertEqual(report["status"], "completed_unverified")
            self.assertEqual(len(report["attempts"]), 2)
            self.assertIn("intentional repair test", requests[1]["payload"]["messages"][-1]["content"])
            self.assertEqual(before, {str(p): p.read_bytes() for p in (root / "task").rglob("*") if p.is_file()})
            self.assertFalse(list((root / "out").rglob("reward.json")))

    def test_worker_denies_reward_input_write_network_and_checks(self):
        snippets = [
            'open("reward.json", "w").write("1")',
            'import os; open(os.environ["TASK_DIR"] + "/data.csv", "w").write("bad")',
            'import socket; socket.socket()',
            'import ctypes; ctypes.CDLL("libc.so.6")',
            'import os; open(os.environ["TASK_DIR"] + "/checks/hidden.txt").read()',
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_task(root / "task")
            for index, code in enumerate(snippets):
                with self.subTest(code=code):
                    out = root / str(index)
                    out.mkdir()
                    script = root / "code.py"
                    script.write_text(code)
                    result = execute(script, root / "task", out, time.monotonic() + 10)
                    self.assertNotEqual(result["returncode"], 0, result)
                    self.assertIn("PermissionError", result["log"])
                    self.assertFalse((out / "reward.json").exists())

    def test_execution_timeout_and_log_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_task(root / "task")
            out = root / "out"
            out.mkdir()
            script = root / "code.py"
            script.write_text('print("x" * 100000);\nwhile True: pass\n')
            result = execute(script, root / "task", out, time.monotonic() + 3)
            self.assertTrue(result["timed_out"])
            self.assertLessEqual(len(result["log"]), 65536)

    def test_missing_model_fails_without_fake_deliverable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_task(root / "task")
            env = {k: v for k, v in os.environ.items() if k not in ("MODEL_ENDPOINT", "MODEL_NAME")}
            result = subprocess.run([sys.executable, "-m", "agent", "solve", "--task-dir", str(root / "task"), "--out", str(root / "out")], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('"event": "agent_failed"', result.stderr)
            report = json.loads((root / "out/.agent/run.json").read_text())
            self.assertEqual(report["stage"], "client")
            self.assertEqual(report["status"], "failed")
            self.assertEqual([path.name for path in (root / "out").iterdir()], [".agent"])


if __name__ == "__main__":
    unittest.main()
