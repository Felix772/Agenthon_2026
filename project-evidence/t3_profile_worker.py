"""Local-only diagnostic wrapper; never included in a competition image."""
import argparse
import cProfile
import json
from pathlib import Path
import pstats
import resource
import time
import tracemalloc

parser = argparse.ArgumentParser()
parser.add_argument('--mode', choices=['timing', 'cpu', 'allocations'], required=True)
args = parser.parse_args()
started = time.perf_counter()
profile = cProfile.Profile(timer=time.perf_counter) if args.mode == 'cpu' else None
if profile:
    profile.enable()
if args.mode == 'allocations':
    tracemalloc.start(10)
from abides_fork.simulate import simulate
events = simulate('/input/scenario.json', '/output/trace.parquet')
if profile:
    profile.disable()
elapsed = time.perf_counter() - started
result = {'mode': args.mode, 'process_wall_sec': elapsed,
          'process_peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
          'events': events}
peak_path = Path('/sys/fs/cgroup/memory.peak')
result['cgroup_peak_bytes'] = int(peak_path.read_text()) if peak_path.exists() else None
if profile:
    profile.dump_stats('/diagnostic/cpu.prof')
    stats = pstats.Stats(profile)
    rows = [{'file': key[0], 'line': key[1], 'function': key[2],
             'primitive_calls': val[0], 'calls': val[1], 'self_sec': val[2],
             'cumulative_sec': val[3]} for key, val in stats.stats.items()]
    rows.sort(key=lambda row: row['self_sec'], reverse=True)
    result['functions'] = rows
    result['profile_total_self_sec'] = stats.total_tt
if args.mode == 'allocations':
    current, peak = tracemalloc.get_traced_memory()
    result['tracemalloc'] = {'current_bytes': current, 'peak_bytes': peak,
        'limitations': 'Python allocations only; includes imports, excludes much native NumPy/Arrow memory; surviving allocations, not allocation-time ranking.',
        'surviving_top': [{'traceback': str(item.traceback), 'bytes': item.size, 'count': item.count}
                          for item in tracemalloc.take_snapshot().statistics('traceback')[:30]]}
    tracemalloc.stop()
Path('/diagnostic/measurements.json').write_text(json.dumps(result, indent=2) + '\n')
