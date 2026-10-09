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


def _semantic_fallback(out, descriptor):
    if descriptor is None:
        return False
    try:
        os.set_blocking(descriptor, False)
        proof = os.read(descriptor, 65).decode("ascii")
        if len(proof) != 64:
            return False
        from .semantic import restore_baseline
        from .workspace import write_json
        names = restore_baseline(out.resolve(), proof)
        write_json(out / ".agent" / "semantic-fallback.json", {
            "status": "completed_unverified", "reason": "optional_work_interrupted",
            "original_deliverables_restored": names, "numerical_correctness": "unknown"})
        return True
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        return False


def _children(pid):
    try:
        return {int(value) for value in (Path("/proc") / str(pid) / "task" / str(pid) / "children").read_text().split()}
    except OSError:
        return set()


def _subreaper():
    # Only the trusted E1 supervisor uses this fixed Linux process-control API.
    # Generated code retains its unconditional ctypes/process restrictions.
    if _children(os.getpid()):
        raise OSError("Semantic supervision requires an exclusive CLI process")
    import ctypes
    library = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if library.prctl(37, ctypes.byref(previous), 0, 0, 0) or library.prctl(36, 1, 0, 0, 0):
        raise OSError("Cannot establish semantic child supervision")
    return library, previous.value, _children(os.getpid())


def _stop_adopted(supervision):
    if supervision is None:
        return True
    _, _, previous_children = supervision
    deadline = time.monotonic() + 2
    while True:
        children = _children(os.getpid()) - previous_children
        if not children:
            return True
        for child in children:
            _kill_descendants(child)
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(child, os.WNOHANG)
            except ChildProcessError:
                pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(.01)


def run_guarded(command, out, deadline, *, pass_fds=(), semantic_fallback_fd=None):
    """Parent wall clock. E1 requires an exclusive CLI, not a concurrent host.

    During E1 supervision every newly adopted child belongs to this invocation.
    Callers must not launch unrelated subprocesses concurrently.
    """
    supervision, child_env = None, None
    if sys.platform == "linux" and semantic_fallback_fd is not None:
        try:
            supervision = _subreaper()
        except (OSError, AttributeError):
            # Optional supervision unavailable: run the unchanged baseline.
            child_env = {**os.environ, "AGENT_E1_SEMANTIC": "0"}
            semantic_fallback_fd = None
    try:
        proc = subprocess.Popen(command, start_new_session=sys.platform == "linux", pass_fds=pass_fds, env=child_env)
    except BaseException:
        if supervision is not None:
            supervision[0].prctl(36, supervision[1], 0, 0, 0)
        raise
    try:
        result = proc.wait(timeout=max(.001, deadline - time.monotonic()))
        stopped = _stop_adopted(supervision)
        if not stopped:
            return 124
        if result != 0 and _semantic_fallback(out, semantic_fallback_fd):
            return 0
        return result
    except subprocess.TimeoutExpired:
        _kill_descendants(proc.pid)
        proc.kill()
        proc.wait(timeout=2)
        stopped = _stop_adopted(supervision)
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
        return 0 if stopped and _semantic_fallback(out, semantic_fallback_fd) else 124
    finally:
        if proc.poll() is None:
            _kill_descendants(proc.pid)
            proc.kill()
            proc.wait(timeout=2)
        if supervision is not None:
            _stop_adopted(supervision)
            supervision[0].prctl(36, supervision[1], 0, 0, 0)


def main(argv=None):
    started = time.monotonic()
    parser = argparse.ArgumentParser(description="QFBench coding agent")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("solve")
    command.add_argument("--task-dir", type=Path, required=True)
    command.add_argument("--out", type=Path, required=True)
    command.add_argument("--internal-worker", action="store_true", help=argparse.SUPPRESS)
    command.add_argument("--internal-output-fd", type=int, help=argparse.SUPPRESS)
    command.add_argument("--internal-semantic-fd", type=int, help=argparse.SUPPRESS)
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
            semantic_read = semantic_write = None
            if os.environ.get("AGENT_E1_SEMANTIC", "0") == "1":
                semantic_read, semantic_write = os.pipe()
                command.extend(["--internal-semantic-fd", str(semantic_write)])
            try:
                descriptors = (write_fd,) if semantic_write is None else (write_fd, semantic_write)
                return run_guarded(command, args.out, started + timeout, pass_fds=descriptors,
                                   semantic_fallback_fd=semantic_read)
            finally:
                if semantic_read is not None:
                    os.close(semantic_read)
                    os.close(semantic_write)
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
        if args.internal_semantic_fd is not None:
            from . import semantic
            semantic._fallback_fd = args.internal_semantic_fd
        from .solver import solve
        solve(args.task_dir, args.out)
    except Exception as exc:
        print(json.dumps({"event": "agent_failed", "error_type": type(exc).__name__,
                          "diagnostics": ".agent/run.json"}), file=sys.stderr, flush=True)
        return 1
    return 0
