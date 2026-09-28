import json
import os
import threading
import time
from pathlib import Path
from .execution import execute
from .model_client import ModelClient, ModelError
from .prompts import initial_messages
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


def solve(task_dir: Path, out: Path):
    started = time.monotonic()
    task = read_task(task_dir)
    client = ModelClient()
    attempts = int(os.environ.get("AGENT_MAX_ATTEMPTS", "3"))
    if not 1 <= attempts <= 5:
        raise ValueError("AGENT_MAX_ATTEMPTS must be between 1 and 5")
    out = prepare_output(task.root, out)
    deadline = started + task.timeout
    # A socket timeout alone cannot bound a server that dribbles bytes forever.
    # Enforce the card's overall wall-clock deadline independently of every I/O.
    watchdog = threading.Timer(max(0.01, deadline - time.monotonic()), lambda: os._exit(124))
    watchdog.daemon = True
    watchdog.start()
    work_deadline = deadline - min(10, task.timeout * 0.05)
    base_messages = initial_messages(task)
    messages = base_messages
    report = {"status": "failed", "attempts": [], "timeout_sec": task.timeout, "model": client.model}
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
                execution_deadline = min(work_deadline, time.monotonic() + task.timeout * 0.4)
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
