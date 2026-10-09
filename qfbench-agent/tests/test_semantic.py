"""Synthetic instruction-derived defects; no House or finance score claims."""
import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent.cli import _semantic_fallback, run_guarded
from agent.execution import execute
from agent.model_client import ModelClient, ModelError
from agent.semantic import freeze_baseline, parse_checker, restore_baseline, review_candidate, run_checker
from agent.solver import solve
from mock_server import mock_model

INSTRUCTION = """Read inputs.json and write results.json.
Convert basis_points to decimal rates by dividing by 10000.
Keep rows in the exact input id order.
Include every input group exactly once in group_totals.
For each row, exposure must equal rate times notional.
Use absolute numerical tolerance 1e-10.
"""
DATA = [{"id": "a", "group": "x", "basis_points": 200, "notional": 100},
        {"id": "b", "group": "y", "basis_points": 300, "notional": 200}]
GOOD = {"rows": [{"id": "a", "rate": .02, "notional": 100, "exposure": 2},
                 {"id": "b", "rate": .03, "notional": 200, "exposure": 6}],
        "group_totals": {"x": 2, "y": 6}}
SOLVER_CODE = '''import json, os
from pathlib import Path
data=json.loads((Path(os.environ["TASK_DIR"])/"inputs.json").read_text())
rows=[dict(id=r["id"],rate=r["basis_points"]/10000,notional=r["notional"],
           exposure=r["basis_points"]/10000*r["notional"]) for r in data]
groups={r["group"]:sum(x["basis_points"]/10000*x["notional"] for x in data if x["group"]==r["group"]) for r in data}
(Path(os.environ["OUTPUT_DIR"])/"results.json").write_text(json.dumps(dict(rows=rows,group_totals=groups)))
'''
CHECKER_CODE = '''import json, os, math
from pathlib import Path
data=json.loads((Path(os.environ["TASK_DIR"])/"inputs.json").read_text())
result=json.loads((Path(os.environ["CANDIDATE_DIR"])/"results.json").read_text())
rows=result["rows"]
expected={r["id"]:r["basis_points"]/10000 for r in data}
check("unit_factor", all(math.isclose(r["rate"],expected[r["id"]],rel_tol=0,abs_tol=1e-10) for r in rows))
check("row_alignment", [r["id"] for r in rows]==[r["id"] for r in data])
check("missing_group", set(result["group_totals"])=={r["group"] for r in data})
check("explicit_identity", all(math.isclose(r["exposure"],r["rate"]*r["notional"],rel_tol=0,abs_tol=1e-10) for r in rows))
'''
IDS = ["unit_factor", "row_alignment", "missing_group", "explicit_identity"]
CHECKER = {"checks": [{"id": identifier, "requirement": line}
                       for identifier, line in zip(IDS, INSTRUCTION.splitlines()[1:5])], "code": CHECKER_CODE}


def wrong(kind):
    value = copy.deepcopy(GOOD)
    if kind == "unit_factor":
        value["rows"][0]["rate"] *= 100
    elif kind == "row_alignment":
        value["rows"].reverse()
    elif kind == "missing_group":
        del value["group_totals"]["y"]
    elif kind == "explicit_identity":
        value["rows"][0]["exposure"] += 1
    return value


def setup(root, value=GOOD):
    task, out = root / "task", root / "output"
    task.mkdir()
    out.mkdir()
    (out / ".agent").mkdir()
    (task / "instruction.md").write_text(INSTRUCTION)
    (task / "card.toml").write_text('[agent]\ntimeout_sec=1200\n')
    (task / "inputs.json").write_text(json.dumps(DATA))
    (task / "checks").mkdir()
    (task / "checks/hidden.txt").write_text("sealed")
    (out / "results.json").write_text(json.dumps(value))
    return SimpleNamespace(root=task, instruction=INSTRUCTION, redact=lambda value: value), out


def trusted_fixture_execute(script, task, output, deadline, *, candidate_dir=None, checker_ids=None, candidate_root=None):
    """Host logic test only. Actual audit hook tests use Linux execute below."""
    results = {}
    env = {"TASK_DIR": str(task), "OUTPUT_DIR": str(output)}
    if candidate_dir is not None:
        env["CANDIDATE_DIR"] = str(candidate_dir)
    def check(identifier, passed):
        results[identifier] = passed
    with patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()):
        exec(compile(script.read_text(), str(script), "exec"), {"check": check})
    return {"returncode": 0, "timed_out": False, "elapsed_sec": .001,
            "log": "", "checker_results": results if candidate_dir is not None else None}


class SemanticTests(unittest.TestCase):
    def publication_failure(self, root, *, fail_restore):
        """Exercise real two-file publication; only model/execution are scripted."""
        task, _ = setup(root)
        out = root / "fresh"
        task.timeout = 1200
        task.instruction += "Write notes.txt with the calculation summary.\n"
        bad_code = SOLVER_CODE + '\np=Path(os.environ["OUTPUT_DIR"])/"results.json"\nr=json.loads(p.read_text())\nr["rows"][0]["rate"]*=100\np.write_text(json.dumps(r))\n(Path(os.environ["OUTPUT_DIR"])/"notes.txt").write_text("original")\n'
        repair_code = SOLVER_CODE + '\n(Path(os.environ["OUTPUT_DIR"])/"notes.txt").write_text("repaired")\n'
        names = ["results.json", "notes.txt"]
        client = ModelClient("http://localhost:1", "synthetic", "synthetic-token")
        real_replace = os.replace
        calls, original = [], {}
        def replace(source, destination):
            source = Path(source)
            if source.parent == out / ".agent/semantic/candidate":
                if not calls:
                    original.update({name: (out / name).read_bytes() for name in names})
                calls.append(source.name)
                if len(calls) == 2:
                    raise OSError("synthetic second-file publication failure")
            if fail_restore and source.parent == out / ".agent/semantic-original" and source.name.startswith("restore-"):
                raise OSError("synthetic recovery failure")
            return real_replace(source, destination)
        def validate(directory, declared, contract, whole_output, deadline):
            from agent.workspace import validate_outputs
            validate_outputs(directory, declared, contract)
        messages = [{"role": "user", "content": json.dumps({"instruction": task.instruction})}]
        from agent.cli import main
        with patch.dict(os.environ, {"AGENT_E1_SEMANTIC": "1", "AGENT_C3_REVIEW": "0", "AGENT_MAX_ATTEMPTS": "3"}), \
                patch("agent.solver.ModelClient", return_value=client), \
                patch.object(client, "complete", return_value=json.dumps({"code": bad_code, "deliverables": names})) as initial, \
                patch("agent.solver.read_task", return_value=task), patch("agent.solver.initial_messages", return_value=messages), \
                patch("agent.solver.execute", side_effect=trusted_fixture_execute), \
                patch("agent.semantic.execute", side_effect=trusted_fixture_execute), \
                patch("agent.semantic.bounded_complete", side_effect=[json.dumps(CHECKER), json.dumps({"code": repair_code, "deliverables": names})]), \
                patch("agent.semantic.bounded_validate", side_effect=validate), patch("os.replace", side_effect=replace):
            code = main(["solve", "--task-dir", str(task.root), "--out", str(out), "--internal-worker"])
        return code, json.loads((out / ".agent/run.json").read_text()), out, original, calls, initial.call_count

    def test_two_file_publication_failure_restores_both_originals(self):
        with tempfile.TemporaryDirectory() as temp:
            code, report, out, original, calls, generations = self.publication_failure(Path(temp), fail_restore=False)
            self.assertEqual(calls, ["results.json", "notes.txt"])
            self.assertEqual(code, 0)
            self.assertEqual(report["status"], "completed_unverified")
            self.assertFalse(report["attempts"][0]["semantic"]["accepted"])
            self.assertEqual({name: (out / name).read_bytes() for name in original}, original)
            self.assertEqual(generations, 1)

    def test_failed_restore_is_terminal_and_cannot_report_completed(self):
        with tempfile.TemporaryDirectory() as temp:
            code, report, out, original, calls, generations = self.publication_failure(Path(temp), fail_restore=True)
            self.assertEqual(calls, ["results.json", "notes.txt"])
            self.assertNotEqual(code, 0)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["error_type"], "SemanticRecoveryError")
            self.assertNotIn("complete", [event["stage"] for event in report["events"]])
            self.assertNotEqual(out.joinpath("results.json").read_bytes(), original["results.json"])
            self.assertEqual(out.joinpath("notes.txt").read_bytes(), original["notes.txt"])
            self.assertEqual(generations, 1, "Unsafe recovery must not re-enter core generation")

    def direct(self, root, value=GOOD, responses=None, executor=trusted_fixture_execute, remaining=300):
        task, out = setup(root, value)
        responses = responses or [json.dumps(CHECKER), json.dumps({"code": SOLVER_CODE, "deliverables": ["results.json"]})]
        with mock_model(responses) as (url, requests), patch.dict(os.environ, {"MODEL_ENDPOINT": url, "MODEL_NAME": "synthetic"}), \
                patch("agent.semantic.execute", side_effect=executor):
            client = ModelClient()
            record = review_candidate(client=client, task=task, out=out,
                solution={"code": "ORIGINAL_SOURCE_SENTINEL", "deliverables": ["results.json"]}, contract={},
                base_messages=[{"role": "user", "content": json.dumps({"instruction": INSTRUCTION})}],
                work_deadline=time.monotonic() + remaining, redact=lambda value: value)
        return record, out, requests, client

    def test_four_defects_are_detected_and_repaired_from_input(self):
        for kind in IDS:
            with self.subTest(defect=kind), tempfile.TemporaryDirectory() as temp:
                record, out, requests, client = self.direct(Path(temp), wrong(kind))
                self.assertTrue(record["accepted"], record)
                self.assertFalse(record["baseline"]["checks"][kind])
                self.assertTrue(all(record["repaired"]["checks"].values()))
                self.assertEqual(json.loads((out / "results.json").read_text()), GOOD)
                self.assertEqual(len(requests), 2)
                self.assertEqual(client.sends, 2)
                self.assertNotIn("ORIGINAL_SOURCE_SENTINEL", json.dumps(requests[0]))
                self.assertEqual(record["house_checker_calls"], 1)

    def test_correct_and_tolerance_edge_candidates_keep_exact_bytes(self):
        for offset in (0, 5e-11):
            value = copy.deepcopy(GOOD)
            value["rows"][0]["exposure"] += offset
            with self.subTest(offset=offset), tempfile.TemporaryDirectory() as temp:
                record, out, requests, _ = self.direct(Path(temp), value)
                self.assertEqual(record["reason"], "checks_passed")
                self.assertEqual(out.joinpath("results.json").read_text(), json.dumps(value))
                self.assertEqual(len(requests), 1)

    def test_budget_skip_and_invalid_checker_preserve_original(self):
        for remaining, response, expected in [(80, json.dumps(CHECKER), "budget"),
                (300, "broken JSON", "invalid_review"),
                (300, json.dumps({"checks": [], "code": ""}), "no_supported_check")]:
            with self.subTest(reason=expected), tempfile.TemporaryDirectory() as temp:
                value = wrong("missing_group")
                record, out, requests, _ = self.direct(Path(temp), value, [response], remaining=remaining)
                self.assertEqual(record["reason"], expected)
                self.assertEqual(out.joinpath("results.json").read_text(), json.dumps(value))
                self.assertEqual(len(requests), 0 if expected == "budget" else 1)

    def test_http_error_or_timeout_keeps_published_bytes(self):
        for category in ("review_timeout", "http_permanent"):
            with self.subTest(category=category), tempfile.TemporaryDirectory() as temp, \
                    patch("agent.semantic.bounded_complete", side_effect=ModelError("synthetic", category=category)):
                record, out, requests, _ = self.direct(Path(temp))
                self.assertEqual(record["reason"], category)
                self.assertEqual(out.joinpath("results.json").read_text(), json.dumps(GOOD))
                self.assertEqual(len(requests), 0)

    def test_invalid_or_still_wrong_repair_keeps_original(self):
        responses = ["not JSON", json.dumps({"code": SOLVER_CODE, "deliverables": ["other.json"]}),
                     json.dumps({"code": 'print("missing")', "deliverables": ["results.json"]})]
        for response in responses:
            with self.subTest(response=response[:30]), tempfile.TemporaryDirectory() as temp:
                value = wrong("unit_factor")
                record, out, _, _ = self.direct(Path(temp), value, [json.dumps(CHECKER), response])
                self.assertFalse(record["accepted"])
                self.assertEqual(out.joinpath("results.json").read_text(), json.dumps(value))

    def test_unanchored_or_incomplete_checker_is_rejected(self):
        for changes in ({"checks": [{"id": "guess", "requirement": "Assume volatility is constant"}]},
                        {"code": "("}, {"checks": [], "code": "print(1)"}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, SyntaxError)):
                parse_checker(json.dumps({**CHECKER, **changes}), INSTRUCTION)

    def test_request_budget_reserves_repair_and_never_exceeds_25(self):
        with tempfile.TemporaryDirectory() as temp, mock_model([json.dumps(CHECKER)]) as (url, requests):
            task, out = setup(Path(temp))
            client = ModelClient(url, "synthetic")
            client.sends = client.requests = 24
            record = review_candidate(client=client, task=task, out=out, solution={"deliverables": ["results.json"]},
                contract={}, base_messages=[], work_deadline=time.monotonic() + 300, redact=lambda value: value)
            self.assertEqual(record["reason"], "budget")
            self.assertEqual(client.sends, 24)
            self.assertEqual(requests, [])

    def test_snapshot_restore_requires_authenticated_unchanged_manifest_and_data(self):
        with tempfile.TemporaryDirectory() as temp:
            _, out = setup(Path(temp))
            proof = freeze_baseline(out, ["results.json"])
            out.joinpath("results.json").write_text("changed")
            with self.assertRaises(ValueError):
                restore_baseline(out, "0" * 64)
            restore_baseline(out, proof)
            self.assertEqual(out.joinpath("results.json").read_text(), json.dumps(GOOD))
            out.joinpath(".agent/semantic-original/results.json").write_text("tampered")
            with self.assertRaises(ValueError):
                restore_baseline(out, proof)

    def test_recovery_cleans_large_scratch_and_handles_temporary_name_collision(self):
        with tempfile.TemporaryDirectory() as temp:
            _, out = setup(Path(temp))
            out.joinpath("x").write_text("first")
            out.joinpath("x.semantic-restore").write_text("second")
            proof = freeze_baseline(out, ["x.semantic-restore", "x", "results.json"])
            scratch = out / ".agent/semantic/check-scratch"
            scratch.mkdir(parents=True)
            with (scratch / "large.bin").open("wb") as handle:
                handle.truncate(64 * 1024 * 1024)
            out.joinpath("x").write_text("bad")
            out.joinpath("x.semantic-restore").write_text("bad")
            restore_baseline(out, proof)
            self.assertEqual(out.joinpath("x").read_text(), "first")
            self.assertEqual(out.joinpath("x.semantic-restore").read_text(), "second")
            self.assertFalse(scratch.exists())

    def test_feature_default_off_uses_one_generation_and_identical_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task, unused = setup(root)
            target = root / "fresh"
            response = json.dumps({"code": SOLVER_CODE, "deliverables": ["results.json"]})
            with mock_model([response]) as (url, requests), patch.dict(os.environ,
                    {"MODEL_ENDPOINT": url, "MODEL_NAME": "synthetic", "AGENT_E1_SEMANTIC": "0"}), \
                    patch("agent.solver.execute", side_effect=trusted_fixture_execute):
                solve(task.root, target)
            self.assertEqual(json.loads(target.joinpath("results.json").read_text()), GOOD)
            self.assertEqual(len(requests), 1)
            self.assertFalse(target.joinpath(".agent/semantic-original.json").exists())


@unittest.skipUnless(sys.platform == "linux", "Actual guarded worker requires Linux")
class SemanticLinuxTests(unittest.TestCase):
    def test_full_cli_repairs_with_real_workers_and_finalizes_modes(self):
        bad_code = SOLVER_CODE + '\np=Path(os.environ["OUTPUT_DIR"])/"results.json"\nr=json.loads(p.read_text())\nr["rows"][0]["rate"]*=100\np.write_text(json.dumps(r))\n'
        responses = [json.dumps({"code": bad_code, "deliverables": ["results.json"]}),
                     json.dumps(CHECKER), json.dumps({"code": SOLVER_CODE, "deliverables": ["results.json"]})]
        with tempfile.TemporaryDirectory() as temp, mock_model(responses) as (url, requests):
            root = Path(temp)
            task, _ = setup(root)
            out = root / "fresh"
            env = {**os.environ, "MODEL_ENDPOINT": url, "MODEL_NAME": "synthetic",
                   "AGENT_E1_SEMANTIC": "1", "AGENT_C3_REVIEW": "0", "AGENT_MAX_ATTEMPTS": "1"}
            command = [sys.executable, "-c", 'import os; os.umask(0o077); from agent.cli import main; raise SystemExit(main())',
                       "solve", "--task-dir", str(task.root), "--out", str(out)]
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=50)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(out.joinpath("results.json").read_text()), GOOD)
            report = json.loads(out.joinpath(".agent/run.json").read_text())
            self.assertTrue(report["attempts"][0]["semantic"]["accepted"], report)
            self.assertEqual(len(requests), 3)
            self.assertEqual(report["model_usage"]["sends"], 3)
            for path in out.rglob("*"):
                self.assertTrue(path.stat().st_mode & 0o004)
                if path.is_dir():
                    self.assertTrue(path.stat().st_mode & 0o001)

    def test_missing_supervision_disables_optional_work_without_failing_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            read_fd, write_fd = os.pipe()
            try:
                with patch("agent.cli._subreaper", side_effect=OSError("unavailable")), \
                        patch.dict(os.environ, {"AGENT_E1_SEMANTIC": "1"}):
                    code = run_guarded([sys.executable, "-c", 'import os; raise SystemExit(42 if os.environ.get("AGENT_E1_SEMANTIC")=="1" else 0)'],
                                       Path(temp), time.monotonic() + 5, semantic_fallback_fd=read_fd)
                self.assertEqual(code, 0)
            finally:
                os.close(read_fd)
                os.close(write_fd)

    def test_second_checker_cannot_read_sibling_solver_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task, out = setup(root)
            directory = out / ".agent/semantic"
            candidate = directory / "candidate"
            candidate.mkdir(parents=True)
            candidate.joinpath("results.json").write_text(json.dumps(GOOD))
            directory.joinpath("repair.py").write_text("private source")
            script = root / "checker.py"
            script.write_text('import os\nfrom pathlib import Path\n(Path(os.environ["CANDIDATE_DIR"]).parent/"repair.py").read_text()\ncheck("test",True)')
            checks, record = run_checker({"ids": ["test"]}, script, task, candidate, root / "scratch",
                                         time.monotonic() + 20, candidate_root=out)
            self.assertIsNone(checks)
            self.assertEqual(record["reason"], "checker_execution")

    def test_fallback_preserves_executable_deliverable(self):
        with tempfile.TemporaryDirectory() as temp:
            _, out = setup(Path(temp))
            script = out / "run.sh"
            script.write_text("#!/bin/sh\nexit 0\n")
            script.chmod(0o700)
            proof = freeze_baseline(out, ["run.sh", "results.json"])
            script.write_text("changed")
            script.chmod(0o600)
            restore_baseline(out, proof)
            self.assertEqual(script.read_text(), "#!/bin/sh\nexit 0\n")
            self.assertEqual(script.stat().st_mode & 0o111, 0o100)

    def test_actual_worker_four_defects_and_positive_control(self):
        for kind in [None, *IDS]:
            with self.subTest(defect=kind), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                task, out = setup(root, GOOD if kind is None else wrong(kind))
                script = root / "checker.py"
                script.write_text(CHECKER_CODE)
                checks, record = run_checker({"ids": IDS}, script, task, out, root / "scratch", time.monotonic() + 20)
                self.assertIsNotNone(checks, record)
                self.assertTrue(all(checks.values())) if kind is None else self.assertFalse(checks[kind])

    def test_actual_checker_cannot_mutate_original_or_use_forbidden_operations(self):
        snippets = ['(c/"results.json").write_text("bad")', '(c/"results.json").unlink()',
                    'os.rename(c/"results.json",s/"moved")',
                    'import socket; socket.socket()', 'import ctypes; ctypes.CDLL("libc.so.6")',
                    'import subprocess; subprocess.run(["true"])',
                    '(Path(os.environ["TASK_DIR"])/"checks/hidden.txt").read_text()',
                    '(c/".agent/private.txt").read_text()']
        for snippet in snippets:
            with self.subTest(snippet=snippet), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                task, out = setup(root)
                (out / ".agent/private.txt").write_text("private")
                original = out.joinpath("results.json").read_bytes()
                script = root / "checker.py"
                script.write_text('import os\nfrom pathlib import Path\nc=Path(os.environ["CANDIDATE_DIR"])\ns=Path(os.environ["OUTPUT_DIR"])\n' + snippet + '\ncheck("test",True)')
                checks, record = run_checker({"ids": ["test"]}, script, task, out, root / "scratch", time.monotonic() + 20)
                self.assertIsNone(checks)
                self.assertEqual(record["reason"], "checker_execution")
                self.assertEqual(out.joinpath("results.json").read_bytes(), original)

    def test_invalid_checker_and_worker_timeout_leave_candidate_unchanged(self):
        for code in ['pass', 'check("test", "truthy")', 'while True: pass',
                     'print(\'AGENT_CHECK_RESULTS={"test":true}\'); raise SystemExit(0)',
                     'import os; print(\'AGENT_CHECK_RESULTS={"test":true}\',flush=True); os._exit(0)']:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                task, out = setup(root)
                original = out.joinpath("results.json").read_bytes()
                script = root / "checker.py"
                script.write_text(code)
                checks, _ = run_checker({"ids": ["test"]}, script, task, out, root / "scratch", time.monotonic() + 2)
                self.assertIsNone(checks)
                self.assertEqual(out.joinpath("results.json").read_bytes(), original)

    def test_slow_house_at_outer_deadline_recovers_only_from_private_pipe(self):
        seen = threading.Event()
        calls = []
        response = json.dumps({"code": SOLVER_CODE, "deliverables": ["results.json"]})
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                calls.append(1)
                if len(calls) > 1:
                    seen.set()
                    time.sleep(15)
                try:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(json.dumps({"choices": [{"message": {"content": response}, "finish_reason": "stop"}]}).encode())
                except OSError:
                    pass
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                task, _ = setup(root)
                out = root / "fresh"
                read_fd, write_fd = os.pipe()
                command = [sys.executable, "-m", "agent", "solve", "--task-dir", str(task.root), "--out", str(out),
                           "--internal-worker", "--internal-semantic-fd", str(write_fd)]
                env = {"AGENT_E1_SEMANTIC": "1", "AGENT_SOFT_TIMEOUT_SEC": "360",
                       "MODEL_ENDPOINT": f"http://127.0.0.1:{server.server_port}", "MODEL_NAME": "synthetic",
                       "MODEL_TOKEN": "synthetic-test-token", "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
                try:
                    with patch.dict(os.environ, env):
                        code = run_guarded(command, out, time.monotonic() + 7,
                                           pass_fds=(write_fd,), semantic_fallback_fd=read_fd)
                    self.assertTrue(seen.is_set(), "The optional House call must actually have started")
                    self.assertEqual(code, 0)
                    self.assertEqual(json.loads(out.joinpath("results.json").read_text()), GOOD)
                    self.assertTrue(out.joinpath(".agent/semantic-fallback.json").is_file())
                finally:
                    os.close(write_fd)
                    os.close(read_fd)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_marker_file_without_private_pipe_cannot_convert_timeout_to_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, out = setup(root)
            freeze_baseline(out, ["results.json"])
            self.assertFalse(_semantic_fallback(out, None))
            read_fd, write_fd = os.pipe()
            try:
                self.assertFalse(_semantic_fallback(out, read_fd))
                code = run_guarded([sys.executable, "-c", "import time; time.sleep(10)"], out,
                                   time.monotonic() + .2, semantic_fallback_fd=read_fd)
                self.assertEqual(code, 124)
            finally:
                os.close(read_fd)
                os.close(write_fd)

    def test_normal_nonzero_exit_kills_adopted_worker_before_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, out = setup(root)
            proof = freeze_baseline(out, ["results.json"])
            read_fd, write_fd = os.pipe()
            script = ('import os, subprocess, sys\nfrom pathlib import Path\n'
                f'os.write({write_fd}, {proof.encode()!r})\n'
                'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"], start_new_session=True)\n'
                f'Path({str(out / ".agent/orphan-pid")!r}).write_text(str(child.pid))\n'
                'os._exit(7)\n')
            try:
                code = run_guarded([sys.executable, "-c", script], out, time.monotonic() + 10,
                                   pass_fds=(write_fd,), semantic_fallback_fd=read_fd)
                self.assertEqual(code, 0)
                orphan = int(out.joinpath(".agent/orphan-pid").read_text())
                self.assertFalse(Path(f"/proc/{orphan}").exists())
                self.assertEqual(json.loads(out.joinpath("results.json").read_text()), GOOD)
            finally:
                os.close(read_fd)
                os.close(write_fd)


if __name__ == "__main__":
    unittest.main()
