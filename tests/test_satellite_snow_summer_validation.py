import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import satellite_snow_summer_validation as s


def fixture():
    rng=np.random.default_rng(9);n=25
    rain=rng.uniform(200,1400,n);flow=rng.uniform(1e5,9e5,n);snow=rng.uniform(.1,.7,n)
    return pd.DataFrame(dict(year=np.arange(2000,2025),runoff_af=rain*100+flow*.2+snow*1e5,
                             winter_precip_mm=rain,winter_flow_af=flow,winter_flow_may_af=flow+2e4,
                             snow_frequency=snow,ground_swe_mm=rain*.1,eligible_snow=True,prior_runoff_af=2e5))


def test_ridge_standardizes_only_training_and_preserves_units():
    a=fixture();train=a.iloc[:10];row=a.iloc[10]
    expected=s.ridge_predict(train,row,['winter_precip_mm','snow_frequency'])
    a.winter_precip_mm*=1000
    actual=s.ridge_predict(a.iloc[:10],a.iloc[10],['winter_precip_mm','snow_frequency'])
    assert np.isclose(expected,actual)


def test_constant_feature_is_supported():
    a=fixture();a['ground_swe_mm']=0
    assert np.isfinite(s.ridge_predict(a.iloc[:10],a.iloc[10],['ground_swe_mm']))


def test_unknown_target_does_not_stop_forecast_or_leak():
    a=fixture();before=s.predict(a).iloc[-1];a.loc[a.year.eq(2024),'runoff_af']=np.nan
    after=s.predict(a).iloc[-1]
    assert after.eligible and after.weather_flow_snow==before.weather_flow_snow
    assert after.training_last_year==2023


def test_swe_and_fresh_flow_use_correct_missing_support():
    a=fixture();a.loc[a.year.eq(2005),'ground_swe_mm']=np.nan
    a.loc[a.year.eq(2023),'winter_flow_may_af']=np.nan
    old=s.predict(a,with_swe=True);fresh=s.predict(a,with_swe=True,fresh_flow=True)
    assert old.loc[old.eligible,'year'].min()==2011
    assert old.loc[old.year.eq(2023),'eligible'].item()
    assert not fresh.loc[fresh.year.eq(2023),'eligible'].item()


def test_current_and_future_targets_do_not_enter_training():
    a=fixture();before=s.predict(a);a.loc[a.year.ge(2015),'runoff_af']=1e12
    after=s.predict(a)
    assert np.allclose(before.loc[before.year.between(2010,2015),'weather_flow_snow'],after.loc[after.year.between(2010,2015),'weather_flow_snow'])


def test_target_and_prior_flow_calendar_boundaries(tmp_path):
    (tmp_path/'ground').mkdir()
    dates=pd.date_range('1999-01-01','2000-12-01',freq='MS')
    pd.DataFrame({'STATION_ID':'SBF','SENSOR_NUMBER':65,'UNITS':'AF','DATE TIME':dates.strftime('%Y%m%d %H%M'),'VALUE':dates.month}).to_csv(tmp_path/'sbf_monthly_fnf.csv',index=False)
    pd.DataFrame({'year':[2000],'snow_frequency':[.4],'eligible_snow':[True]}).to_csv(tmp_path/'annual_snow.csv',index=False)
    pd.DataFrame({'year':[2000],'winter_precip_mm':[100.],'ground_swe_mm':[10.]}).to_csv(tmp_path/'ground/summer_ground.csv',index=False)
    import json
    (tmp_path/'source_manifest.json').write_text(json.dumps({'cdec_sha256':s.daily.sha256(tmp_path/'sbf_monthly_fnf.csv')}))
    row=s.load_panel(tmp_path).iloc[0]
    assert row.runoff_af==7+8+9
    assert row.winter_flow_af==10+11+12+1+2+3+4
    assert row.winter_flow_may_af==row.winter_flow_af+5
    assert row.prior_runoff_af==7+8+9
