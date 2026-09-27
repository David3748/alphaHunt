from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from src import satellite_vhp_wheat_validation as m


def fixture_panel():
    years=np.arange(1982,2026); n=len(years)
    p=pd.DataFrame(dict(year=years, SMN=.2+.02*np.sin(years), SMT=295+np.cos(years),
                       winter_precip_inches=10+np.sin(years)*3,winter_tavg_f=56+np.cos(years)*2,
                       prior_SMN=.2+.02*np.sin(years-1),prior_SMT=295+np.cos(years-1),
                       prior_yield=30+np.sin(years-1),prior_production_per_planted_acre=15+np.sin(years-1),
                       production_per_planted_acre=15+np.sin(years),official_june_yield=30+np.sin(years),
                       official_may_yield=31+np.sin(years)))
    p['yield']=30+np.sin(years)
    for col,suffix in [('forecast_at','06-15'),('label_available','11-01'),('satellite_available','05-27'),('ground_available','06-14')]:
        p[col]=pd.to_datetime(p.year.astype(str)+'-'+suffix)
    return p


def test_offline_real_sources_are_complete_and_specific():
    p=m.load_panel()
    assert len(p)==44 and p.year.min()==1982 and p.year.max()==2025
    assert p.ground_months.eq(8).all()
    assert p.loc[p.year.eq(2024),'yield'].item()==31
    assert p.loc[p.year.eq(2024),'production_bu'].item()==80600000
    assert p.loc[p.year.eq(2024),'planted_acres'].item()==5500000
    assert p.loc[p.year.eq(2003),'SMN'].isna().all()
    assert (p.satellite_available < p.forecast_at).all()
    assert (p.ground_available < p.forecast_at).all()


def test_smoothing_delay_and_week_selection(tmp_path):
    f=tmp_path/'sat.txt'
    head="Mean data for USA Province= 44: Texas, version='GC_current'<br>for area with 'WHEA'<br>\n"
    f.write_text(head+'\n'.join(f'{2000},{w},{.2 if 9<=w<=13 else .8},290,1,2,3,' for w in range(1,53)))
    p=m.parse_satellite(f).iloc[0]
    assert p.SMN==pytest.approx(.2)
    assert p.satellite_period_end==pd.Timestamp('2000-03-31')
    assert p.satellite_available==pd.Timestamp('2000-05-26')
    f.write_text(f.read_text().replace('2000,13,0.2,290','2000,13,0.2,-1'))
    assert m.parse_satellite(f).SMN.isna().all()


def test_ground_crosses_year_and_excludes_future():
    dates=pd.date_range('1999-01-01','2000-12-01',freq='MS')
    p=pd.Series(1.,index=dates);t=pd.Series(55.,index=dates)
    p.loc['2000-06-01':]=9999
    result=m.weather_features(p,t,[2000]).iloc[0]
    assert result.winter_precip_inches==8
    assert result.ground_available==pd.Timestamp('2000-06-14')
    p.loc['1999-10-01']=np.nan
    assert np.isnan(m.weather_features(p,t,[2000]).winter_precip_inches.iloc[0])


def test_current_and_future_targets_cannot_change_forecast():
    p=fixture_panel();old=m.predict(p,start=2010,end=2010).iloc[0]
    p.loc[p.year.ge(2010),'yield']=99999
    new=m.predict(p,start=2010,end=2010).iloc[0]
    for col in m.MODELS:
        assert new[col]==pytest.approx(old[col])
    assert new.actual != old.actual


def test_unreleased_training_labels_excluded():
    p=fixture_panel();p.loc[p.year.eq(2009),'label_available']=pd.Timestamp('2010-07-01')
    a=m.predict(p,start=2010,end=2010).iloc[0]
    p.loc[p.year.eq(2009),'yield']=999999
    b=m.predict(p,start=2010,end=2010).iloc[0]
    assert a.satellite==pytest.approx(b.satellite)
    assert a.training_last_year==2008


@pytest.mark.parametrize('column,value',[('SMN',np.inf),('SMT',np.nan),('prior_SMN',np.nan),('winter_precip_inches',np.inf),('satellite_available',pd.NaT),('ground_available',pd.Timestamp('2010-06-16'))])
def test_missing_infinite_or_late_inputs_abstain(column,value):
    p=fixture_panel();p.loc[p.year.eq(2010),column]=value
    result=m.predict(p,start=2010,end=2010).iloc[0]
    assert not result.eligible


def test_minimum_fifteen_and_common_training_support():
    p=fixture_panel();result=m.predict(p,start=1996,end=1997)
    assert not result.iloc[0].eligible and result.iloc[1].eligible
    p.loc[p.year.eq(1982),'SMN']=np.nan
    assert not m.predict(p,start=1997,end=1997).iloc[0].eligible


def test_ridge_standardization_is_training_only():
    train=pd.DataFrame(dict(x=[1.,2.,3.],y=[2.,4.,6.]));row=pd.Series(dict(x=1000.))
    expected=4+(1000-2)/np.std([1,2,3])*(3/(3+5))*np.std([2,4,6])
    assert m.ridge_predict(train,row,['x'],'y')==pytest.approx(expected)


def test_bootstrap_retains_calendar_gaps_and_reproducibility():
    a=m.paired_ci([2000,2001,2004,2005],[1,2,3,4],[0,0,0,0],[.5,1,1.5,2],draws=200)
    assert a==pytest.approx([.5,.5])
    assert m.paired_ci([2000,2001,2004,2005],[1,2,3,4],[0,0,0,0],[.5,1,1.5,2],draws=200)==a


def test_official_benchmark_is_not_training_feature():
    p=fixture_panel();a=m.predict(p,start=2010,end=2010).iloc[0]
    p['official_june_yield']=1e9;b=m.predict(p,start=2010,end=2010).iloc[0]
    assert a.satellite==pytest.approx(b.satellite)
    assert b.official_june_yield==1e9


def test_secondary_current_final_planted_area_is_not_feature():
    p=fixture_panel();p['planted_acres']=100
    a=m.predict(p,target='production_per_planted_acre',start=2010,end=2010).iloc[0]
    p.loc[p.year.eq(2010),'planted_acres']=99999
    b=m.predict(p,target='production_per_planted_acre',start=2010,end=2010).iloc[0]
    assert a.satellite==pytest.approx(b.satellite)
