"""Fixed, serial local profiling plan. Preserve every log and failed run."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / 'project-evidence'
OUT = E / 't3-03-profile-20260924'
IMAGE = 'track3-abides-baseline:agenthon-local-20260922'
SCENARIOS = ['as01_base_mix', 'as03_mm_liquidity_heavy', 'gb_base_30agent_30s']
OUT.mkdir(exist_ok=False)
image_id = subprocess.check_output(['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}'], text=True).strip()
assert image_id == 'sha256:3d534ebbd778dfdea64d2ed5c2844430208465cd670e530893779ad06ae9bf22'
plan = {'created_at': datetime.now(timezone.utc).isoformat(), 'image_id': image_id,
        'scenarios': SCENARIOS, 'timing_repeats': 3, 'cpu_profile_repeats': 1,
        'allocation_profile_scenarios': [SCENARIOS[0]], 'concurrency': 1, 'warmup_runs': 0,
        'scope': 'Local diagnostic convention, not organizer Final timing; fresh container per run',
        'limits': {'cpus': 4, 'memory_bytes': 3 * 1024**3, 'swap': False, 'network': 'none'},
        'host_docker_info': json.loads(subprocess.check_output(['docker', 'info', '--format', '{{json .}}'], text=True))}
# Limit host inventory to non-sensitive resource/runtime facts.
plan['host_docker_info'] = {key: plan['host_docker_info'].get(key) for key in ['NCPU','MemTotal','OSType','Architecture','DefaultRuntime','ServerVersion']}
(OUT / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
records = []
for scenario_name in SCENARIOS:
    source = ROOT / '.validation/track3-current-update/regression_suite/scenarios' / (scenario_name + '.json')
    input_dir = OUT / scenario_name / 'input'
    input_dir.mkdir(parents=True)
    shutil.copyfile(source, input_dir / 'scenario.json')
    modes = [('timing', i) for i in range(1, 4)] + [('cpu', 1)]
    if scenario_name == SCENARIOS[0]:
        modes.append(('allocations', 1))
    for mode, repeat in modes:
        run_dir = input_dir.parent / f'{mode}-{repeat}'
        output = run_dir / 'output'
        diagnostic = run_dir / 'diagnostic'
        output.mkdir(parents=True)
        diagnostic.mkdir()
        name = f'agenthon-t3-profile-{scenario_name.replace("_", "-")}-{mode}-{repeat}'
        command = ['docker', 'run', '--name', name, '--network', 'none', '--read-only',
                   '--user', '65534:65534', '--cap-drop=ALL', '--security-opt', 'no-new-privileges',
                   '--cpus', '4', '--memory', '3g', '--memory-swap', '3g', '--pids-limit', '256',
                   '--ulimit', 'nofile=1024:1024', '--ulimit', 'nproc=256:256',
                   '--ulimit', 'fsize=67108864:67108864',
                   '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777',
                   '--tmpfs', '/work:rw,noexec,nosuid,nodev,size=64m,mode=1777',
                   '--mount', f'type=bind,src={input_dir},dst=/input,readonly',
                   '--mount', f'type=bind,src={output},dst=/output',
                   '--mount', f'type=bind,src={diagnostic},dst=/diagnostic',
                   '--mount', f'type=bind,src={E / "t3_profile_worker.py"},dst=/diagnostic_code/worker.py,readonly',
                   IMAGE, 'python', '/diagnostic_code/worker.py', '--mode', mode]
        record = {'scenario': scenario_name, 'mode': mode, 'repeat': repeat,
                  'input_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'command': command,
                  'started_at': datetime.now(timezone.utc).isoformat()}
        print(f'Start {scenario_name} {mode} {repeat}', flush=True)
        started = time.perf_counter()
        try:
            with (run_dir / 'run.log').open('w') as log:
                completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=240)
            record.update(exit_code=completed.returncode, host_wall_sec=time.perf_counter() - started)
            inspection = json.loads(subprocess.check_output(['docker', 'inspect', name], text=True))[0]
            record['container_state'] = inspection['State']
            if completed.returncode != 0:
                raise RuntimeError('Simulation failed; inspect preserved log')
            record['measurements'] = json.loads((diagnostic / 'measurements.json').read_text())
            record['output_files'] = {p.name: {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size} for p in output.iterdir()}
            assert sum(item['bytes'] for item in record['output_files'].values()) <= 64 * 1024**2
        except Exception as exc:
            record['error'] = str(exc)
            raise
        finally:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
            records.append(record)
            (OUT / 'runs.json').write_text(json.dumps(records, indent=2) + '\n')
        print(f'Completed {scenario_name} {mode} {repeat}: {record["host_wall_sec"]:.2f}s', flush=True)

summary = {'plan': 'plan.json', 'image_id': image_id, 'scenarios': [], 'production_timing_verified': False}
for scenario_name in SCENARIOS:
    selected = [row for row in records if row['scenario'] == scenario_name]
    for filename in ['trace.parquet', 'message_trace.parquet']:
        assert len({row['output_files'][filename]['sha256'] for row in selected}) == 1, (scenario_name, filename)
    assert len({row['measurements']['events']['n_events'] for row in selected}) == 1
    timed = [row for row in selected if row['mode'] == 'timing']
    summary['scenarios'].append({'name': scenario_name, 'trace_repeat_identical': True,
        'timing_host_wall_sec': [row['host_wall_sec'] for row in timed],
        'timing_process_wall_sec': [row['measurements']['process_wall_sec'] for row in timed],
        'median_host_wall_sec': statistics.median(row['host_wall_sec'] for row in timed),
        'peak_rss_bytes': max(row['measurements']['process_peak_rss_bytes'] for row in timed),
        'cgroup_peak_bytes': max(row['measurements']['cgroup_peak_bytes'] or 0 for row in timed),
        'n_events': timed[0]['measurements']['events']['n_events']})
(OUT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps(summary, indent=2), flush=True)
