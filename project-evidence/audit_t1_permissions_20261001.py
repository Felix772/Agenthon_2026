"""Synthetic exact-image T1 output permission audit; no House, build or release."""
from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1] if len(Path(__file__).resolve().parents) > 1 else Path("/")
INCUMBENT = "sha256:b8602023ca154d7879186f91f1e9a23ec43a9b0d916b18c9a6ae8c53947250f3"
NAMES = ["results.json", "tables/values.csv", "tables/deep/values.parquet", "scripts/solution.py", "scripts/runner.sh"]


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def serve(mode, private):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.path != "/v1/chat/completions" or self.headers.get("Authorization") != "Bearer synthetic-permission-audit":
                self.send_error(403)
                return
            if mode == "watchdog":
                time.sleep(30)
            code = """import json, os
from pathlib import Path
import pandas as pd
out = Path(os.environ['OUTPUT_DIR'])
values = pd.read_csv(Path(os.environ['TASK_DIR']) / 'values.csv')
(out / 'tables/deep').mkdir(parents=True)
(out / 'scripts').mkdir()
(out / 'results.json').write_text(json.dumps({'total': int(values['value'].sum())}))
values.to_csv(out / 'tables/values.csv', index=False)
values.to_parquet(out / 'tables/deep/values.parquet', index=False)
(out / 'scripts/solution.py').write_text('def total(values):\\n    return sum(values)\\n')
(out / 'scripts/runner.sh').write_text('#!/bin/sh\\nexit 0\\n')
(out / 'scripts/runner.sh').chmod(0o755)
"""
            if private:
                code += "for path in out.rglob('*'):\n    path.chmod(0o700 if path.is_dir() else (0o755 if path.suffix == '.sh' else 0o600))\n"
            content = "malformed synthetic response" if mode == "failure" else json.dumps({"code": code, "deliverables": NAMES})
            response = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 100, "completion_tokens": 200}}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                self.wfile.write(json.dumps(response).encode())
            except BrokenPipeError:
                pass

        def log_message(self, *args):
            pass

    print("synthetic permission server ready", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()


def inspect_output():
    """Record lstat as root, then actually read every path as unrelated UID/GID."""
    root = Path("/app/output")
    paths = [root] + sorted(root.rglob("*"))
    rows = []
    for path in paths:
        info = path.lstat()
        kind = "directory" if stat.S_ISDIR(info.st_mode) else "file" if stat.S_ISREG(info.st_mode) else "unsafe"
        row = {"path": path.relative_to(root).as_posix(), "mode": oct(stat.S_IMODE(info.st_mode)),
               "uid": info.st_uid, "gid": info.st_gid, "kind": kind, "world_writable": bool(info.st_mode & 2),
               "hardlinked": kind == "file" and info.st_nlink > 1}
        if kind == "file":
            row.update(bytes=info.st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        rows.append(row)
    diagnostics = {}
    report = root / ".agent/run.json"
    if report.is_file():
        raw = json.loads(report.read_text())
        diagnostics = {key: raw.get(key) for key in ("status", "stage", "failure_category")}
    os.setgroups([])
    os.setgid(65533)
    os.setuid(65533)
    for row, path in zip(rows, paths, strict=True):
        try:
            if row["kind"] == "directory":
                list(path.iterdir())
                row["traversable"] = os.access(path, os.X_OK)
                row["readable"] = row["traversable"]
            elif row["kind"] == "file":
                with path.open("rb") as stream:
                    stream.read(1)
                row["readable"] = True
            else:
                row["readable"] = False
        except OSError as exc:
            row["readable"] = False
            row["error_type"] = type(exc).__name__
    accepted = all(row["readable"] and not row["world_writable"] and not row["hardlinked"]
                   and row["kind"] != "unsafe" for row in rows)
    formats = {}
    if accepted and all((root / name).is_file() for name in NAMES):
        import pandas as pd
        formats["json"] = json.loads((root / NAMES[0]).read_text()) == {"total": 5}
        formats["csv"] = pd.read_csv(root / NAMES[1]).to_dict("list") == {"value": [2, 3]}
        formats["parquet"] = pd.read_parquet(root / NAMES[2]).to_dict("list") == {"value": [2, 3]}
        compile((root / NAMES[3]).read_text(), NAMES[3], "exec")
        formats["python_compile"] = True
        formats["executable_as_reader_uid"] = subprocess.run([str(root / NAMES[4])], check=False).returncode == 0
    print(json.dumps({"reader_uid": os.getuid(), "reader_gid": os.getgid(), "reader_groups": os.getgroups(),
                      "all_readable_safe": accepted, "rows": rows, "diagnostics": diagnostics,
                      "cross_uid_format_checks": formats,
                      "representative_files_present": all((root / name).is_file() for name in NAMES)
                      if accepted else None}))


def docker(*args, check=True, timeout=90):
    result = subprocess.run(["docker", *map(str, args)], capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f"docker {args[0]} failed: {result.stderr[-3000:]}")
    return result


def audit(args):
    out = ROOT / "project-evidence/t13-experiments/T1-PERM-AUDIT-v1" / args.label
    out.mkdir(parents=True, exist_ok=False)
    inputs = out / "input"
    inputs.mkdir()
    (inputs / "card.toml").write_text('[agent]\ntimeout_sec = 60\n[contamination]\ncanary_guid = "synthetic-permission-sentinel"\n')
    (inputs / "instruction.md").write_text(
        "Read values.csv. Write results.json with total; tables/values.csv and tables/deep/values.parquet "
        "with the value column; scripts/solution.py with a total function and scripts/runner.sh as an executable check. Synthetic plumbing only.\n")
    (inputs / "values.csv").write_text("value\n2\n3\n")
    identity = json.loads(docker("image", "inspect", args.image).stdout)[0]
    if identity["Id"] != args.image or identity["Config"]["Entrypoint"] != ["python", "-m", "agent"]:
        raise RuntimeError("Image ID/actual participant entrypoint mismatch")
    stamp = uuid.uuid4().hex[:10]
    network, helper, participant, volume = (f"t1-perm-{kind}-{stamp}" for kind in ("net", "mock", "run", "out"))
    fixture = str(Path(__file__).resolve())
    volume_mount = f"type=volume,source={volume},target=/app/output,volume-nocopy"
    flags = ["--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=256",
             "--ulimit=nofile=1024:1024", "--ulimit=nproc=256:256", "--ulimit=fsize=67108864:67108864",
             "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=64m", "--cpus=16", "--memory=128g", "--memory-swap=128g"]
    binding = {"image_id": args.image, "incumbent": args.image == INCUMBENT, "label": args.label,
               "mode": args.mode, "umask": args.umask, "explicit_private_generated_modes": args.private,
               "entrypoint": identity["Config"]["Entrypoint"], "instrumentation": "shell sets umask then execs exact image entrypoint",
               "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "network": "internal synthetic helper only", "house_calls": 0, "owned_volume": volume}
    save(out / "binding.json", binding)
    try:
        docker("volume", "create", volume)
        docker("run", "--rm", "--network=none", "--read-only", "--user=0:0", "--mount", volume_mount,
               "--entrypoint", "python", args.image, "-c",
               "import os; os.chown('/app/output',65534,65534); os.chmod('/app/output',0o755)")
        docker("network", "create", "--internal", network)
        server = ["run", "-d", "--name", helper, "--network", network, *flags, "--user=65534:65534",
                  "--mount", f"type=bind,source={fixture},target=/fixture.py,readonly", "--entrypoint", "python",
                  args.image, "/fixture.py", "--serve", "--mode", args.mode]
        if args.private:
            server.append("--private")
        docker(*server)
        for _ in range(40):
            if "synthetic permission server ready" in docker("logs", helper).stdout:
                break
            time.sleep(.1)
        else:
            helper_log = docker("logs", helper)
            (out / "helper.log").write_text(helper_log.stdout + helper_log.stderr, encoding="utf-8")
            raise RuntimeError("Synthetic helper did not become ready: " + helper_log.stderr[-1000:])
        command = ["run", "--name", participant, "--network", network, *flags, "--user=65534:65534",
                   "--mount", volume_mount, "--mount", f"type=volume,source={volume},target=/output,volume-nocopy",
                   "--mount", f"type=bind,source={inputs},target=/input,readonly",
                   "-e", f"MODEL_ENDPOINT=http://{helper}:8000", "-e", "MODEL_NAME=synthetic-permission",
                   "-e", "MODEL_TOKEN=synthetic-permission-audit", "-e", f"NO_PROXY={helper}",
                   "-e", "QFBENCH_NETWORK=restricted", "-e", "AGENT_MAX_ATTEMPTS=1",
                   "-e", f"AGENT_SOFT_TIMEOUT_SEC={3 if args.mode == 'watchdog' else 45}",
                   "--entrypoint", "/bin/sh", args.image, "-c",
                   f"umask {args.umask}; exec python -m agent solve --task-dir /input --out /app/output"]
        result = docker(*command, check=False, timeout=70)
        (out / "participant.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        container = json.loads(docker("inspect", participant).stdout)[0]
        save(out / "runtime.json", {"state": container["State"], "host_config": {
            key: container["HostConfig"].get(key) for key in ("ReadonlyRootfs", "CapDrop", "SecurityOpt", "NanoCpus", "Memory", "MemorySwap", "PidsLimit")},
            "user": container["Config"]["User"], "command": command})
        probe = docker("run", "--rm", "--network=none", "--read-only", "--user=0:0", "--mount", volume_mount + ",readonly",
                       "--mount", f"type=bind,source={fixture},target=/fixture.py,readonly", "--entrypoint", "python",
                       args.image, "/fixture.py", "--inspect-output")
        evidence = json.loads(probe.stdout)
        evidence.update(exit_code=result.returncode, expected_exit={"success": 0, "failure": 1, "watchdog": 124}[args.mode])
        evidence["passed"] = evidence["all_readable_safe"] and result.returncode == evidence["expected_exit"]
        if args.mode == "success":
            evidence["passed"] = evidence["passed"] and evidence["representative_files_present"] and all(evidence["cross_uid_format_checks"].values())
        evidence["audit_expectation_met"] = (not evidence["all_readable_safe"] and result.returncode == evidence["expected_exit"]) if args.expect_permission_failure else evidence["passed"]
        save(out / "summary.json", evidence)
        print(json.dumps({"label": args.label, "exit_code": result.returncode, "passed": evidence["passed"],
                          "entries": len(evidence["rows"]), "unreadable": [row["path"] for row in evidence["rows"] if not row["readable"]]}))
    finally:
        for name in (participant, helper):
            docker("rm", "-f", name, check=False)
        docker("network", "rm", network, check=False)
        docker("volume", "rm", volume, check=False)
    return 0 if evidence["audit_expectation_met"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=INCUMBENT)
    parser.add_argument("--label")
    parser.add_argument("--mode", choices=("success", "failure", "watchdog"), default="success")
    parser.add_argument("--umask", choices=("022", "077"), default="077")
    parser.add_argument("--private", action="store_true")
    parser.add_argument("--expect-permission-failure", action="store_true")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--inspect-output", action="store_true")
    options = parser.parse_args()
    if options.serve:
        serve(options.mode, options.private)
    elif options.inspect_output:
        inspect_output()
    elif options.label:
        raise SystemExit(audit(options))
    else:
        parser.error("--label is required for an audit")
