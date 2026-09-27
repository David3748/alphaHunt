import numpy as np
import pandas as pd
import pytest
from src import satellite_south_africa_maize as m


def inputs():
    years=np.arange(1980,2025)
    labels=pd.DataFrame(dict(harvest_year=years,actual_t_ha=2+.05*(years-1980)+.2*np.sin(years),production_t=100.,harvested_ha=30.))
    ground=pd.DataFrame([dict(year=y,month=mo,precip_mm=10.+np.sin(y),n_days=31 if mo==8 else 30,eligible=True) for y in range(1982,2024) for mo in [8,9]])
    sst=pd.DataFrame(dict(date=pd.to_datetime([f'{y}-09-01' for y in range(1982,2024)]),september_nino34_sst_c=27+np.cos(np.arange(42))))
    return labels,ground,sst


def test_fao_year_plus_one_feature_alignment_and_lag_three():
    p=m.build_panel(*inputs());r=p[p.harvest_year.eq(2020)].iloc[0]
    assert r.forecast_at==pd.Timestamp('2019-10-20 12:00')
    assert r.sst_source_end==pd.Timestamp('2019-09-30')
    assert r.sst_available==pd.Timestamp('2019-10-20')
    assert r.prior_released_harvest==2017
    assert r.prior_label_available==pd.Timestamp('2018-12-31 23:59')
    assert r.target_start==pd.Timestamp('2020-05-01')
    assert r.label_available==pd.Timestamp('2021-12-31 23:59')


def test_completed_harvest_stress_uses_previous_harvest_not_target():
    p=m.build_panel(*inputs(),release_regime='completed_harvest_stress')
    r=p[p.harvest_year.eq(2020)].iloc[0]
    assert r.prior_released_harvest==2019
    pred=m.predict(p,start=2020,end=2020).iloc[0]
    assert pred.latest_training_harvest==2019


def test_primary_training_embargo_and_future_target_invariance():
    p=m.build_panel(*inputs());a=m.predict(p,start=2020,end=2020).iloc[0]
    assert a.latest_training_harvest==2017
    p.loc[p.harvest_year.ge(2018),'actual_value']=1000
    b=m.predict(p,start=2020,end=2020).iloc[0]
    for model in m.MODELS:assert a[model]==pytest.approx(b[model])
    assert a.actual_value != b.actual_value


@pytest.mark.parametrize('column,value',[('september_nino34_sst_c',np.inf),('august_ground_mm',np.nan),('sst_available',pd.NaT),('sst_available',pd.Timestamp('2019-10-21')),('ground_available',pd.Timestamp('2019-10-21')),('prior_label_available',pd.Timestamp('2019-10-21'))])
def test_unavailable_invalid_inputs_abstain(column,value):
    p=m.build_panel(*inputs());p.loc[p.harvest_year.eq(2020),column]=value
    assert not m.predict(p,start=2020,end=2020).iloc[0].eligible


def test_future_harvested_area_is_not_feature():
    p=m.build_panel(*inputs());a=m.predict(p,start=2020,end=2020).iloc[0]
    p.loc[p.harvest_year.ge(2018),'harvested_ha']=100000
    b=m.predict(p,start=2020,end=2020).iloc[0]
    assert a.satellite==pytest.approx(b.satellite)


def test_ground_requires_complete_correct_months():
    labels,ground,sst=inputs();ground.loc[0,'n_days']=30
    with pytest.raises(ValueError,match='Incompleteground'):m.build_panel(labels,ground,sst)
    labels,ground,sst=inputs();ground.loc[0,'month']=10
    with pytest.raises(ValueError,match='Unexpectedfeature'):m.build_panel(labels,ground,sst)


def test_low_ground_coverage_does_not_become_zero_rain():
    labels,ground,sst=inputs();ground.loc[(ground.year==2019)&(ground.month==9),'eligible']=False
    p=m.build_panel(labels,ground,sst)
    assert np.isnan(p.loc[p.harvest_year==2020,'september_ground_mm'].item())
    assert not m.predict(p,start=2020,end=2020).iloc[0].eligible


def test_minimum_training_fifteen_follows_label_embargo():
    p=m.build_panel(*inputs());a=m.predict(p,start=1999,end=2000)
    assert a.n_train.tolist()==[14,15]
    assert a.eligible.tolist()==[False,True]


def test_five_calendar_year_bootstrap_preserves_missing_gaps():
    a=m.paired_ci([2000,2001,2004,2008],[1,2,3,4],[0,0,0,0],[.5,1,1.5,2],draws=300)
    assert a==pytest.approx([.5,.5])


def test_fao_calendar_units_not_psd_marketing_year(tmp_path):
    rows=[]
    for element,unit,value in [('Production','t',12000000),('Area harvested','ha',3000000),('Yield','kg/ha',4000)]:
        rows.append(dict(Area='South Africa',Item='Maize (corn)',Element=element,Unit=unit,Value=value,Year=2024,Flag='A'))
    path=tmp_path/'fao.csv';pd.DataFrame(rows).to_csv(path,index=False)
    result=m.load_labels(path)
    assert result.harvest_year.item()==2024 and result.actual_t_ha.item()==4
    rows[0]['Unit']='1000 t';pd.DataFrame(rows).to_csv(path,index=False)
    with pytest.raises(ValueError,match='units'):m.load_labels(path)


def test_production_secondary_units_and_recent_mean():
    labels,ground,sst=inputs()
    labels['production_t']=1e6*(10+(labels.harvest_year-1980)*.1)
    panel=m.build_panel(labels,ground,sst,target='production')
    prediction=m.predict(panel,start=2020,end=2020).iloc[0]
    assert prediction.actual_value==14
    assert prediction.prior_released_outcome==pytest.approx(13.7)
    assert prediction.recent_mean==pytest.approx(np.mean([10+(year-1980)*.1 for year in range(2008,2018)]))
    labels.loc[labels.harvest_year.ge(2018),'production_t']=1e12
    labels.loc[labels.harvest_year.ge(2018),'harvested_ha']=1e12
    changed=m.predict(m.build_panel(labels,ground,sst,target='production'),start=2020,end=2020).iloc[0]
    assert prediction.satellite==pytest.approx(changed.satellite)
    assert changed.actual_value!=prediction.actual_value


def test_gate_cannot_ignore_better_simple_trend():
    p=pd.DataFrame(dict(harvest_year=np.arange(2000,2025),actual_value=4.,eligible=True,reason=''))
    for name in m.MODELS:p[name]=3.
    p['satellite']=3.5;p['trend']=3.9
    result=m.score(p)
    assert result['comparisons']['baseline']['rmse_reduction']==.5
    assert result['strongest_baseline']=='trend'
    assert not result['forecast_gate_passed']


def test_saved_official_labels_and_ground_gaps():
    labels=m.load_labels(m.DEFAULT/'inputs/faostat_south_africa_maize.csv')
    assert labels.production_flag.eq('A').all()
    assert labels.harvest_year.min()==1980 and labels.harvest_year.max()==2024
    ground=pd.read_csv(m.DEFAULT/'inputs/ground_augsep.csv')
    bad=ground[~ground.eligible]
    assert set(zip(bad.year,bad.month))=={(1985,8),(1986,9),(2004,9)}
    assert bad.precip_mm.isna().all()
