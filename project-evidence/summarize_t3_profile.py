"""Derive disjoint self-time buckets and explicitly overlapping hotspot evidence."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'project-evidence/t3-03-profile-20260924'
summary=json.loads((OUT/'summary.json').read_text())
runs=json.loads((OUT/'runs.json').read_text())
def component(row):
    path,name=row['file'],row['function']
    if path.endswith('/queue.py') or 'heapq' in name: return 'event_queue'
    if path.endswith(('/copy.py','/copyreg.py')): return 'copy_allocation'
    if path.endswith(('/order_book.py','/price_level.py')): return 'matching'
    if path.endswith('/kernel.py'): return 'kernel_dispatch'
    if path.endswith('/config.py') and name=='get_latency': return 'latency'
    if '/agents/' in path or path.endswith('/agents.py'): return 'agent_logic'
    if 'pyarrow' in path or ('/pandas/' in path and '/io/' in path) or path.endswith('/trace.py') or name=='parse_logs_df': return 'trace_and_io'
    if name=='fmt_ts' or (path.endswith('/orders.py') and name=='__str__'): return 'diagnostic_formatting'
    return 'other_including_imports'
hotspots=[]
for run in runs:
    if run['mode']!='cpu':continue
    m=run['measurements']
    buckets={}
    for row in m['functions']:
        key=component(row)
        buckets[key]=buckets.get(key,0)+row['self_sec']
    selected=[row for row in m['functions'] if row['function'] in ['fmt_ts','deepcopy','get_latency','parse_logs_df','write_table']]
    hotspots.append({'scenario':run['scenario'],'self_time_seconds':buckets,
                     'selected_functions':selected,'profile_total_self_sec':m['profile_total_self_sec']})
summary.update(hotspots=hotspots,candidates=[
    {'order':1,'change':'Cache repeated diagnostic timestamp formatting without changing the formatted string',
     'evidence':'fmt_ts self time and calls across three profiles; output truncates to seconds, repeated formats are redundant',
     'risk':'Bound cache and preserve exact formatting/invalid-value behavior; confirm trace and ledger identity and timing outside noise'},
    {'order':2,'change':'Specialize copies only for proven immutable message/order fields',
     'evidence':'copy.deepcopy call counts, cumulative time and Python-allocation profile',
     'risk':'Aliasing mutable state can change orders or traces; do not remove copying broadly'},
    {'order':3,'change':'Use a scalar latency clamp with exactly preserved boundary/RNG behavior',
     'evidence':'get_latency cumulative time and scalar NumPy clip overhead',
     'risk':'Nonfinite values, reversed bounds and rounding must match; do not change sampling or draw order'}],
    limitations=['All cProfile times include instrumentation overhead and imports; cumulative entries overlap and must not be summed',
                 'Buckets partition self time using filenames; they are diagnostic approximations, not an official SimProfile award submission',
                 'Python allocation snapshot misses native allocations and records survivors, not allocation cost by line',
                 'cgroup peak is read inside local runc by the diagnostic wrapper; it is not trusted organizer telemetry',
                 'No dedicated host: unrelated containers existed; paired alternation is required before any optimization decision',
                 'No reference/scoring changes and no simulator optimization has been applied in T3-03'])
(OUT/'profile-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
lines=['# T3-03 local profile','',
       'Three fixed public scenarios each ran three unprofiled repeats plus one CPU profile. One additional allocation profile used the base mix. All trace and message-ledger hashes match across all runs for each scenario. This is local diagnosis, not Final timing.','',
       '| Scenario | Median host seconds | Peak RSS MiB | Local cgroup peak MiB | Trace events |',
       '|---|---:|---:|---:|---:|']
for row in summary['scenarios']:
    lines.append(f'| {row["name"]} | {row["median_host_wall_sec"]:.3f} | {row["peak_rss_bytes"]/2**20:.1f} | {row["cgroup_peak_bytes"]/2**20:.1f} | {row["n_events"]} |')
lines+=['','## Measured candidate hotspots','',
        '| Scenario | fmt_ts self seconds / calls | deepcopy cumulative seconds | latency cumulative seconds |',
        '|---|---:|---:|---:|']
for row in hotspots:
    def fn(name):return next(x for x in row['selected_functions'] if x['function']==name)
    f=fn('fmt_ts')
    lines.append(f'| {row["scenario"]} | {f["self_sec"]:.3f} / {f["calls"]} | {fn("deepcopy")["cumulative_sec"]:.3f} | {fn("get_latency")["cumulative_sec"]:.3f} |')
lines+=['','These cumulative times overlap with callers and are not additive. The first candidate is a bounded diagnostic-formatting cache; copying and scalar latency are later independent candidates.','']
lines += ['- '+item for item in summary['limitations']]
lines += ['','Full disjoint self-time buckets, raw cProfile files, logs, hashes, run commands and fixed plan are in t3-03-profile-20260924/. Remote review is pending.']
(ROOT/'project-evidence/t3-03-review.md').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines[:12]))
