"""Docker integration test on a synthetic CSV task, with mock inference.

Uses the unchanged official verifier on our own test unit. A pass here proves
plumbing only, not ability to solve any public or hidden finance task.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid

from evaluate import mount, run_container, stage_input, verifier_mounts, runtime_flags, output_tree_size


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def main():
    project = Path(__file__).resolve().parents[1]
    public = project.parent / "track1-coding-public"
    stamp = uuid.uuid4().hex[:10]
    results = project / "artifacts" / ("selftest-" + stamp)
    unit = results / "synthetic-unit"
    data_dir = unit / "environment/data"
    data_dir.mkdir(parents=True)
    csv = data_dir / "values.csv"
    csv.write_text("value\n2\n3\n", encoding="utf-8")
    # Copy only the public card shape into our own synthetic fixture. Public
    # tasks and checks are never edited or used as source of expected answers.
    card = (public / "units/t1-EXAMPLE-bs-greeks-pde/card.toml").read_text(encoding="utf-8")
    card = card.replace("t1-EXAMPLE-bs-greeks-pde", "t1-synthetic-plumbing").replace("1800.0", "30.0")
    (unit / "card.toml").write_text(card, encoding="utf-8")
    (unit / "instruction.md").write_text("Sum value from environment/data/values.csv and write results.json with a total field. Synthetic plumbing test only.", encoding="utf-8")
    (unit / "checks").mkdir()
    (unit / "checks/reference_data").mkdir()
    (unit / "checks/reference_data/expected.json").write_text('{"total": 5}', encoding="utf-8")
    (unit / "checks/test_outputs.py").write_text('''import json, csv, os
from pathlib import Path
def test_total():
    with Path("/input/environment/data/values.csv").open() as f:
        expected = sum(int(row["value"]) for row in csv.DictReader(f))
    result = json.loads((Path(os.environ["OUTPUT_DIR"]) / "results.json").read_text())
    assert result == {"total": expected}
    assert Path("/app/data/values.csv").read_text() == Path("/app/values.csv").read_text()
    assert json.loads(Path("/tests/reference_data/expected.json").read_text()) == result
''', encoding="utf-8")
    manifest = {"manifest_version": "2.0", "coverage": ["environment/data"], "files": [{
        "path": "environment/data/values.csv", "sha256": hashlib.sha256(csv.read_bytes()).hexdigest()}]}
    (unit / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    code = '''import json, os
from pathlib import Path
import pandas as pd
task = Path(os.environ["TASK_DIR"])
assert not (task / "checks").exists()
values = pd.read_csv(task / "environment/data/values.csv")
(Path(os.environ["OUTPUT_DIR"]) / "results.json").write_text(json.dumps({"total": int(values["value"].sum())}))
'''
    (results / "response.json").write_text(json.dumps({"code": code, "deliverables": ["results.json"]}), encoding="utf-8")
    stage_input(unit, results / "input")
    output = results / "output"
    output.mkdir()
    output.chmod(0o777)
    flags = runtime_flags(16, "128g")
    probe = subprocess.run(["docker", "run", "--rm", "--network=none", *flags,
                            "--entrypoint", "python", *mount(project / "tools", "/tools", True),
                            *mount(results / "input", "/input", True),
                            *mount(output, "/app/output"), *mount(output, "/output"),
                            "qfbench-agent:dev", "/tools/runtime_probe.py"], capture_output=True, text=True, timeout=60)
    (results / "runtime-probe.log").write_text(probe.stdout + probe.stderr, encoding="utf-8")
    if probe.returncode:
        raise RuntimeError(f"Runtime restrictions failed; inspect {results / 'runtime-probe.log'}")
    runtime = json.loads(probe.stdout)
    (results / "runtime.json").write_text(json.dumps(runtime, indent=2), encoding="utf-8")
    network, model = "qfmock-net-" + stamp, "qfmock-" + stamp
    docker("network", "create", "--internal", network)
    try:
        docker("run", "-d", "--rm", "--name", model, "--network", network, "--read-only", "--entrypoint", "python",
               *mount(project / "tools", "/tools", True), *mount(results / "response.json", "/response.json", True),
               "qfbench-agent:dev", "/tools/mock_endpoint.py", "--response", "/response.json")
        for _ in range(20):
            if "ready" in docker("logs", model):
                break
            time.sleep(0.25)
        name = "qftest-" + stamp
        command = ["docker", "run", "--rm", "--name", name, "--network", network, *flags,
                   "-e", f"MODEL_ENDPOINT=http://{model}:8000", "-e", "MODEL_NAME=synthetic-mock",
                   "-e", "MODEL_TOKEN=synthetic-test-token",
                   "-e", f"NO_PROXY={model}", "-e", "QFBENCH_NETWORK=restricted",
                   *mount(results / "input", "/input", True), *mount(output, "/app/output"), *mount(output, "/output"),
                   "qfbench-agent:dev", "solve", "--task-dir", "/input", "--out", "/app/output"]
        agent = run_container(command, name, 45, results / "agent.log")
        if agent["exit_code"] != 0:
            raise RuntimeError(f"Agent selftest failed; inspect {results / 'agent.log'}")
        output_bytes = output_tree_size(output)
        name = "qftestverify-" + stamp
        command = ["docker", "run", "--rm", "--name", name, "--network=none", "--entrypoint", "python",
                   *mount(public, "/public", True), *mount(project / "tools", "/tools", True),
                   *verifier_mounts(unit, output), *mount(results, "/review"),
                   "qfbench-harness:dev", "/tools/verify.py", "--unit", "/input", "--out", "/app/output", "--report", "/review/verdict.json"]
        verifier = run_container(command, name, 60, results / "verifier.log")
        verdict = json.loads((results / "verdict.json").read_text())
        passed = verifier["exit_code"] == 0 and verdict["admissible"] is True
        summary = {"synthetic_mock_test": True, "finance_performance_measured": False, "passed": passed,
                   "agent": agent, "verifier": verifier, "runtime": runtime, "output_bytes": output_bytes,
                   "host_memory_bytes": json.loads(docker("info", "--format", "{{json .MemTotal}}")),
                   "gpu_verified": False, "organizer_proxy_verified": False,
                   "image_id": json.loads(docker("image", "inspect", "qfbench-agent:dev"))[0]["Id"]}
        (results / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps({**summary, "results": str(results)}, indent=2))
        return 0 if passed else 1
    finally:
        subprocess.run(["docker", "rm", "-f", model], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["docker", "network", "rm", network], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    raise SystemExit(main())
