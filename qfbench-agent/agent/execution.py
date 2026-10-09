"""Execute one generated program with bounded logs and a process deadline."""
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


def execute(script, task_dir, output_dir, deadline, *, candidate_dir=None, checker_ids=None, candidate_root=None):
    if sys.platform != "linux":
        raise RuntimeError("Generated code must run inside the Linux agent container")
    worker = Path(__file__).with_name("worker.py")
    # Do not expose endpoint/proxy configuration or unrelated host credentials to
    # generated code. It performs local computation only.
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "LD_LIBRARY_PATH") if k in os.environ}
    env.update(TASK_DIR=str(task_dir), OUTPUT_DIR=str(output_dir), HOME=str(output_dir),
               TMPDIR=str(output_dir), XDG_CACHE_HOME=str(output_dir),
               PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1",
               MPLCONFIGDIR=str(output_dir / "mpl-cache"),
               QFBENCH_SEED=os.environ.get("QFBENCH_SEED", "0"),
               OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    checker_read = checker_write = None
    command = [sys.executable, "-B", str(worker), str(script)]
    if candidate_dir is not None:
        import json
        env["CANDIDATE_DIR"] = str(candidate_dir)
        env["CANDIDATE_ROOT"] = str(candidate_root or candidate_dir)
        env["AGENT_CHECKER_IDS"] = json.dumps(checker_ids)
        checker_read, checker_write = os.pipe()
        command.extend(["--checker-result-fd", str(checker_write)])
    started = time.monotonic()
    try:
        proc = subprocess.Popen(command, cwd=output_dir,
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True, pass_fds=() if checker_write is None else (checker_write,))
    except BaseException:
        if checker_read is not None:
            os.close(checker_read)
            os.close(checker_write)
        raise
    if checker_write is not None:
        os.close(checker_write)
    tail = bytearray()

    def consume():
        while chunk := proc.stdout.read(4096):
            tail.extend(chunk)
            if len(tail) > 65536:
                del tail[:-65536]

    reader = threading.Thread(target=consume, daemon=True)
    reader.start()
    timed_out = False
    try:
        proc.wait(timeout=max(0.01, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        # Always kill the process group, including descendants that outlive main.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        reader.join(timeout=2)
        proc.stdout.close()
    result = {"returncode": proc.returncode, "timed_out": timed_out,
              "elapsed_sec": round(time.monotonic() - started, 3),
              "log": tail.decode("utf-8", errors="replace")}
    if checker_read is not None:
        try:
            os.set_blocking(checker_read, False)
            raw = os.read(checker_read, 4097)
            if len(raw) <= 4096:
                result["checker_results"] = json.loads(raw)
        except (OSError, ValueError):
            pass
        finally:
            os.close(checker_read)
    return result
