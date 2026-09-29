import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import satellite_snow_kings_validation as k


def test_target_parser_rejects_daily_cfs_and_wrong_station(tmp_path):
    p=tmp_path/'flow.csv';base=dict(STATION_ID=['KGF'],DURATION=['M'],SENSOR_NUMBER=[65],UNITS=['AF'],VALUE=[123],**{'DATE TIME':['20000101 0000']})
    pd.DataFrame(base).to_csv(p,index=False);assert k.parse_kings_fnf(p).iloc[0]==123
    for col,value in [('STATION_ID','SBF'),('DURATION','D'),('SENSOR_NUMBER',8),('UNITS','CFS')]:
        bad={**base,col:[value]};pd.DataFrame(bad).to_csv(p,index=False)
        with pytest.raises(ValueError):k.parse_kings_fnf(p)


def test_pooled_shared_years_do_not_double_sample_and_scale_is_initial_only():
    years=np.arange(2010,2024);frame=pd.DataFrame(dict(year=years,eligible=True,runoff_af=100.,weather_flow=90.,weather_flow_snow=95.))
    panel=pd.DataFrame(dict(year=np.arange(2000,2024),runoff_af=100.))
    r=k.pooled_metrics({'a':frame,'b':frame},{'a':panel,'b':panel})
    assert r['n_calendar_years']==14 and r['n_basins']==2
    assert r['rmse_reduction']==.5
    assert np.allclose(r['rmse_reduction_ci95'],[.5,.5])
    panel.loc[panel.year.ge(2010),'runoff_af']=1e9
    assert k.initial_scale(panel)==100


def test_equal_basin_normalization_is_unit_scale_invariant():
    years=np.arange(2010,2024);a=pd.DataFrame(dict(year=years,eligible=True,runoff_af=100.,weather_flow=90.,weather_flow_snow=95.))
    p=pd.DataFrame(dict(year=np.arange(2000,2024),runoff_af=100.))
    b=a.copy();b[['runoff_af','weather_flow','weather_flow_snow']]*=10;q=p.copy();q.runoff_af*=10
    r=k.pooled_metrics({'a':a,'b':b},{'a':p,'b':q})
    assert np.isclose(r['rmse_reduction'],.5)


def test_missing_basin_year_is_not_treated_as_independent_extra_year():
    years=np.arange(2010,2024);a=pd.DataFrame(dict(year=years,eligible=True,runoff_af=100.,weather_flow=90.,weather_flow_snow=95.));b=a.loc[a.year.ne(2015)]
    p=pd.DataFrame(dict(year=np.arange(2000,2024),runoff_af=100.))
    r=k.pooled_metrics({'a':a,'b':b},{'a':p,'b':p})
    assert r['n_calendar_years']==13 and 2015 not in r['evaluated_years']


def test_pooled_mae_drops_a_calendar_year_missing_in_either_basin():
    years=np.arange(2010,2024);a=pd.DataFrame(dict(year=years,eligible=True,runoff_af=100.,weather_flow=90.,weather_flow_snow=95.));b=a.loc[a.year.ne(2015)].copy()
    a.loc[a.year.eq(2015),'weather_flow_snow']=-1e8
    p=pd.DataFrame(dict(year=np.arange(2000,2024),runoff_af=100.))
    r=k.pooled_metrics({'a':a,'b':b},{'a':p,'b':p})
    assert np.isclose(r['mae_reduction'],.5)
