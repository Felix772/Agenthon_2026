import json
import os
import threading
import time
from pathlib import Path
from .execution import execute
from .model_client import ModelClient, ModelError
from .prompts import initial_messages, review_message
from .task_reader import read_task
from .workspace import deliverable_name, prepare_output, publish_outputs, validate_outputs, write_json


def parse_solution(text):
    text = text.strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3].strip()
    result = json.loads(text)
    if not isinstance(result, dict) or set(result) != {"code", "deliverables"}:
        raise ValueError("Return only a JSON object with code and deliverables")
    if not isinstance(result["code"], str) or not result["code"].strip():
        raise ValueError("No Python code returned")
    names = result["deliverables"]
    if not isinstance(names, list) or not names or len(names) > 100:
        raise ValueError("Declare 1–100 relative deliverable filenames")
    for name in names:
        deliverable_name(name)
    compile(result["code"], "solution.py", "exec")
    return result


# The Development ingestion stage is one 12-hour clock for the whole roster (about 8 minutes
# per unit on Track 1); units not reached score 0. The card's per-unit ceiling is usually far
# larger, so cap our own work well inside the roster average (Track 1 README, rules 3 and 5).
DEFAULT_UNIT_BUDGET_SEC = 420.0
MAX_REVIEW_PREVIEW = 1500


def preview_outputs(directory, names):
    previews = {}
    for name in names:
        path = directory / name
        suffix = path.suffix.lower()
        try:
            if suffix == ".parquet":
                import pyarrow.parquet as pq
                table = pq.read_table(path)
                text = json.dumps({"schema": [f"{f.name}: {f.type}" for f in table.schema],
                                   "num_rows": table.num_rows, "head": table.slice(0, 5).to_pylist()},
                                  ensure_ascii=False, default=str)
            else:
                with path.open("rb") as handle:
                    text = handle.read(MAX_REVIEW_PREVIEW).decode("utf-8", errors="replace")
        except Exception as exc:
            text = f"unreadable: {type(exc).__name__}"
        previews[name] = text[:MAX_REVIEW_PREVIEW]
    return previews


def unpublish(out, names):
    for name in names:
        try:
            (out / name).unlink()
        except FileNotFoundError:
            pass


def solve(task_dir: Path, out: Path):
    started = time.monotonic()
    task = read_task(task_dir)
    client = ModelClient()
    # 25 House requests per unit: spend them on repairs and a review, not on idling.
    attempts = int(os.environ.get("AGENT_MAX_ATTEMPTS", "6"))
    if not 1 <= attempts <= 12:
        raise ValueError("AGENT_MAX_ATTEMPTS must be between 1 and 12")
    reviews = int(os.environ.get("AGENT_REVIEW_ROUNDS", "1"))
    if not 0 <= reviews <= 3:
        raise ValueError("AGENT_REVIEW_ROUNDS must be between 0 and 3")
    budget = float(os.environ.get("AGENT_UNIT_BUDGET_SEC", str(DEFAULT_UNIT_BUDGET_SEC)))
    if not budget > 0:
        raise ValueError("AGENT_UNIT_BUDGET_SEC must be positive")
    out = prepare_output(task.root, out)
    deadline = started + min(task.timeout, budget)
    limit = deadline - started
    # A socket timeout alone cannot bound a server that dribbles bytes forever.
    # Enforce the card's overall wall-clock deadline independently of every I/O.
    watchdog = threading.Timer(max(0.01, deadline - time.monotonic()), lambda: os._exit(124))
    watchdog.daemon = True
    watchdog.start()
    work_deadline = deadline - min(10, limit * 0.05)
    base_messages = initial_messages(task)
    messages = base_messages
    report = {"status": "failed", "attempts": [], "reviews": [], "timeout_sec": task.timeout,
              "budget_sec": round(limit, 3), "model": client.model}
    last_error = "No execution time remaining"
    try:
        for attempt in range(1, attempts + 1):
            if time.monotonic() >= work_deadline:
                break
            attempt_dir = out / ".agent" / f"attempt-{attempt}"
            attempt_dir.mkdir()
            generated_out = attempt_dir / "output"
            generated_out.mkdir()
            content = ""
            entry = {"attempt": attempt, "status": "failed"}
            report["attempts"].append(entry)
            try:
                content = task.redact(client.complete(messages, work_deadline))
                solution = parse_solution(content)
                script = attempt_dir / "solution.py"
                script.write_text(solution["code"], encoding="utf-8")
                execution_deadline = min(work_deadline, time.monotonic() + limit * 0.4)
                execution = execute(script, task.root, generated_out, execution_deadline)
                execution["log"] = task.redact(execution["log"])
                write_json(attempt_dir / "execution.json", execution)
                if execution["timed_out"]:
                    raise ValueError("Generated program exceeded its execution time budget")
                if execution["returncode"] != 0:
                    raise ValueError(f"Generated program exited {execution['returncode']}:\n{execution['log']}")
                validate_outputs(generated_out, solution["deliverables"])
                publish_outputs(generated_out, solution["deliverables"], out)
                entry["status"] = "completed"
                report.update(status="completed_unverified", deliverables=solution["deliverables"])
                review(task, client, base_messages, content, solution, generated_out, execution,
                       out, report, reviews, started, limit, work_deadline)
                return out
            except (ValueError, SyntaxError, ModelError, OSError) as exc:
                last_error = task.redact(str(exc))[-12000:]
                entry["error"] = last_error
                messages = base_messages + ([{"role": "assistant", "content": content}] if content else []) + [{
                    "role": "user", "content": "Repair this failure. Return the full JSON solution again.\n" + last_error}]
        raise RuntimeError("No valid deliverables produced: " + last_error)
    finally:
        watchdog.cancel()
        report["elapsed_sec"] = round(time.monotonic() - started, 3)
        report["model_usage"] = {"input_tokens_or_reservations": client.input_tokens,
                                 "output_tokens_or_reservations": client.output_tokens, "requests": client.requests,
                                 "unknown_usage_requests": client.unknown_usage_requests,
                                 "usage_status": "unknown" if client.unknown_usage_requests else "reported"}
        write_json(out / ".agent" / "run.json", report)


def review(task, client, base_messages, content, solution, generated_out, execution, out, report,
           rounds, started, limit, work_deadline):
    """Ask the model to check a working program's outputs; adopt only a revision that runs.

    The published deliverables stay in place unless a revised program executes cleanly and
    validates, so a review can never leave the unit with less than it already had."""
    for number in range(1, rounds + 1):
        # Keep at least a third of the unit budget for the review's own execution.
        if work_deadline - time.monotonic() < max(20.0, limit / 3) or client.requests >= 24:
            return
        entry = {"round": number, "status": "failed"}
        report["reviews"].append(entry)
        attempt_dir = out / ".agent" / f"review-{number}"
        attempt_dir.mkdir()
        try:
            messages = base_messages + [
                {"role": "assistant", "content": content},
                {"role": "user", "content": task.redact(review_message(
                    preview_outputs(generated_out, solution["deliverables"]), execution["log"]))}]
            reply = task.redact(client.complete(messages, work_deadline))
            text = reply.strip()
            if text.startswith("```json\n") and text.endswith("```"):
                text = text[8:-3].strip()
            verdict = json.loads(text)
            if isinstance(verdict, dict) and verdict.get("verdict") == "ok":
                entry["status"] = "accepted_as_is"
                return
            revised = parse_solution(reply)
            revised_out = attempt_dir / "output"
            revised_out.mkdir()
            script = attempt_dir / "solution.py"
            script.write_text(revised["code"], encoding="utf-8")
            run = execute(script, task.root, revised_out,
                          min(work_deadline, time.monotonic() + limit * 0.4))
            run["log"] = task.redact(run["log"])
            write_json(attempt_dir / "execution.json", run)
            if run["timed_out"] or run["returncode"] != 0:
                entry["status"] = "revision_failed_kept_original"
                return
            validate_outputs(revised_out, revised["deliverables"])
            unpublish(out, solution["deliverables"])
            publish_outputs(revised_out, revised["deliverables"], out)
            entry["status"] = "revised"
            report["deliverables"] = revised["deliverables"]
            content, solution, generated_out, execution = reply, revised, revised_out, run
        except Exception as exc:  # a review must never cost the unit its working answer
            entry["error"] = task.redact(f"{type(exc).__name__}: {exc}")[-2000:]
            return
