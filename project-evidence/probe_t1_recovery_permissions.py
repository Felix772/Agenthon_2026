"""Linux-only synthetic full CLI watchdog probe; leaves a tree for another UID.

The test supervisor injects a 10s outer deadline while the child retains its
normal 360s budget, so optional review actually starts before it is interrupted.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from test_semantic import GOOD, SOLVER_CODE, setup

root = Path("/tmp/recovery-probe")
root.mkdir(mode=0o755)
task, _ = setup(root)
out = root / "fresh"
calls = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        calls.append(1)
        if len(calls) > 1:
            time.sleep(30)
        response = {"code": SOLVER_CODE, "deliverables": ["results.json"]}
        body = {"choices": [{"message": {"content": json.dumps(response)}, "finish_reason": "stop"}]}
        try:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())
        except OSError:
            pass

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
server.daemon_threads = True
threading.Thread(target=server.serve_forever, daemon=True).start()
wrapper = """import os,time
from agent import cli
original=cli.run_guarded
def early(command,out,deadline,**kwargs):
    return original(command,out,min(deadline,time.monotonic()+10),**kwargs)
cli.run_guarded=early
os.umask(0o077)
raise SystemExit(cli.main())
"""
env = {**os.environ, "AGENT_E1_SEMANTIC": "1", "AGENT_C3_REVIEW": "0",
       "AGENT_SOFT_TIMEOUT_SEC": "360", "MODEL_ENDPOINT": f"http://127.0.0.1:{server.server_port}",
       "MODEL_NAME": "synthetic", "MODEL_TOKEN": "synthetic-test-token",
       "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
result = subprocess.run([sys.executable, "-c", wrapper, "solve", "--task-dir", str(task.root),
                         "--out", str(out)], env=env, capture_output=True, text=True, timeout=25)
assert result.returncode == 0, result.stderr
assert len(calls) == 2, calls
assert out.joinpath(".agent/semantic-fallback.json").is_file()
assert json.loads(out.joinpath("results.json").read_text()) == GOOD
report = {"passed": True, "writer_uid": os.getuid(), "cli_exit_code": result.returncode,
          "synthetic_calls": len(calls), "outer_watchdog_recovery": True, "umask": "077",
          "tree": str(out)}
Path("/report/probe.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report), flush=True)
server.shutdown()
server.server_close()
time.sleep(300)
