"""One experimental optimization: copy only truly immutable flat log values cheaply."""
import hashlib
import inspect
from pathlib import Path
import abides_core.agent

path=Path(inspect.getfile(abides_core.agent))
source=path.read_text()
if hashlib.sha256(path.read_bytes()).hexdigest()!='13763aaf6f5207314689f98c4e7708dd1e910e2bd20644d0f17cf39df07b74ca':
    raise RuntimeError('Pinned Agent source changed')
needle='            event = deepcopy(event)'
if source.count(needle)!=1:raise RuntimeError('Ambiguous log-copy site')
helper='''

# Exact built-in types only: subclasses may own mutable state or custom copy hooks.
_LOG_ATOMIC_TYPES = frozenset((type(None), bool, int, float, complex, str, bytes))


def _copy_log_event(event):
    if type(event) in _LOG_ATOMIC_TYPES:
        return event
    if type(event) is dict and all(type(k) in _LOG_ATOMIC_TYPES and
                                  type(v) in _LOG_ATOMIC_TYPES for k, v in event.items()):
        return event.copy()
    return deepcopy(event)
'''
updated=source.replace(needle,'            event = _copy_log_event(event)')+helper
path.write_text(updated)
print('agent.py before',hashlib.sha256(source.encode()).hexdigest())
print('agent.py after',hashlib.sha256(updated.encode()).hexdigest())
