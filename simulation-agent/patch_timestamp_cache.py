"""Build-time diagnostic-formatting optimization for the pinned baseline only."""
import hashlib
import inspect
from pathlib import Path
import abides_core.utils

path=Path(inspect.getfile(abides_core.utils))
source=path.read_text()
needle='''def fmt_ts(timestamp: NanosecondTime) -> str:
    """
    Converts a timestamp stored as nanoseconds into a human readable string.
    """
    return pd.Timestamp(timestamp, unit="ns").strftime("%Y-%m-%d %H:%M:%S")'''
replacement='''from functools import lru_cache as _fmt_lru_cache
from numbers import Integral as _FmtIntegral


def _fmt_ts_uncached(timestamp: NanosecondTime) -> str:
    return pd.Timestamp(timestamp, unit="ns").strftime("%Y-%m-%d %H:%M:%S")


@_fmt_lru_cache(maxsize=4096)
def _fmt_second(second: int) -> str:
    return _fmt_ts_uncached(second * 1_000_000_000)


def fmt_ts(timestamp: NanosecondTime) -> str:
    """Format exactly as the baseline, caching repeated whole-second strings.

    Only valid integral nanoseconds take the cache path. The first partially
    representable second of pandas' range and every other input keep the
    original behavior, including exceptions. Cache keys never retain messages,
    orders, agents or simulation state; formatting consumes no RNG.
    """
    if (isinstance(timestamp, _FmtIntegral) and not isinstance(timestamp, bool)
            and -9_223_372_036_000_000_000 <= timestamp <= 9_223_372_036_854_775_807):
        return _fmt_second(int(timestamp) // 1_000_000_000)
    return _fmt_ts_uncached(timestamp)'''
if source.count(needle)!=1:
    raise RuntimeError('Pinned fmt_ts implementation changed; refuse an ambiguous patch')
updated=source.replace(needle,replacement)
path.write_text(updated)
print('Patched',path)
print('before_sha256',hashlib.sha256(source.encode()).hexdigest())
print('after_sha256',hashlib.sha256(updated.encode()).hexdigest())
