"""Temporal isolation, target transforms and paired denominator regression checks."""
import numpy as np
import pandas as pd
import pytest
from evaluation import BASE, actuals, aggregate, configurations, freeze_fold, sample_fold


@pytest.mark.parametrize('monthly,target', [(False,'level'),(False,'log_return'),(True,'level')])
def test_future_values_cannot_change_fit_or_samples(monthly,target):
    index=pd.date_range('2000-01-01',periods=240,freq='MS') if monthly else pd.bdate_range('2010-01-01','2017-12-31')
    rng=np.random.default_rng(13)
    wide=pd.DataFrame(rng.normal(0,.01,(len(index),2)),index=index,columns=['A','B'])
    if target=='level': wide=wide.cumsum()+10
    fold=freeze_fold(wide,2014,monthly)
    altered=wide.copy(); altered.loc[altered.index>pd.Timestamp(fold['history_end'])]=100000
    a,fa=sample_fold(wide,fold,BASE,target,monthly,1,200)
    b,fb=sample_fold(altered,fold,BASE,target,monthly,1,200)
    np.testing.assert_array_equal(a,b); assert fa==fb


def test_log_targets_are_cumulative_log_of_future_simple_returns():
    wide=pd.DataFrame({'A':[.1]*30},index=pd.bdate_range('2021-01-01',periods=30))
    fold={'cutoff':'2020-12-31','target_dates':[str(wide.index[4].date()),str(wide.index[20].date())]}
    np.testing.assert_allclose(actuals(wide,fold,'log_return'),np.log1p(.1)*np.array([5,21]))


def test_monthly_steps_include_unpublished_observation_gap():
    wide=pd.DataFrame({'A':range(240)},index=pd.date_range('2000-01-01',periods=240,freq='MS'))
    fold=freeze_fold(wide,2014,True)
    assert fold['history_end']=='2014-10-01'
    assert fold['steps']==[3,5]
    assert fold['target_dates']==['2015-01-01','2015-03-01']


def test_grid_has_no_bootstrap_shrinkage_duplicates_and_denominator_is_fixed():
    configs=configurations()
    assert len(configs)==12
    assert sum(x['method']=='bootstrap' for x in configs)==3
    with pytest.raises(ValueError,match='missing required group'): aggregate([],{})


def test_drift_ablation_does_not_change_shocks():
    wide=pd.DataFrame({'A':np.linspace(.001,.01,1500)},index=pd.bdate_range('2010-01-01',periods=1500))
    fold=freeze_fold(wide,2014,False)
    a,fit=sample_fold(wide,fold,BASE,'log_return',False,1,200)
    b,_=sample_fold(wide,fold,dict(BASE,drift_mode='zero'),'log_return',False,1,200)
    np.testing.assert_allclose(a-b,np.broadcast_to(np.array(fit['empirical_mean'])[None,:,None]*np.array(fold['steps'])[None,None,:],a.shape))
