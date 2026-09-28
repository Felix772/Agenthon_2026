"""Python guardrails for generated code, inside the Docker security boundary.

Audit hooks catch ordinary accidental violations; they are NOT a hostile-code
sandbox. Use read-only Docker mounts, restricted networking and resource caps.
"""
import os
import random
import resource
import sys
from pathlib import Path


def main():
    script = Path(sys.argv[1]).resolve()
    code = compile(script.read_text(encoding="utf-8"), str(script), "exec")
    output = Path(os.environ["OUTPUT_DIR"]).resolve()
    task = Path(os.environ["TASK_DIR"]).resolve()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    limit = min([64 * 1024 * 1024] + [value for value in (soft, hard) if value != resource.RLIM_INFINITY])
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    # Libraries may probe CPU configuration or load native extensions on import.
    # Initialize the core stack before denying those operations to generated code.
    import numpy as np
    import pandas  # noqa: F401
    import scipy  # noqa: F401
    import pyarrow  # noqa: F401

    seed = int(os.environ.get("QFBENCH_SEED", "0"))
    random.seed(seed)
    np.random.seed(seed % (2**32))
    forbidden = {"reward.json", "pytest_report.json", "reward.txt", "reward", "verifier", "checks", ".agent"}
    sealed = {"checks", "reference", "reference_data", "solution", ".git"}

    def check_path(value, writing=False):
        if isinstance(value, int):
            return
        path = Path(os.fsdecode(value)).resolve()
        if writing:
            if not path.is_relative_to(output):
                raise PermissionError("Writes must stay within OUTPUT_DIR")
            if any(p.lower() in forbidden or p.lower().startswith("reward.") for p in path.relative_to(output).parts):
                raise PermissionError("Reward/verifier artifacts are forbidden")
        elif path.is_relative_to(task):
            parts = path.relative_to(task).parts
            if any(p in sealed for p in parts) or path.name in {"card.toml", "manifest.json"}:
                raise PermissionError("Generated code cannot read grader or card metadata")

    def audit(event, args):
        if event == "open":
            _, mode, flags = args
            writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            check_path(args[0], writing)
        elif event in {"os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.truncate", "os.utime"}:
            check_path(args[0], True)
        elif event in {"os.rename", "os.replace"}:
            check_path(args[0], True)
            check_path(args[1], True)
        elif event in {"os.link", "os.symlink"}:
            raise PermissionError("Output links are forbidden")
        elif event.startswith(("socket.", "subprocess.", "ctypes.")) or event in {"os.system", "os.exec", "os.posix_spawn", "os.fork", "os.forkpty"}:
            raise PermissionError("Generated code may not launch processes or use network/native system calls")

    sys.addaudithook(audit)
    exec(code, {"__name__": "__main__", "__file__": str(script)})


if __name__ == "__main__":
    main()
