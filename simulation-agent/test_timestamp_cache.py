"""Formatter equivalence, including nanosecond edges and bounded cache size."""
import random
import unittest
import numpy as np
import pandas as pd
from abides_core.utils import fmt_ts, _fmt_second

def original(value):
    return pd.Timestamp(value,unit='ns').strftime('%Y-%m-%d %H:%M:%S')

def outcome(function,value):
    try:return ('value',function(value))
    except Exception as exc:return ('error',type(exc).__name__,str(exc))

class TimestampCacheTest(unittest.TestCase):
    def test_integer_domain_and_edges(self):
        values=[pd.Timestamp.min.value,pd.Timestamp.max.value,0,-1,1,
                -9223372036000000001,-9223372036000000000,9223372036854775808,
                -9223372036854775808,-9223372036854775809]
        for second in [-1000000000,-1,0,1,1612500000,9223372035]:
            for offset in [-1,0,1,499999999,999999999,1000000000]:
                values.append(second*1000000000+offset)
        rng=random.Random(20260924)
        values += [rng.randrange(pd.Timestamp.min.value,pd.Timestamp.max.value) for _ in range(20000)]
        for value in values:
            self.assertEqual(outcome(original,value),outcome(fmt_ts,value),repr(value))

    def test_non_integer_and_numpy_inputs(self):
        values=[None,True,False,pd.NaT,float('nan'),float('inf'),1.25,-1.25,
                np.int64(1612500000999999999),np.uint64(2**63-1),np.uint64(2**64-1),
                np.float64(1.5),'2021-02-05',pd.Timestamp('2021-02-05'),
                pd.Timestamp('2021-02-05',tz='US/Eastern')]
        for value in values:
            self.assertEqual(outcome(original,value),outcome(fmt_ts,value),repr(value))

    def test_cache_reuses_second_and_is_bounded(self):
        _fmt_second.cache_clear()
        fmt_ts(1612500000000000000)
        fmt_ts(1612500000999999999)
        self.assertEqual(_fmt_second.cache_info().hits,1)
        for value in range(5000):fmt_ts(value*1000000000)
        self.assertEqual(_fmt_second.cache_info().currsize,4096)
        self.assertEqual(_fmt_second.cache_info().maxsize,4096)

if __name__=='__main__':unittest.main()
