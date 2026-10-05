import argparse
import json
import math
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

def _kill_descendants(pid):
    """Kill only descendants of this invocation, including separate worker groups."""
    if sys.platform != "linux":
        return
    try:
        children = (Path("/proc") / str(pid) / "task" / str(pid) / "children").read_text().split()
    except OSError:
        children = []
    for child in children:
        child_pid = int(child)
        _kill_descendants(child_pid)
        try:
            os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_guarded(command, out, deadline, *, pass_fds=()):
    """Parent-process wall clock, independent of child I/O and the Python GIL."""
    proc = subprocess.Popen(command, start_new_session=sys.platform == "linux", pass_fds=pass_fds)
    try:
        return proc.wait(timeout=max(.001, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        _kill_descendants(proc.pid)
        proc.kill()
        proc.wait(timeout=2)
        evidence = {"event": "outer_watchdog_timeout", "exit_code": 124, "timed_out": True,
                    "scope": "complete CLI child including task discovery, input inspection, HTTP, execution and publication"}
        print(json.dumps(evidence), file=sys.stderr, flush=True)
        # The child's most recent run.json remains intact. This separate file
        # records termination even if the child could not update its report.
        report_dir = out / ".agent"
        if report_dir.is_dir() and not report_dir.is_symlink():
            try:
                from .workspace import write_json
                write_json(report_dir / "outer-watchdog.json", evidence)
            except OSError:
                pass
        return 124
    finally:
        if proc.poll() is None:
            _kill_descendants(proc.pid)
            proc.kill()
            proc.wait(timeout=2)


def main(argv=None):
    started = time.monotonic()
    parser = argparse.ArgumentParser(description="QFBench coding agent")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("solve")
    command.add_argument("--task-dir", type=Path, required=True)
    command.add_argument("--out", type=Path, required=True)
    command.add_argument("--internal-worker", action="store_true", help=argparse.SUPPRESS)
    command.add_argument("--internal-output-fd", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    print(json.dumps({"event": "agent_startup", "schema_version": 2}), file=sys.stderr, flush=True)
    try:
        if not args.internal_worker:
            timeout = float(os.environ.get("AGENT_SOFT_TIMEOUT_SEC", "360"))
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("AGENT_SOFT_TIMEOUT_SEC must be positive and finite")
            command = [sys.executable, "-m", "agent", "solve", "--task-dir", str(args.task_dir),
                       "--out", str(args.out), "--internal-worker"]
            if os.name != "posix":
                return run_guarded(command, args.out, started + timeout)
            read_fd, write_fd = os.pipe()
            command.extend(["--internal-output-fd", str(write_fd)])
            try:
                return run_guarded(command, args.out, started + timeout, pass_fds=(write_fd,))
            finally:
                os.close(write_fd)
                try:
                    accepted = os.read(read_fd, 4096)
                finally:
                    os.close(read_fd)
                # Only prepare_output can grant permission to finalize this
                # invocation's tree. Rejected paths never send a receipt.
                if accepted:
                    from .workspace import finalize_output_permissions
                    receipt = json.loads(accepted)
                    finalized_out = Path(receipt["path"])
                    info = finalized_out.lstat()
                    if finalized_out != args.out.resolve() or [info.st_dev, info.st_ino] != receipt["identity"]:
                        raise ValueError("Accepted output directory identity changed")
                    finalize_output_permissions(finalized_out)
        # Keep the supervising parent small: import the input/solver stack only
        # in the child whose whole lifetime is bounded by the parent watchdog.
        from . import workspace
        workspace._output_acceptance_fd = args.internal_output_fd
        from .solver import solve
        solve(args.task_dir, args.out)
    except Exception as exc:
        print(json.dumps({"event": "agent_failed", "error_type": type(exc).__name__,
                          "diagnostics": ".agent/run.json"}), file=sys.stderr, flush=True)
        return 1
    return 0
