"""T3-SCALAR-DIRECT-v5: fresh paired local timing on the Ubuntu Docker proxy.

Local nonrankable experiment. Whole-roster blocks are immutable; interrupted or
polluted blocks are retained and must restart from their first pair. No build,
publication, packaging, upload, participant change, or reuse of earlier timing samples.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import stat
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "project-evidence/t13-experiments/T3-SCALAR-DIRECT-v5"
PLAN = OUT / "plan-v5.json"
REGISTRATION = OUT / "registration.json"
CURRENT = ROOT / ".validation/t3-direct-current-20261001"
PREVIOUS = ROOT / "project-evidence/t13-experiments/T3-SCALAR-DIRECT-v4"
DOCKER_SOCKET = Path("/mnt/wsl/docker-desktop/shared-sockets/guest-services/Ubuntu.docker.container-proxy.sock")
DOCKER_HOST = f"unix://{DOCKER_SOCKET}"
RUN_NAMESPACE_BASE = 5000
IMAGES = {
    "direct-control": "sha256:df5e1b9d96abf06947a33cf499c3b4feed271fa31f6439de8ce49ff98a834489",
    "direct-scalar": "sha256:a12731a118178e10469608ec92172bc7db4b799a2b4ad8cd2b1acf939dd053c9",
}
REFS = {
    "Agenthon2026-public": "84221f1b553475b1283cb653145051e916377dd0",
    "track1-coding-public": "177058dff9630bc53456b21e35bc5a28a0ad02b6",
    "track2-forecasting-public": "3654fe3ced199df08862231a893c958e2a05ebef",
    "track3-simulation-public": "e9e42cc25fb840462f4cd97b23107b87619ddb85",
    "track4-analysis-public": "ede7381d8c1ba9d8c84068f9d142f5e093a33892",
}
INPUT_SHA = "daed56d432146c2c985872848ec4809bde1e0e062db489bbe3250c9c97508bb0"
CARD_SHA = "a3184d90b0e0bda0a8bbd0c669e542c40e9296b4bda3db530af9a3f8eb634943"
SERVICE_ID = "ceff2235add6962095b5cd1f357c075b1e1ffe33dad2a67973ce095f9f5bdb8f"
SEED = 2026100107


class BlockPollution(RuntimeError):
    """Observed external activity: whole-block retry is permitted, with evidence."""


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def sha(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def canonical(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def command(*args):
    return subprocess.check_output(list(map(str, args)), text=True).strip()


def docker_endpoint():
    """Require the Ubuntu proxy that translates this distro's /mnt/c bind paths."""
    if os.environ.get("DOCKER_HOST") != DOCKER_HOST:
        raise RuntimeError(f"DOCKER_HOST must be {DOCKER_HOST}")
    if os.environ.get("DOCKER_CONTEXT"):
        raise RuntimeError("DOCKER_CONTEXT overrides DOCKER_HOST; unset it")
    if not DOCKER_SOCKET.is_socket():
        raise RuntimeError(f"Ubuntu Docker proxy socket is unavailable: {DOCKER_SOCKET}")
    socket_info = DOCKER_SOCKET.stat()
    version = command("docker", "version", "--format", "{{.Server.Version}}")
    if version != "29.4.2":
        raise RuntimeError(f"Expected registered Docker Desktop 29.4.2, got {version}")
    return {"docker_host": DOCKER_HOST, "server_version": version,
            "daemon_id": command("docker", "info", "--format", "{{.ID}}"),
            "socket_device": socket_info.st_dev, "socket_inode": socket_info.st_ino,
            "socket_uid": socket_info.st_uid, "socket_gid": socket_info.st_gid,
            "socket_mode": oct(stat.S_IMODE(socket_info.st_mode))}


def bind_preflight(label):
    """Exercise input and output mounts through the per-distro proxy before timing."""
    source = OUT / "inputs/t3-as01-base-mix"
    scenario = source / "scenario.json"
    if not scenario.is_file() or sha(scenario) != "b81f521035e2d2937d94a06d03a4e03b8721d77d317549f00c0515e9b1aac1fb":
        raise RuntimeError("Bind preflight source does not match frozen public input")
    directory = OUT / "preflight" / f"{label}-{time.time_ns()}"
    output = directory / "output"
    output.mkdir(parents=True, exist_ok=False)
    output.chmod(0o777)
    code = ("from pathlib import Path; "
            "assert Path('/input/scenario.json').is_file(); "
            "p=Path('/output/bind-probe.txt'); p.write_text('t3-v5-bind-ok\\n'); "
            "assert p.read_text() == 't3-v5-bind-ok\\n'; print('bind-ok')")
    args = ["docker", "run", "--rm", "--name", f"t3-v5-bind-probe-{time.time_ns()}",
            "--pull", "never", "--network", "none", "--read-only", "--user", "65534:65534",
            "--cap-drop=ALL", "--security-opt", "no-new-privileges",
            "--mount", f"type=bind,src={source},dst=/input,readonly",
            "--mount", f"type=bind,src={output},dst=/output",
            "--entrypoint", "python", IMAGES["direct-control"], "-c", code]
    started_at = now()
    completed = subprocess.run(args, capture_output=True, text=True, timeout=60)
    marker = output / "bind-probe.txt"
    passed = completed.returncode == 0 and completed.stdout.strip() == "bind-ok" and marker.is_file() \
        and marker.read_text() == "t3-v5-bind-ok\n"
    result = {"label": label, "started_at": started_at, "finished_at": now(),
              "docker_endpoint": docker_endpoint(), "command": args,
              "exit_code": completed.returncode, "stdout": completed.stdout[-1000:],
              "stderr": completed.stderr[-1000:], "passed": passed,
              "marker_sha256": sha(marker) if marker.is_file() else None,
              "record": str((directory / "probe.json").relative_to(OUT))}
    save(directory / "probe.json", result)
    if not passed:
        raise RuntimeError(f"Ubuntu Docker proxy bind preflight failed; see {directory / 'probe.json'}")
    return result


def service_snapshot(service):
    return {"Id": service["Id"], "Name": service["Name"], "State": service["State"],
            "RestartCount": service["RestartCount"],
            "RestartPolicy": service["HostConfig"]["RestartPolicy"]}


def require_service_running(service):
    snapshot = service_snapshot(service)
    policy = snapshot["RestartPolicy"]
    if snapshot["Id"] != SERVICE_ID or snapshot["Name"] != "/claude-app-1":
        raise RuntimeError("Exact claude-app-1 service identity changed")
    if policy.get("Name") != "on-failure" or policy.get("MaximumRetryCount") != 0:
        raise RuntimeError("Exact claude-app-1 restart policy changed")
    if not (snapshot["State"]["Running"] or snapshot["State"]["Restarting"]):
        raise RuntimeError("claude-app-1 must be running or restarting before/after a T3 block")
    return snapshot


def run_attempt_token(block_dir):
    return RUN_NAMESPACE_BASE + int(block_dir.name.removeprefix("attempt-"))


def support():
    sys.path.insert(0, str(ROOT / "simulation-agent"))
    import run_parallel_batch_experiment as screen
    import run_t3_full_roster_parallel4 as full
    from throughput import node_fingerprint
    from qfbench2_track_simulation.scoring import cluster_key
    return screen, full, node_fingerprint, cluster_key


def schedule(names):
    rng = random.Random(SEED)
    first = {name: rng.choice(list(IMAGES)) for name in names}
    result = []
    for block in range(3):
        ordered = names[:]
        rng.shuffle(ordered)
        rows = []
        for name in ordered:
            a = rng.choice(list(IMAGES)) if block == 0 else first[name]
            if block == 2:
                a = next(value for value in IMAGES if value != a)
            order = [a] + [value for value in IMAGES if value != a]
            rows.extend({"unit": name, "variant": variant, "repeat": block} for variant in order)
        result.append({"block": block, "phase": "warmup" if block == 0 else "measured", "schedule": rows})
    return result


def bindings():
    sources = [Path(__file__), ROOT / "simulation-agent/run_parallel_batch_experiment.py",
               ROOT / "simulation-agent/run_t3_full_roster_parallel4.py", CURRENT / "binding.json"]
    return {str(path.relative_to(ROOT)): sha(path) for path in sources}


def registered_plan():
    registration = json.loads(REGISTRATION.read_text())
    plan_bytes = PLAN.read_bytes()
    digest = hashlib.sha256(plan_bytes).hexdigest()
    if registration["plan_sha256"] != digest:
        raise RuntimeError("Plan bytes differ from immutable v5 registration")
    if registration["script_sha256"] != sha(Path(__file__)):
        raise RuntimeError("Harness bytes differ from immutable v5 registration")
    plan = json.loads(plan_bytes)
    if plan["experiment"] != "T3-SCALAR-DIRECT-v5" or registration["experiment"] != plan["experiment"]:
        raise RuntimeError("Registered plan has wrong experiment identity")
    if plan["script_bindings"] != bindings():
        raise RuntimeError("Harness or execution support changed after v5 registration")
    verify_current_source()
    return plan, digest


def verify_current_source():
    official = json.loads((CURRENT / "binding.json").read_text())
    for name, digest in official["source_files"].items():
        if sha(CURRENT / name) != digest:
            raise RuntimeError(f"Frozen current official source changed: {name}")


def register():
    if PLAN.exists() or REGISTRATION.exists():
        raise RuntimeError("Plan or registration already exists; do not overwrite")
    endpoint = docker_endpoint()
    screen, full, fingerprint, cluster_key = support()
    screen.verify_current_verifier_source()  # Old, explicitly bound execution support only.
    verify_current_source()
    units = full.roster()
    names = [unit.name for unit in units]
    inputs = full.stage_inputs(units, OUT)
    cards = {unit.name: sha(unit / "card.toml") for unit in units}
    if canonical({"units": names, "input_hashes": inputs}) != INPUT_SHA or canonical(cards) != CARD_SHA:
        raise RuntimeError("Frozen 71-unit roster changed")
    controls_path = ROOT / "project-evidence/t3-parallel-batch4-full-roster-20260928/records.json"
    controls = {row["unit"]: row for row in json.loads(controls_path.read_text())
                if row["variant"] == "selected-default4" and row["repeat"] == 0 and row["status"] == "passed"}
    scalar_path = ROOT / "project-evidence/t3-scalar-latency-semantic-20260928/records.json"
    scalars = {row["unit"]: row for row in json.loads(scalar_path.read_text()) if row["status"] == "passed"}
    if set(controls) != set(names) or set(scalars) != set(names):
        raise RuntimeError("Prior semantic controls do not cover the exact 71 units")
    for name in names:
        if controls[name]["stable"] != scalars[name]["stable"] or controls[name]["event_count"] != scalars[name]["event_count"]:
            raise RuntimeError("Prior direct control/candidate semantic bindings differ")
    for identity in IMAGES.values():
        if screen.inspect_image(identity) != identity:
            raise RuntimeError("Frozen Docker image is missing")
    service = require_service_running(json.loads(command("docker", "inspect", SERVICE_ID))[0])
    probe = bind_preflight("registration")
    plan = {"created_at": now(), "experiment": "T3-SCALAR-DIRECT-v5", "images": IMAGES, "refs": REFS,
        "rankable": False, "script_bindings": bindings(), "current_verifier_binding_sha256": sha(CURRENT / "binding.json"),
        "legacy_execution_support_verifier_sha256": screen.VERIFIER_ARCHIVE_SHA256,
        "docker_endpoint": endpoint, "bind_preflight": probe, "service_before_registration": service,
        "service_contract": {"id": SERVICE_ID, "name": "/claude-app-1", "restart_policy": "on-failure",
                             "maximum_retry_count": 0, "required_before_after_each_block": "running_or_restarting"},
        "run_attempt_namespace_base": RUN_NAMESPACE_BASE,
        "units": names, "input_hashes": inputs, "roster_input_sha256": INPUT_SHA, "card_sha256": CARD_SHA,
        "families": {unit.name: cluster_key(unit) for unit in units}, "is_batch": {unit.name: full.is_batch(unit) for unit in units},
        "stable_controls": {name: {key: row[key] for key in ("stable", "event_count")} for name, row in controls.items()},
        "semantic_control_records_sha256": sha(controls_path), "scalar_semantic_records_sha256": sha(scalar_path),
        "seed": SEED, "bootstrap_seeds": {"family_primary": SEED + 1, "unit_sensitivity": SEED + 2},
        "blocks": schedule(names), "fingerprint": fingerprint.collect().to_dict(),
        "resource": "4 CPU / 16GiB cgroup / no swap / UID65534 / readonly / no network / 300s local hard cap; GPU unused",
        "primary_rate": "host-counted events / Docker State.StartedAt-to-FinishedAt seconds",
        "primary_interval": f"20,000 scenario_family-cluster paired bootstraps of per-unit median absolute rate differences; seed {SEED + 1}; 2.5/97.5 percentiles",
        "sensitivity_interval": f"20,000 paired unit bootstraps; seed {SEED + 2}; cannot substitute for primary interval",
        "overall_median_improvement": "median across all 71 units of scalar per-unit median rate / control per-unit median rate - 1",
        "mean_rate_ratio": "arithmetic mean of 71 scalar per-unit median rates / analogous control mean - 1",
        "promotion": {"all_semantics": 71, "participant_failures": 0, "primary_ci_lower_gt": 0,
                      "median_relative_gain_ge": .03, "single_mean_ratio_ge": -.02, "batch_mean_ratio_ge": -.02},
        "block_policy": "Fresh warmup plus two measured whole-roster blocks. Retain and retry all 142 only for observed BlockPollution (co-resident container, event-watcher loss, or independently reported heavy load before rate inspection), or an externally audited host/process interruption with an unfinished running block. Other setup failures and participant failures stop. No exclusion of valid adverse blocks.",
        "clean_window": "Only coordinated root review/browser and this experiment; all other T1/T2/T4 CPU/Docker work stopped; user authorized temporary stop and restart of exact claude-app-1 ID.",
        "monitor_limits": "Docker co-residents checked before/after every run; events retained; wall-clock gaps and host load retained. Unobserved Windows scheduling interference remains a local-host limitation.",
        "host_restart_recovery": "After any process or host interruption, externally inspect exact service ID/policy and restore it if stopped; retain the interrupted block and classify from evidence before any new run."}
    previous = PREVIOUS / "plan-v4.json"
    failed = PREVIOUS / "blocks/0/attempt-01/status.json"
    audit = PREVIOUS / "blocks/0/attempt-01/interruption-audit.json"
    original_status = PREVIOUS / "blocks/0/attempt-01/status.before-interruption-recovery.json"
    plan["supersedes"] = {"plan_path": str(previous.relative_to(ROOT)), "plan_sha256": sha(previous),
        "failed_status_path": str(failed.relative_to(ROOT)), "failed_status_sha256": sha(failed),
        "interruption_audit_path": str(audit.relative_to(ROOT)), "interruption_audit_sha256": sha(audit),
        "original_status_path": str(original_status.relative_to(ROOT)),
        "original_status_sha256": sha(original_status),
        "reason": "v4 warmup stopped after 12 of 142 passed records when Windows replaced the WSL service. The unfinished block was invalidated without rate analysis. The WSL kernel and Ubuntu Docker proxy inode changed, so the v4 registration rejects a retry. Fresh v5 registration uses a new host fingerprint, schedule seed, container namespace, warmup, and both measured blocks.",
        "reuse_v1_or_v3_timing_records": False, "reuse_v4_timing_records": False,
        "reuse_v4_warmup": False}
    plan["failure_policy"] = "Participant output refusal stops; setup and unknown/unclassified failures stop_pending_classification and cannot rerun. Only observed BlockPollution or an externally audited host/process interruption with an unfinished running block, written evidence, and exact service restoration permits whole-block invalidation and retry."
    save(PLAN, plan)
    plan_sha256 = sha(PLAN)
    save(REGISTRATION, {"experiment": plan["experiment"], "registered_at": now(),
                        "plan_sha256": plan_sha256, "script_sha256": sha(Path(__file__)),
                        "expected_blocks": 3, "expected_runs_per_block": 142})
    print(json.dumps({"registered": True, "runs": 426, "plan_sha256": plan_sha256}))


def current_check(block_dir):
    _, plan_sha256 = registered_plan()
    if json.loads((block_dir / "status.json").read_text())["plan_sha256"] != plan_sha256:
        raise RuntimeError("Verifier block does not match registered v5 plan bytes")
    sys.path[:0] = [str(CURRENT / "toolkit"), str(CURRENT / "track3")]
    from dataclasses import asdict
    from importlib.metadata import version
    from qfbench2_track_simulation.scoring import build_developer_verifier
    records = json.loads((block_dir / "records.json").read_text())
    checked = []
    for row in records:
        unit = ROOT / ".validation/t3-release-source-20260925/units" / row["unit"]
        if row["attempt"] != run_attempt_token(block_dir):
            raise RuntimeError("Verifier record has wrong v5 Docker name namespace")
        accepted = block_dir / "attempts" / row["unit"] / row["variant"] / str(row["repeat"]) / str(row["attempt"]) / "accepted" / row["unit"]
        ctx = {"unit_dir": unit, "output_dir": accepted}
        verdict = build_developer_verifier(ctx).run(ctx)
        checked.append({"unit": row["unit"], "variant": row["variant"], "verdict": asdict(verdict)})
    result = {"toolkit_version": version("qfbench2-common"), "count": len(checked),
              "all_passed": all(row["verdict"]["admissible"] for row in checked), "records": checked}
    save(block_dir / "current-verifier.json", result)
    if result["toolkit_version"] != "2.5.1" or not result["all_passed"]:
        raise RuntimeError("Current official verifier rejected block")


def boot_ci(values, families, seed):
    rng = random.Random(seed)
    groups = defaultdict(list)
    for key, value in zip(families, values, strict=True):
        groups[key].append(value)
    blocks = list(groups.values())
    draws = sorted(statistics.mean(value for group in rng.choices(blocks, k=len(blocks)) for value in group)
                   for _ in range(20000))
    return [draws[499], draws[19499]]


def summarize(plan):
    _, plan_sha256 = registered_plan()
    accepted = []
    all_records = []
    for block in plan["blocks"]:
        options = sorted((OUT / "blocks" / str(block["block"])).glob("*/status.json"))
        valid = [path for path in options if json.loads(path.read_text())["status"] == "complete_clean"]
        if len(valid) != 1:
            return {"complete": False, "block": block["block"], "clean_attempts": len(valid)}
        directory = valid[0].parent
        if json.loads(valid[0].read_text()).get("plan_sha256") != plan_sha256:
            raise RuntimeError("Accepted block does not match registered v5 plan bytes")
        samples = json.loads((directory / "records.json").read_text())
        actual_schedule = [{key: row[key] for key in ("unit", "variant", "repeat")} for row in samples]
        verifier = json.loads((directory / "current-verifier.json").read_text())
        if actual_schedule != block["schedule"] or any(row["status"] != "passed" for row in samples):
            raise RuntimeError("Completed block records differ from the entire frozen schedule")
        if any(row.get("attempt") != run_attempt_token(directory) for row in samples):
            raise RuntimeError("Completed block includes another experiment's Docker name namespace")
        if verifier["count"] != 142 or not verifier["all_passed"] or len(verifier["records"]) != 142:
            raise RuntimeError("Current verifier did not admit all 142 block runs")
        if [(row["unit"], row["variant"]) for row in verifier["records"]] != [(row["unit"], row["variant"]) for row in samples]:
            raise RuntimeError("Current verifier roster differs from run records")
        for row in samples:
            control = plan["stable_controls"][row["unit"]]
            if row["stable"] != control["stable"] or row["event_count"] != control["event_count"]:
                raise RuntimeError("Completed block semantic binding changed")
        all_records.extend(samples)
        accepted.append(directory)
    records = [row for path in accepted[1:] for row in json.loads((path / "records.json").read_text())]
    rows = {}
    for name in plan["units"]:
        rates = {variant: statistics.median(row["rate"] for row in records if row["unit"] == name and row["variant"] == variant) for variant in IMAGES}
        rows[name] = {"control_rate": rates["direct-control"], "scalar_rate": rates["direct-scalar"],
                      "relative_gain": rates["direct-scalar"] / rates["direct-control"] - 1,
                      "difference": rates["direct-scalar"] - rates["direct-control"]}
    groups = {"all_71": plan["units"], "single_65": [name for name in rows if not plan["is_batch"][name]],
              "batch_6": [name for name in rows if plan["is_batch"][name]]}
    result = {"complete": True, "rankable": False, "passed_runs": len(all_records), "units": rows, "accepted_blocks": [str(p.relative_to(OUT)) for p in accepted]}
    for label, names in groups.items():
        differences = [rows[name]["difference"] for name in names]
        mean_control = statistics.mean(rows[name]["control_rate"] for name in names)
        mean_scalar = statistics.mean(rows[name]["scalar_rate"] for name in names)
        result[label] = {"n": len(names), "control_mean_rate": mean_control, "scalar_mean_rate": mean_scalar,
                         "mean_rate_ratio": mean_scalar / mean_control - 1,
                         "median_relative_gain": statistics.median(rows[name]["relative_gain"] for name in names),
                         "primary_family_bootstrap95_difference": boot_ci(differences, [plan["families"][name] for name in names], SEED + 1),
                         "unit_bootstrap95_difference_sensitivity": boot_ci(differences, names, SEED + 2)}
    result["promotion_gate_passed"] = (result["all_71"]["primary_family_bootstrap95_difference"][0] > 0
        and result["all_71"]["median_relative_gain"] >= .03 and result["single_65"]["mean_rate_ratio"] >= -.02
        and result["batch_6"]["mean_rate_ratio"] >= -.02)
    return result


def run(block_number):
    plan, plan_sha256 = registered_plan()
    if plan["script_bindings"] != bindings():
        raise RuntimeError("Harness changed after registration")
    endpoint = docker_endpoint()
    if endpoint != plan["docker_endpoint"]:
        raise RuntimeError("Ubuntu Docker endpoint or socket metadata changed since registration")
    screen, full, fingerprint, _ = support()
    screen.verify_current_verifier_source()
    verify_current_source()
    for identity in IMAGES.values():
        if screen.inspect_image(identity) != identity:
            raise RuntimeError("Frozen Docker identity changed")
    node = fingerprint.collect().to_dict()
    for key in ("cpu_model", "cpu_count", "memory_bytes", "kernel", "docker_version", "gpu_name", "gpu_count"):
        if node[key] != plan["fingerprint"][key]:
            raise RuntimeError(f"Node changed since registration: {key}")
    block = plan["blocks"][block_number]
    for earlier in range(block_number):
        status_files = list((OUT / "blocks" / str(earlier)).glob("*/status.json"))
        if not any(json.loads(path.read_text())["status"] == "complete_clean" for path in status_files):
            raise RuntimeError("Earlier entire block must pass first")
    parent = OUT / "blocks" / str(block_number)
    prior = list(parent.glob("*/status.json"))
    if any(json.loads(path.read_text())["status"] != "invalidated_block" for path in prior):
        raise RuntimeError("Only an explicitly invalidated infrastructure/load block may restart; other failures require classification")
    require_service_running(json.loads(command("docker", "inspect", SERVICE_ID))[0])
    probe = bind_preflight(f"block-{block_number}-attempt-{len(prior) + 1:02}")
    block_dir = parent / f"attempt-{len(prior) + 1:02}"
    block_dir.mkdir(parents=True, exist_ok=False)
    attempt_token = run_attempt_token(block_dir)
    expected_names = {
        f"t3-{'pb' if plan['is_batch'][item['unit']] else 'p4'}-{item['unit']}-{item['variant']}-{item['repeat']}-{attempt_token}"
        for item in block["schedule"]
    }
    if len(expected_names) != 142:
        raise RuntimeError("Frozen schedule does not define 142 unique Docker container names")
    units = full.roster()
    staged = full.stage_inputs(units, block_dir)
    if staged != plan["input_hashes"] or canonical({unit.name: sha(unit / "card.toml") for unit in units}) != plan["card_sha256"]:
        raise RuntimeError("Frozen solver inputs or card map changed")
    status = {"status": "running", "started_at": now(), "block": block_number,
              "plan_sha256": plan_sha256, "fingerprint": node}
    save(block_dir / "status.json", status)
    probe["plan_sha256"] = plan_sha256
    save(block_dir / "preflight.json", probe)
    service = json.loads(command("docker", "inspect", SERVICE_ID))[0]
    save(block_dir / "service-before.json", require_service_running(service))
    event_handle = (block_dir / "docker-events.jsonl").open("w")
    watcher = None
    records = []
    try:
        command("docker", "stop", "--time", "10", SERVICE_ID)
        active = command("docker", "ps", "--format", "{{.ID}} {{.Names}}")
        if active:
            save(block_dir / "pollution.json", {"at": now(), "active_containers": active})
            raise BlockPollution("Other running Docker containers pollute timing")
        watcher = subprocess.Popen(["docker", "events", "--format", "{{json .}}"], stdout=event_handle, stderr=subprocess.STDOUT)
        for index, item in enumerate(block["schedule"]):
            active = command("docker", "ps", "--format", "{{.ID}} {{.Names}}")
            if active or watcher.poll() is not None:
                save(block_dir / "pollution.json", {"at": now(), "before_run": index + 1,
                                                   "active_containers": active, "watcher_returncode": watcher.poll()})
                raise BlockPollution("Other container activity or Docker-event interruption")
            unit = ROOT / ".validation/t3-release-source-20260925/units" / item["unit"]
            runner = screen.run_one if full.is_batch(unit) else full.run_single
            row = runner(unit, item["variant"], block_number, IMAGES[item["variant"]],
                         block_dir, attempt_token, node)
            row["sampled_loadavg"] = Path("/proc/loadavg").read_text().strip()
            control = plan["stable_controls"][item["unit"]]
            if row["status"] == "passed" and (row["stable"] != control["stable"] or row["event_count"] != control["event_count"]):
                row.update(status="failed", failure_class="participant_semantic_difference")
            records.append(row)
            save(block_dir / "records.json", records)
            print(json.dumps({"block": block_number, "run": index + 1, "total": 142, "unit": row["unit"], "variant": row["variant"], "status": row["status"], "seconds": row.get("container_seconds")}), flush=True)
            if row["status"] != "passed":
                classification = row.get("failure_class", "")
                raw = block_dir / "attempts" / item["unit"] / item["variant"] / str(block_number) / str(row["attempt"]) / "raw"
                required = list(control["stable"]) + ["batch_events.json" if full.is_batch(unit) else "events.json"]
                missing_or_unsafe = row.get("exit_code") == 0 and any(
                    not (raw / name).is_file() or (raw / name).is_symlink() for name in required)
                if classification.startswith("participant") or row.get("error_type") == "TreeRefused" or missing_or_unsafe:
                    status["status"] = "participant_failed"
                else:
                    status["status"] = "stop_pending_classification"
                raise RuntimeError(f"Run failed: {row.get('failure_class')}")
            active = command("docker", "ps", "--format", "{{.ID}} {{.Names}}")
            if active:
                save(block_dir / "pollution.json", {"at": now(), "after_run": index + 1, "active_containers": active})
                raise BlockPollution("Co-resident Docker container appeared")
        current = subprocess.run([sys.executable, str(Path(__file__)), "--current-check", str(block_dir)],
                                 env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        if current.returncode:
            verdict_path = block_dir / "current-verifier.json"
            if verdict_path.is_file() and not json.loads(verdict_path.read_text())["all_passed"]:
                status["status"] = "participant_failed"
            raise RuntimeError("Current official verifier did not pass the block")
        if watcher.poll() is not None:
            save(block_dir / "pollution.json", {"at": now(), "phase": "final_block_monitor_check",
                                               "watcher_returncode": watcher.poll()})
            raise BlockPollution("Docker event monitor ended before complete block validation")
        active = command("docker", "ps", "--format", "{{.ID}} {{.Names}}")
        if active:
            save(block_dir / "pollution.json", {"at": now(), "phase": "before_event_handoff",
                                               "active_containers": active})
            raise BlockPollution("Co-resident Docker container appeared before event handoff")
        watcher.terminate()
        try:
            watcher.wait(timeout=10)
        except subprocess.TimeoutExpired as exc:
            watcher.kill()
            watcher.wait(timeout=10)
            raise BlockPollution("Docker event monitor did not stop cleanly") from exc
        event_handle.close()
        events = (block_dir / "docker-events.jsonl").read_text().splitlines()
        starts = defaultdict(int)
        for line in events:
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                save(block_dir / "pollution.json", {"at": now(), "phase": "final_event_audit",
                                                   "unparseable_event_line": line[:1000]})
                raise BlockPollution("Docker event monitor emitted a non-JSON line") from exc
            name = event.get("Actor", {}).get("Attributes", {}).get("name", "")
            if event.get("Type") != "container":
                continue
            if name not in expected_names:
                save(block_dir / "pollution.json", {"at": now(), "phase": "final_event_audit",
                                                   "unexpected_container_name": name, "event": event})
                raise BlockPollution("Unrelated container lifecycle event polluted entire block")
            if event.get("Action") == "start":
                starts[name] += 1
        if set(starts) != expected_names or any(count != 1 for count in starts.values()):
            save(block_dir / "pollution.json", {"at": now(), "phase": "final_event_audit",
                                               "expected_start_count": 142, "observed_start_count": sum(starts.values()),
                                               "missing_starts": sorted(expected_names - set(starts)),
                                               "duplicate_starts": sorted(name for name, count in starts.items() if count != 1)})
            raise BlockPollution("Docker event monitor missed or duplicated a scheduled container start")
        active = command("docker", "ps", "--format", "{{.ID}} {{.Names}}")
        if active:
            save(block_dir / "pollution.json", {"at": now(), "phase": "after_event_handoff",
                                               "active_containers": active})
            raise BlockPollution("Co-resident Docker container appeared during event handoff")
        save(block_dir / "event-audit.json", {"expected_container_names": sorted(expected_names),
                                              "observed_start_count": sum(starts.values()), "event_count": len(events),
                                              "watcher_returncode": watcher.returncode, "audited_at": now()})
        status["status"] = "complete_clean"
    except BaseException as exc:
        if status["status"] == "running":
            status["status"] = "invalidated_block" if isinstance(exc, BlockPollution) else "stop_pending_classification"
        status["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        try:
            if watcher is not None and watcher.poll() is None:
                watcher.terminate()
                watcher.wait(timeout=10)
            if not event_handle.closed:
                event_handle.close()
            status["finished_at"] = now()
            status["passed_runs"] = sum(row["status"] == "passed" for row in records)
            save(block_dir / "status.json", status)
        finally:
            # A diagnostics/monitor cleanup failure must not skip restoration.
            try:
                service_after = json.loads(command("docker", "inspect", SERVICE_ID))[0]
                if not (service_after["State"]["Running"] or service_after["State"]["Restarting"]):
                    command("docker", "start", SERVICE_ID)
                    service_after = json.loads(command("docker", "inspect", SERVICE_ID))[0]
                snapshot = require_service_running(service_after)
                snapshot["restore_requested"] = True
                save(block_dir / "service-after.json", snapshot)
            except BaseException as restore_error:
                status["status"] = "service_restore_failed"
                status["service_restore_error"] = f"{type(restore_error).__name__}: {restore_error}"
                save(block_dir / "status.json", status)
                raise
    result = summarize(plan)
    save(OUT / "summary.json", result)
    print(json.dumps({key: value for key, value in result.items() if key != "units"}), flush=True)


def recovery_check():
    """External post-interruption audit; never reclassify or rerun a block here."""
    endpoint = docker_endpoint()
    unfinished = []
    for path in sorted((OUT / "blocks").glob("*/attempt-*/status.json")):
        status = json.loads(path.read_text())
        if status["status"] == "running":
            unfinished.append(str(path.relative_to(OUT)))
    service = json.loads(command("docker", "inspect", SERVICE_ID))[0]
    snapshot = service_snapshot(service)
    try:
        require_service_running(service)
        service_restored = True
    except RuntimeError:
        service_restored = False
    result = {"checked_at": now(), "docker_endpoint": endpoint,
              "unfinished_attempts": unfinished, "service_restored": service_restored,
              "service": snapshot, "action_if_unsafe": "Restore exact service and classify interrupted attempt from retained evidence before any retry."}
    print(json.dumps(result), flush=True)
    if unfinished or not service_restored:
        raise SystemExit(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--register", action="store_true")
    mode.add_argument("--block", type=int, choices=(0, 1, 2))
    mode.add_argument("--current-check", type=Path)
    mode.add_argument("--summarize", action="store_true")
    mode.add_argument("--recovery-check", action="store_true")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Use the retained Linux/WSL T3 validation Python")
    if args.current_check:
        current_check(args.current_check)
    elif args.register:
        register()
    elif args.recovery_check:
        recovery_check()
    elif args.summarize:
        result = summarize(registered_plan()[0])
        save(OUT / "summary.json", result)
        print(json.dumps({key: value for key, value in result.items() if key != "units"}))
    else:
        run(args.block)
