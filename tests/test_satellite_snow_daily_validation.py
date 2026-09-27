import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import satellite_snow_daily_validation as s


def fixture():
    years=np.arange(2000,2026)
    rng=np.random.default_rng(42)
    rain=rng.uniform(200,1600,len(years)); flow=rng.uniform(5e4,3e5,len(years)); snow=rng.uniform(.1,.8,len(years))
    return pd.DataFrame(dict(year=years, runoff_af=rain*700+flow*2+snow*2e5, winter_precip_mm=rain,
                             winter_flow_af=flow,march_snow_frequency=snow,eligible_snow=True,
                             ground_swe_mm=rain*.3,prior_runoff_af=np.full(len(years),5e5)))


def test_screened_snow_is_presence_not_ndsi_fraction():
    a=np.array([[10,100,0],[10,100,0],[10,100,0]],dtype=np.uint8)
    r=s.snow_feature(a,np.zeros_like(a))
    assert np.isclose(r['march_snow_frequency'],2/3)


def test_cloud_water_and_bad_qa_are_missing_not_zero():
    a=np.array([[100,250,100],[0,237,100],[0,250,100]],dtype=np.uint8)
    q=np.array([[0,0,2],[0,0,2],[1,0,2]],dtype=np.uint8)
    r=s.snow_feature(a,q)
    assert np.isclose(r['march_snow_frequency'],1/3)
    assert r['snow_pixel_coverage']==1/3
    assert not r['eligible_snow']


def test_three_valid_days_and_ninety_percent_pixels_required():
    a=np.zeros((3,10),dtype=np.uint8); q=np.zeros_like(a); a[:2,0]=250
    r=s.snow_feature(a,q)
    assert r['eligible_snow'] and r['qualified_pixels']==9
    a[:2,1]=250
    assert not s.snow_feature(a,q)['eligible_snow']


def test_missing_month_cannot_be_summed_as_zero():
    dates=pd.date_range('2000-04-01',periods=4,freq='MS')
    assert np.isnan(s.complete_sum(pd.Series([1,2,3],index=dates[:3]),dates))


def test_first_ten_training_years_and_no_future_label_leak():
    p=fixture(); a=s.predict(p)
    assert a.loc[a.eligible,'year'].min()==2010
    assert a.loc[a.year.eq(2010),'n_train'].item()==10
    p.loc[p.year.ge(2015),'runoff_af']=1e10
    b=s.predict(p)
    assert np.allclose(a.loc[a.year.le(2015),'weather_flow_snow'].dropna(),b.loc[b.year.le(2015),'weather_flow_snow'].dropna())
    assert (a.loc[a.eligible,'training_last_year']<a.loc[a.eligible,'year']).all()


def test_missing_ground_or_snow_abstains():
    p=fixture();p.loc[p.year.eq(2018),'winter_precip_mm']=np.nan;p.loc[p.year.eq(2019),'eligible_snow']=False
    a=s.predict(p)
    assert not a.loc[a.year.isin([2018,2019]),'eligible'].any()


def test_swe_models_have_identical_training_support():
    p=fixture();p.loc[p.year.eq(2005),'ground_swe_mm']=np.nan
    a=s.predict(p,with_swe=True)
    assert a.loc[a.eligible,'year'].min()==2011
    assert a.loc[a.year.eq(2011),'n_train'].item()==10
    assert a.loc[a.eligible,['weather_flow','weather_flow_snow','weather_flow_swe','weather_flow_swe_snow']].notna().all().all()


def test_bootstrap_reproducible_and_identical_models_zero_gain():
    y=np.arange(1,17); p=y+1
    assert s.paired_gain(y,p,p)==[0.,0.]
    assert s.paired_gain(y,p,y+.5)==s.paired_gain(y,p,y+.5)


def test_unknown_current_target_keeps_forecast():
    p=fixture(); expected=s.predict(p).iloc[-1]
    p.loc[p.year.eq(2025),'runoff_af']=np.nan
    actual=s.predict(p).iloc[-1]
    assert actual.eligible
    for model in s.MODELS:
        assert actual[model]==expected[model]
    result=s.evaluate(s.predict(p))
    assert 2025 not in result['evaluated_years']


def test_infinite_inputs_abstain():
    p=fixture();p.loc[p.year.eq(2024),'winter_precip_mm']=np.inf
    assert not s.predict(p).loc[lambda x:x.year.eq(2024),'eligible'].item()


def test_bootstrap_calendar_gaps_are_not_compressed():
    y=np.array([1.,2.,5.,6.]);base=y+np.array([1,2,3,4]);sat=y+np.array([2,1,4,2])
    assert s.paired_gain(y,base,sat,years=[2000,2001,2004,2005])!=s.paired_gain(y,base,sat)


def test_no_available_sources_returns_insufficient_data():
    p=fixture();p['eligible_snow']=False
    assert s.evaluate(s.predict(p))['status']=='insufficient_data'
    assert s.evaluate_swe(s.predict(p,with_swe=True))['status']=='insufficient_data'


def test_source_integrity_rejects_changed_or_missing_file(tmp_path):
    p=tmp_path/'raw';p.write_text('original');sources={'raw':s.sha256(p)}
    s.verify_named_sources(tmp_path,sources)
    p.write_text('tampered')
    import pytest
    with pytest.raises(ValueError,match='integrity'):s.verify_named_sources(tmp_path,sources)
    p.unlink()
    with pytest.raises(ValueError,match='integrity'):s.verify_named_sources(tmp_path,sources)
