import json
import math
import os
import re
import shutil
import threading
import time
from pathlib import Path
from .execution import execute
from .contracts import instruction_contract
from .model_client import ModelClient, ModelError
from .prompts import initial_messages
from .task_reader import read_task
from .workspace import deliverable_name, prepare_output, publish_outputs, validate_outputs, write_json, output_tree_bytes

JSON_FENCE = re.compile(r"\A```(?:json)?[ \t]*\r?\n(?P<body>.*?)(?:\r?\n)?```\Z", re.I | re.S)


def parse_solution(text, *, check_compile=True):
    text = text.strip()
    if text.startswith("```"):
        fence = JSON_FENCE.fullmatch(text)
        if fence is None:
            raise ValueError("Return one complete JSON object, optionally in a JSON code fence")
        text = fence.group("body").strip()
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
    if check_compile:
        compile(result["code"], "solution.py", "exec")
    return result


def solve(task_dir: Path, out: Path):
    started = time.monotonic()
    # Persist before task parsing/client setup, so those failures have evidence.
    # The CLI also emits a safe startup event if output setup itself fails.
    out = prepare_output(task_dir.resolve(), out)
    report = {"schema_version": 2, "status": "running", "stage": "startup", "attempts": [], "events": []}
    client, task, watchdog = None, None, None
    semantic_errors = ()
    lock = threading.RLock()

    def checkpoint(stage=None, **updates):
        with lock:
            if stage:
                report["stage"] = stage
                report["events"].append({"stage": stage, "elapsed_sec": round(time.monotonic() - started, 3)})
            report.update(updates)
            report["elapsed_sec"] = round(time.monotonic() - started, 3)
            if client is not None:
                report["model_usage"] = {"input_tokens_or_reservations": client.input_tokens,
                    "output_tokens_or_reservations": client.output_tokens, "requests": client.requests,
                    "sends": client.sends, "remaining_sends": client.remaining_sends,
                    "unconfirmed_review_send_reservations": getattr(client, "review_send_reservations", 0),
                    "sends_include_unconfirmed_reservations": bool(getattr(client, "review_send_reservations", 0)),
                    "unknown_usage_requests": client.unknown_usage_requests,
                    "usage_status": "unknown" if client.unknown_usage_requests else "reported"}
                report["transport"] = {"http_status": client.last_http_status,
                    "finish_reason": client.last_finish_reason, "response_bytes": client.last_response_bytes}
            write_json(out / ".agent" / "run.json", report)

    def expire():
        try:
            if report.get("semantic", {}).get("baseline_published"):
                from .cli import _kill_descendants
                _kill_descendants(os.getpid())
            # Last checkpoint is always durable even if this write is interrupted.
            checkpoint("deadline", status="timed_out", failure_category="watchdog_deadline")
        finally:
            os._exit(124)

    def repair_detail(exc):
        return redact(str(exc))[-12000:]

    def redact(value):
        detail = task.redact(value)
        for key in ("MODEL_TOKEN", "MODEL_ENDPOINT", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            if os.environ.get(key):
                detail = detail.replace(os.environ[key], "[REDACTED]")
        return detail

    checkpoint("startup")
    try:
        soft_timeout = float(os.environ.get("AGENT_SOFT_TIMEOUT_SEC", "360"))
        if not math.isfinite(soft_timeout) or soft_timeout <= 0:
            raise ValueError("AGENT_SOFT_TIMEOUT_SEC must be positive and finite")
        # Bound discovery/inspection as well as model calls and execution.
        watchdog = threading.Timer(max(0.01, started + soft_timeout - time.monotonic()), expire)
        watchdog.daemon = True
        watchdog.start()
        task = read_task(task_dir)
        effective_timeout = min(task.timeout, soft_timeout)
        deadline = started + effective_timeout
        watchdog.cancel()
        watchdog = threading.Timer(max(0.01, deadline - time.monotonic()), expire)
        watchdog.daemon = True
        watchdog.start()
        checkpoint("client", timeout_sec=task.timeout, soft_timeout_sec=soft_timeout,
                   effective_timeout_sec=effective_timeout, publication_reserve_sec=min(15, effective_timeout * .05))
        client = ModelClient()
        attempts = int(os.environ.get("AGENT_MAX_ATTEMPTS", "3"))
        if not 1 <= attempts <= 5:
            raise ValueError("AGENT_MAX_ATTEMPTS must be between 1 and 5")
        work_deadline = deadline - min(15, effective_timeout * .05)
        review_enabled = os.environ.get("AGENT_C3_REVIEW", "0") == "1"
        semantic_enabled = os.environ.get("AGENT_E1_SEMANTIC", "0") == "1"
        review_used = False
        report["c3"] = {"enabled": review_enabled, "maximum_reviews": 1}
        checkpoint("inspect")
        contract = instruction_contract(task.redact(task.instruction))
        write_json(out / ".agent" / "contract.json", contract)
        base_messages = initial_messages(task, contract=contract)
        messages = base_messages
        last_error = "No execution time remaining"
        for attempt in range(1, attempts + 1):
            if time.monotonic() >= work_deadline or client.remaining_sends == 0:
                checkpoint("deadline" if time.monotonic() >= work_deadline else "http",
                           failure_category="work_deadline" if time.monotonic() >= work_deadline else "request_budget")
                break
            attempt_dir = out / ".agent" / f"attempt-{attempt}"
            attempt_dir.mkdir()
            generated_out = attempt_dir / "output"
            generated_out.mkdir()
            content = ""
            solution = execution = None
            entry = {"attempt": attempt, "status": "failed"}
            report["attempts"].append(entry)
            try:
                checkpoint("http")
                content = task.redact(client.complete(messages, work_deadline, max_sends=min(2, client.remaining_sends)))
                checkpoint("parse")
                solution = parse_solution(content, check_compile=False)
                checkpoint("compile")
                compile(solution["code"], "solution.py", "exec")
                script = attempt_dir / "solution.py"
                script.write_text(solution["code"], encoding="utf-8")
                checkpoint("execute")
                execution_deadline = min(work_deadline, time.monotonic() + effective_timeout * 0.4)
                execution = execute(script, task.root, generated_out, execution_deadline)
                execution["log"] = task.redact(execution["log"])
                # Raw execution text stays in memory for repair; diagnostics use
                # allowlisted metadata so task text cannot leak credentials.
                entry["execution"] = {key: execution[key] for key in ("returncode", "timed_out", "elapsed_sec")}
                exceptions = re.findall(r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)):", execution["log"], re.M)
                if exceptions:
                    entry["execution"]["reported_exception_type"] = exceptions[-1][:100]
                write_json(attempt_dir / "execution.json", entry["execution"])
                if execution["timed_out"]:
                    raise ValueError("Generated program exceeded its execution time budget")
                if execution["returncode"] != 0:
                    raise ValueError(f"Generated program exited {execution['returncode']}:\n{execution['log']}")
                checkpoint("artifact")
                validate_outputs(generated_out, solution["deliverables"], contract)
                checkpoint("publish")
                publish_outputs(generated_out, solution["deliverables"], out, contract)
                shutil.rmtree(generated_out)
                output_tree_bytes(out)
                entry["status"] = "completed"
                if semantic_enabled:
                    # The default path is unchanged. Publish a valid baseline
                    # before starting optional work, which can only replace it
                    # with a fully executed, structurally valid candidate.
                    checkpoint("semantic", semantic={"enabled": True, "baseline_published": True},
                               status="completed_unverified", deliverables=solution["deliverables"])
                    from .semantic import SemanticRecoveryError, review_candidate
                    semantic_errors = (SemanticRecoveryError,)
                    try:
                        entry["semantic"] = review_candidate(client=client, task=task, out=out,
                            solution=solution, contract=contract, base_messages=base_messages,
                            work_deadline=work_deadline, redact=redact)
                    except semantic_errors:
                        raise
                    except Exception as exc:
                        entry["semantic"] = {"status": "kept", "reason": "optional_setup_failure",
                                             "error_type": type(exc).__name__, "accepted": False}
                checkpoint("complete", status="completed_unverified", failure_category=None, deliverables=solution["deliverables"])
                return out
            except semantic_errors:
                # A partial publication with failed rollback must not become
                # "completed" or enter another ordinary generation attempt.
                raise
            except Exception as exc:
                last_error = repair_detail(exc)
                entry.update(failure_stage=report["stage"], error_type=type(exc).__name__,
                             failure_category=exc.category if isinstance(exc, ModelError) else report["stage"],
                             terminal=isinstance(exc, ModelError) and exc.terminal)
                if (review_enabled and not review_used and solution is not None and not entry["terminal"]
                        and entry["failure_stage"] in ("compile", "execute", "artifact")):
                    from .review import attempt_repair
                    review_used = True
                    checkpoint("review")
                    repaired, review_record = attempt_repair(client=client, solution=solution, task=task,
                        directory=generated_out, attempt_dir=attempt_dir, whole_output=out,
                        contract=contract, stage=entry["failure_stage"], error=exc, execution=execution,
                        work_deadline=work_deadline, executor=execute, redact=redact)
                    entry["review"] = review_record
                    entry["terminal"] = entry["terminal"] or review_record["terminal"]
                    if repaired is not None:
                        checkpoint("publish")
                        publish_outputs(repaired["directory"], repaired["deliverables"], out, contract)
                        shutil.rmtree(repaired["directory"])
                        shutil.rmtree(generated_out)
                        output_tree_bytes(out)
                        entry["status"] = "completed_after_review"
                        checkpoint("complete", status="completed_unverified", failure_category=None,
                                   deliverables=repaired["deliverables"])
                        return out
                if generated_out.exists():
                    shutil.rmtree(generated_out)
                checkpoint(failure_category=entry["failure_category"])
                if entry["terminal"]:
                    break
                messages = base_messages + ([{"role": "assistant", "content": content}] if content else []) + [{
                    "role": "user", "content": "Repair this failure. Return the full JSON solution again.\n" + last_error}]
        raise RuntimeError("No valid deliverables produced; inspect .agent/run.json for failure stage")
    except Exception as exc:
        checkpoint(status="failed", error_type=type(exc).__name__,
                   failure_category=report.get("failure_category", exc.category if isinstance(exc, ModelError) else report["stage"]))
        raise
    finally:
        if report.get("semantic", {}).get("baseline_published"):
            from .cli import _kill_descendants
            _kill_descendants(os.getpid())
        if watchdog is not None:
            watchdog.cancel()
        checkpoint()
