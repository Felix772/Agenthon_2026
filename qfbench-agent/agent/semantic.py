"""Opt-in, runtime-generated checks. A checker is evidence, never an oracle."""
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
from pathlib import Path

from .execution import execute
from .model_client import ModelError
from .review import bounded_complete, bounded_validate
from .workspace import OUTPUT_LIMIT, REPORT_RESERVE, deliverable_name, output_tree_bytes, publish_outputs, write_json

MIN_WORK_SECONDS = 155
CALL_SECONDS = 30
CHECK_SECONDS = 15
REPAIR_SECONDS = 45
MAX_BASELINE_BYTES = 8 * 1024 * 1024
_fallback_fd = None


class SemanticRecoveryError(RuntimeError):
    """Original outputs could not be recovered; only the supervisor may retry."""


SYSTEM = '''Write an independent Python checker for the supplied instruction and inputs.
You do not receive the solver's code. Derive checks from the actual input data and explicit
requirements, not memorized answers or assumptions about a task family. Check units, row
alignment, group coverage and numerical identities only when the instruction supports them.
Return JSON only: {"checks":[{"id":"short_id","requirement":"exact instruction excerpt"}],
"code":"complete Python source"}. Declare 1-8 checks. Each requirement must be a nonempty
verbatim excerpt of the supplied instruction. Call check(id, boolean) exactly once for every
declared id; use explicit tolerances justified by that instruction or numerical precision.
Read inputs from TASK_DIR and candidate deliverables from CANDIDATE_DIR (environment variables).
Never modify the candidate. OUTPUT_DIR is disposable scratch. Do not print output. No network,
subprocesses, ctypes, grader/checks/reference files, card metadata, or external model calls.
Use installed libraries. Do not assert guessed financial conventions. If no sound bounded
check can be derived, return {"checks":[],"code":""}. Stay within 4,000 output tokens.'''


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def freeze_baseline(out, names):
    """Snapshot before optional work; a private inherited pipe authenticates recovery.

    Generated workers do not inherit this descriptor. A file called a marker is
    insufficient: the supervisor requires the exact manifest hash from the pipe.
    """
    size = sum((out / name).stat().st_size for name in names)
    if size > MAX_BASELINE_BYTES or output_tree_bytes(out) + size > OUTPUT_LIMIT - REPORT_RESERVE - MAX_BASELINE_BYTES:
        return None
    directory = out / ".agent" / "semantic-original"
    directory.mkdir()
    files = []
    for name in names:
        deliverable_name(name)
        source, target = out / name, directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        files.append({"path": name, "bytes": target.stat().st_size, "sha256": _digest(target),
                      "executable_bits": stat.S_IMODE(source.stat().st_mode) & 0o111})
    info = out.stat()
    manifest = {"identity": [info.st_dev, info.st_ino], "files": files}
    path = out / ".agent" / "semantic-original.json"
    write_json(path, manifest)
    proof = _digest(path)
    if _fallback_fd is not None:
        os.write(_fallback_fd, proof.encode("ascii"))
    return proof


def restore_baseline(out, proof):
    """Validate the authenticated snapshot entirely before touching public files."""
    manifest_path = out / ".agent" / "semantic-original.json"
    if not re.fullmatch(r"[a-f0-9]{64}", proof) or manifest_path.is_symlink() or _digest(manifest_path) != proof:
        raise ValueError("Unauthenticated semantic recovery manifest")
    manifest = json.loads(manifest_path.read_text())
    info = out.stat()
    if manifest["identity"] != [info.st_dev, info.st_ino]:
        raise ValueError("Semantic recovery output identity changed")
    original = out / ".agent" / "semantic-original"
    for item in manifest["files"]:
        name = str(deliverable_name(item["path"]))
        path = original / name
        # Also refuse linked parent components before traversing snapshot files.
        if any(parent.is_symlink() for parent in [path, *path.parents] if parent.is_relative_to(out)):
            raise ValueError("Linked semantic snapshot")
        meta = path.lstat()
        if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1 or meta.st_size != item["bytes"] or _digest(path) != item["sha256"]:
            raise ValueError("Semantic snapshot changed")
    for item in manifest["files"]:
        destination = out / item["path"]
        if any(parent.is_symlink() for parent in [destination, *destination.parents] if parent.is_relative_to(out)):
            raise ValueError("Linked semantic destination")
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix="restore-", dir=original)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            shutil.copyfile(original / item["path"], temporary)
            temporary.chmod(0o600 | item["executable_bits"])
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    for item in manifest["files"]:
        if _digest(out / item["path"]) != item["sha256"]:
            raise ValueError("Incomplete semantic recovery")
    cleanup_scratch(out)
    output_tree_bytes(out)
    return [item["path"] for item in manifest["files"]]


def cleanup_scratch(out):
    for name in ("check-scratch", "recheck-scratch", "candidate"):
        path = out / ".agent" / "semantic" / name
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            if not path.resolve().is_relative_to(out.resolve()):
                raise ValueError("Semantic scratch escaped output")
            shutil.rmtree(path)


def parse_checker(text, instruction):
    value = json.loads(text)
    if not isinstance(value, dict) or set(value) != {"checks", "code"}:
        raise ValueError("Invalid checker response")
    checks, code = value["checks"], value["code"]
    if not isinstance(checks, list) or len(checks) > 8 or not isinstance(code, str) or len(code) > 32000:
        raise ValueError("Unbounded checker")
    identifiers = []
    for item in checks:
        if not isinstance(item, dict) or set(item) != {"id", "requirement"}:
            raise ValueError("Invalid checker requirement")
        identifier, requirement = item["id"], item["requirement"]
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", identifier) or identifier in identifiers:
            raise ValueError("Invalid checker identifier")
        if not isinstance(requirement, str) or not 8 <= len(requirement) <= 1000 or requirement not in instruction:
            raise ValueError("Checker requirement lacks an exact instruction anchor")
        identifiers.append(identifier)
    if not checks:
        if code.strip():
            raise ValueError("Code without declared checks")
        return None
    if not code.strip():
        raise ValueError("Empty checker")
    compile(code, "semantic-checker.py", "exec")
    return {"code": code, "ids": identifiers}


def run_checker(checker, script, task, candidate, scratch, deadline, *, candidate_root=None):
    scratch.mkdir()
    try:
        result = execute(script, task.root, scratch, deadline,
                         candidate_dir=candidate, checker_ids=checker["ids"], candidate_root=candidate_root)
        if result["timed_out"] or result["returncode"] != 0:
            return None, {"status": "invalid", "reason": "checker_timeout" if result["timed_out"] else "checker_execution"}
        checks = result.get("checker_results")
        if not isinstance(checks, dict) or set(checks) != set(checker["ids"]) or any(type(v) is not bool for v in checks.values()):
            return None, {"status": "invalid", "reason": "checker_incomplete"}
        return checks, {"status": "checked", "checks": checks, "elapsed_sec": result["elapsed_sec"]}
    finally:
        shutil.rmtree(scratch)


def review_candidate(*, client, task, out, solution, contract, base_messages, work_deadline, redact):
    """Baseline is already published. Optional errors preserve those exact bytes."""
    record = {"status": "skipped", "reason": "budget", "accepted": False, "house_checker_calls": 0}
    if work_deadline - time.monotonic() < MIN_WORK_SECONDS or client.remaining_sends < 2:
        return record
    proof = freeze_baseline(out, solution["deliverables"])
    if proof is None:
        record["reason"] = "snapshot_space"
        return record
    started = time.monotonic()
    deadline = min(work_deadline - 5, started + MIN_WORK_SECONDS - 10)
    directory = out / ".agent" / "semantic"
    directory.mkdir()
    try:
        # The original runtime prompt carries full instruction and bounded input
        # inspection, but never the candidate's program or reference answers.
        messages = [{"role": "system", "content": SYSTEM}, base_messages[-1],
                    {"role": "user", "content": json.dumps({"deliverables": solution["deliverables"]})}]
        record.update(status="reviewing", reason=None, house_checker_calls=1)
        response = bounded_complete(client, messages, min(deadline, time.monotonic() + CALL_SECONDS))
        checker = parse_checker(redact(response), task.redact(task.instruction))
        if checker is None:
            record.update(status="kept", reason="no_supported_check")
            return record
        script = directory / "checker.py"
        script.write_text(checker["code"], encoding="utf-8")
        checks, observation = run_checker(checker, script, task, out,
            directory / "check-scratch", min(deadline, time.monotonic() + CHECK_SECONDS), candidate_root=out)
        record["baseline"] = observation
        if checks is None or all(checks.values()):
            record.update(status="kept", reason="checks_passed" if checks is not None else observation["reason"])
            return record
        if deadline - time.monotonic() < CALL_SECONDS + REPAIR_SECONDS + CHECK_SECONDS + 5 or client.remaining_sends < 1:
            record.update(status="kept", reason="repair_budget")
            return record
        # One bounded core-style repair; reuse the same independent checker.
        repair_messages = base_messages + [{"role": "assistant", "content": json.dumps(solution)},
            {"role": "user", "content": "An independent runtime checker flagged a possible defect. "
             "Recheck against the instruction; do not change conventions to satisfy an incorrect check. "
             "Return the complete solution JSON, preserving all declared deliverable names.\n" +
             json.dumps({"checks": checks, "checker_code": checker["code"]})}]
        response = bounded_complete(client, repair_messages, min(deadline, time.monotonic() + CALL_SECONDS))
        from .solver import parse_solution
        repaired = parse_solution(redact(response))
        if set(repaired["deliverables"]) != set(solution["deliverables"]):
            raise ValueError("Semantic repair changed deliverables")
        repair_script = directory / "repair.py"
        repair_script.write_text(repaired["code"], encoding="utf-8")
        candidate = directory / "candidate"
        candidate.mkdir()
        result = execute(repair_script, task.root, candidate, min(deadline, time.monotonic() + REPAIR_SECONDS))
        if result["timed_out"] or result["returncode"] != 0:
            raise ValueError("Semantic repair execution failed")
        bounded_validate(candidate, repaired["deliverables"], contract, out, min(deadline, time.monotonic() + 5))
        checks, observation = run_checker(checker, script, task, candidate,
            directory / "recheck-scratch", min(deadline, time.monotonic() + CHECK_SECONDS), candidate_root=out)
        record["repaired"] = observation
        if checks is None or not all(checks.values()):
            record.update(status="kept", reason="repair_not_verified")
            return record
        if deadline - time.monotonic() < 5:
            record.update(status="kept", reason="publication_budget")
            return record
        publish_outputs(candidate, repaired["deliverables"], out, contract)
        record.update(status="repaired", reason="runtime_checks_passed", accepted=True)
        return record
    except Exception as exc:
        try:
            restore_baseline(out, proof)
        except Exception as recovery_error:
            raise SemanticRecoveryError("Semantic baseline recovery failed") from recovery_error
        record.update(status="kept", reason=exc.category if isinstance(exc, ModelError) else "invalid_review",
                      error_type=type(exc).__name__)
        return record
    finally:
        record["elapsed_sec"] = round(time.monotonic() - started, 3)
        # Leave small code/metadata evidence; discard all scratch/candidate files.
        try:
            cleanup_scratch(out)
        except Exception as cleanup_error:
            # Do not mask a failed recovery with a catchable optional error.
            raise SemanticRecoveryError("Semantic cleanup failed") from cleanup_error
