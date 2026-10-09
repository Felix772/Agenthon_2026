"""Python guardrails for generated code, inside the Docker security boundary.

Audit hooks catch ordinary accidental violations; they are NOT a hostile-code
sandbox. Use read-only Docker mounts, restricted networking and resource caps.
"""
import importlib.util
import json
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
    # statsmodels (and arch through it) may import optional Polars. Its CPU
    # feature probe loads libc with ctypes, so initialize this installed
    # dependency before generated code loses access to native system calls.
    if importlib.util.find_spec("polars") is not None:
        import polars  # noqa: F401

    seed = int(os.environ.get("QFBENCH_SEED", "0"))
    random.seed(seed)
    np.random.seed(seed % (2**32))
    forbidden = {"reward.json", "pytest_report.json", "reward.txt", "reward", "verifier", "checks", ".agent"}
    sealed = {"checks", "reference", "reference_data", "solution", ".git"}
    candidate = Path(os.environ["CANDIDATE_DIR"]).resolve() if "CANDIDATE_DIR" in os.environ else None
    candidate_root = Path(os.environ["CANDIDATE_ROOT"]).resolve() if candidate is not None else None

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
        elif candidate_root is not None and path.is_relative_to(candidate_root) and not path.is_relative_to(output):
            if not path.is_relative_to(candidate) or any(part in forbidden for part in path.relative_to(candidate).parts):
                raise PermissionError("Checker cannot read internal candidate diagnostics")

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

    namespace = {"__name__": "__main__", "__file__": str(script)}
    checker_ids = json.loads(os.environ["AGENT_CHECKER_IDS"]) if "AGENT_CHECKER_IDS" in os.environ else None
    results = {}
    if checker_ids is not None:
        candidate = Path(os.environ["CANDIDATE_DIR"]).resolve()
        if candidate == output or candidate.is_relative_to(output):
            raise ValueError("Checker candidate must be outside writable scratch")

        def check(identifier, passed):
            if identifier not in checker_ids or identifier in results or not isinstance(passed, (bool, np.bool_)):
                raise ValueError("Checker must report each declared boolean check exactly once")
            results[identifier] = bool(passed)
        namespace["check"] = check
    sys.addaudithook(audit)
    exec(code, namespace)
    if checker_ids is not None:
        if set(results) != set(checker_ids):
            raise ValueError("Checker omitted a declared check")
        # Separate normal completion from generated stdout. Early exit plus a
        # printed result is invalid. This is not an unforgeable oracle or a
        # boundary against hostile Python inspecting its own file descriptors.
        result_fd = int(sys.argv[3]) if sys.argv[2:3] == ["--checker-result-fd"] else None
        if result_fd is None:
            raise ValueError("Checker completion channel is missing")
        os.write(result_fd, json.dumps(results, sort_keys=True).encode())
        os.close(result_fd)


if __name__ == "__main__":
    main()
