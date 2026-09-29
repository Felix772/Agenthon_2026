"""Strict local Docker smoke for four scored T2 M0-control card shapes."""

from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import time
import tomllib
import uuid
import zlib

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'.validation/t2-current-20260928'))
from qfbench2_track_forecasting.scoring import _main as score_main  # noqa: E402

STAGED = ROOT/'project-evidence/t2-monthly-trend-dev-roster-20260928'
HOST = ROOT/'project-evidence/t2-m0-control-scored-roster-20260928'
SOURCE = ROOT/'.validation/t2-current-20260928/units'
OUT = ROOT/'project-evidence/t2-m0-control-docker-smoke-20260928'
IMAGE = 'forecast-agent:m0-control-20260928'
UNITS = ('t2-F1-dkk-peg-2019',             # single level
         't2-F3-divergence-2014',          # multi-asset and multi-horizon
         't2-F1-ai-mom-2024',             # single log_return
         't2-F2-cnh-tradewar-2018')        # repaired transfer input


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def keyed(path):
    return pd.read_parquet(path).sort_values(['draw','asset','horizon']).reset_index(drop=True)


def main():
    if OUT.exists():
        raise ValueError('Expected a new Docker smoke output directory')
    host_report = json.loads((HOST/'report.json').read_text())
    if host_report['count'] != 71 or not set(UNITS) <= {r['unit'] for r in host_report['records']}:
        raise ValueError('Host M0 audit missing a smoke unit')
    image_id = subprocess.check_output(['docker','image','inspect',IMAGE,'--format','{{.Id}}'],
                                       text=True).strip()
    OUT.mkdir()
    report = {'image': IMAGE, 'image_id': image_id, 'host_report_sha256': digest(HOST/'report.json'),
              'units': list(UNITS), 'records': []}
    common = ['docker','run','--rm','--network','none','--read-only',
              '--user','65534:65534','--cap-drop=ALL',
              '--security-opt','no-new-privileges','--cpus','2',
              '--memory','1g','--memory-swap','1g','--pids-limit','256',
              '--ulimit','nproc=256:256','--ulimit','nofile=1024:1024',
              '--ulimit','fsize=67108864:67108864',
              '--tmpfs','/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777']
    for unit in UNITS:
        started = time.perf_counter()
        retry = STAGED/'retry-inputs'/unit
        staged = retry if retry.is_dir() else STAGED/'inputs'/unit
        output = OUT/'outputs'/unit
        output.mkdir(parents=True)
        card_path = SOURCE/unit/'card.toml'
        card = tomllib.loads(card_path.read_text())
        cutoff = str(card['provenance']['data_cutoff'])
        name = f'agenthon-t2-m0-smoke-{uuid.uuid4().hex[:12]}'
        command = common + [
            '--name',name,
            '--mount',f'type=bind,src={staged},dst=/input,readonly',
            '--mount',f'type=bind,src={output},dst=/output',
            '-e','QFBENCH_SEED=123456789',IMAGE,'forecast',
            '--panels','/input/panels','--text','/input/text',
            '--asof',cutoff,'--out','/output/forecast.parquet',
            '--n-draws','500']
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            subprocess.run(['docker','rm','-f',name], capture_output=True, text=True)
            raise
        if completed.returncode:
            raise RuntimeError(f'Docker forecast failed for {unit}: {completed.stderr[-2000:]}')
        score_stream = io.StringIO()
        with redirect_stdout(score_stream):
            verdict_code = score_main(['score','--card',str(card_path),
                                       '--forecast',str(output/'forecast.parquet')])
        verdict = json.loads(score_stream.getvalue())
        if verdict_code or not verdict['admissible']:
            raise RuntimeError(f'Official public gates failed for {unit}: {verdict}')
        rationale = json.loads((output/'forecast_rationale.md').read_text().split('\n\n',1)[1])
        expected_seed = zlib.crc32(unit.encode('utf-8')) & 0x7FFFFFFF
        if rationale['seed'] != expected_seed or rationale['method'] != 'm0-control':
            raise RuntimeError(f'M0 control seed or routing failed for {unit}')
        observed = keyed(output/'forecast.parquet')
        host = keyed(HOST/'outputs'/unit/'forecast.parquet')
        if not observed[['draw','asset','horizon']].equals(host[['draw','asset','horizon']]):
            raise RuntimeError(f'Docker/host keys differ for {unit}')
        max_abs_error = float(np.max(np.abs(observed.value.to_numpy()-host.value.to_numpy())))
        if max_abs_error > 1e-9:
            raise RuntimeError(f'Docker/host M0 draw difference for {unit}: {max_abs_error}')
        row = {'unit':unit,'target':card['targets']['target_type'],
               'cells':len(card['targets']['asset_ids'])*len(card['targets']['horizons']),
               'seed':expected_seed,'admissible':True,
               'max_abs_error_vs_host':max_abs_error,
               'output_bytes':sum(p.stat().st_size for p in output.iterdir()),
               'elapsed_sec':time.perf_counter()-started,
               'stdout':completed.stdout[-1000:]}
        if row['output_bytes'] > 64*1024**2:
            raise RuntimeError(f'Docker output exceeds 64 MiB for {unit}')
        report['records'].append(row)
        (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print(unit, 'passed', f'{row["elapsed_sec"]:.2f}s', flush=True)
    report['passed'] = len(report['records'])
    (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='records'},indent=2))


if __name__ == '__main__':
    main()
