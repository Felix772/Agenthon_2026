"""Local-only instrumentation; no change to simulation inputs, outputs or RNG."""
import argparse
import cProfile
import importlib
import json
from pathlib import Path
import pstats
import resource
import time

p=argparse.ArgumentParser(); p.add_argument('--cpu',action='store_true'); a=p.parse_args()
start=time.perf_counter()
profile=cProfile.Profile() if a.cpu else None
if profile: profile.enable()
sim=importlib.import_module('abides_fork.simulate')
batch=importlib.import_module('abides_fork.simulate_batch')
import pandas as pd
import_sec=time.perf_counter()-start
phases={}
def timed(owner,name,label):
    original=getattr(owner,name)
    def wrapped(*args,**kwargs):
        t=time.perf_counter()
        try: return original(*args,**kwargs)
        finally: phases[label]=phases.get(label,0.)+time.perf_counter()-t
    setattr(owner,name,wrapped)
timed(sim,'build_config','configuration_sec')
timed(sim.abides,'run','simulation_sec')
timed(sim,'extract_trace','trace_extract_sec')
timed(sim,'extract_message_trace','ledger_extract_sec')
timed(pd.DataFrame,'to_parquet','parquet_io_sec')
events=(batch.simulate_batch('/input/scenarios','/output') if Path('/input/scenarios').exists()
        else sim.simulate('/input/scenario.json','/output/trace.parquet'))
elapsed=time.perf_counter()-start
if profile: profile.disable()
result={'process_wall_sec':elapsed,'import_sec':import_sec,'phases':phases,
        'other_sec':elapsed-import_sec-sum(phases.values()),'events':events,
        'process_peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
        'cgroup_peak_bytes':int(Path('/sys/fs/cgroup/memory.peak').read_text())}
if profile:
    profile.dump_stats('/diagnostic/cpu.prof')
    stats=pstats.Stats(profile)
    result['functions']=sorted([dict(file=k[0],line=k[1],function=k[2],calls=v[1],self_sec=v[2],cumulative_sec=v[3])
        for k,v in stats.stats.items()],key=lambda r:r['self_sec'],reverse=True)
Path('/diagnostic/measurements.json').write_text(json.dumps(result,indent=2)+'\n')
