import numpy as np
import pandas as pd
import pytest
import json
from src import satellite_southern_africa_maize as m


def training():
    years=np.arange(1983,2003);rng=np.random.default_rng(42)
    frame=pd.DataFrame(dict(harvest_year=years,prior_released_outcome=rng.normal(3,1,len(years)),
        august_ground_mm=rng.uniform(0,30,len(years)),september_ground_mm=rng.uniform(0,40,len(years)),
        september_nino34_sst_c=rng.normal(27,1,len(years))))
    frame['actual_value']=1+.1*(years-1983)
    row=frame.iloc[-1].copy();row.harvest_year=2020
    return frame,row


def panel():
    years=np.arange(1980,2025)
    labels=pd.DataFrame(dict(harvest_year=years,actual_t_ha=2+.05*(years-1980),production_t=10e6,harvested_ha=3e6))
    ground=pd.DataFrame([dict(year=y,month=mo,precip_mm=10+np.sin(y),n_days=31 if mo==8 else 30,eligible=True) for y in range(1982,2024) for mo in [8,9]])
    sst=pd.DataFrame(dict(date=[f'{y}-09-01' for y in range(1982,2024)],september_nino34_sst_c=27+np.cos(np.arange(42))))
    return m.core.build_panel(labels,ground,sst)


def test_linear_technology_trend_is_not_shrunk():
    train,row=training()
    assert m.partial_ridge(train,row,m.CONTROLS+m.core.SAT)==pytest.approx(1+.1*(2020-1983))


def test_matches_block_normal_equations_with_zero_time_penalty():
    train,row=training();train.actual_value+=np.sin(train.harvest_year)
    cols=m.CONTROLS+m.core.SAT
    x=train[cols].to_numpy();center=x.mean(axis=0);scale=x.std(axis=0);z=(x-center)/scale
    tcenter=train.harvest_year.mean();u=np.c_[np.ones(len(train)),train.harvest_year-tcenter]
    design=np.c_[u,z];penalty=np.diag([0.,0.]+[5.]*len(cols))
    beta=np.linalg.solve(design.T@design+penalty,design.T@train.actual_value)
    new=np.r_[1.,row.harvest_year-tcenter,(row[cols].to_numpy(float)-center)/scale]
    assert m.partial_ridge(train,row,cols)==pytest.approx(float(new@beta))


def test_unpenalized_trend_equivariance():
    train,row=training();a=m.partial_ridge(train,row,m.CONTROLS)
    train.actual_value+=5+.02*(train.harvest_year-1980)
    b=m.partial_ridge(train,row,m.CONTROLS)
    assert b-a==pytest.approx(5+.02*(row.harvest_year-1980))


def test_target_and_unreleased_outcomes_do_not_change_forecast():
    p=panel();a=m.predict(p,start=2020,end=2020).iloc[0]
    p.loc[p.harvest_year.ge(2018),'actual_value']=10000
    b=m.predict(p,start=2020,end=2020).iloc[0]
    assert a.latest_training_harvest==2017
    for name in m.core.MODELS:assert a[name]==pytest.approx(b[name])
    assert a.actual_value!=b.actual_value


def test_normalization_uses_fixed_initial_training_mean():
    p=panel();pred=m.predict(p)
    expected=p[p.harvest_year.between(1983,1997)].actual_value.mean()
    assert pred.normalization_scale.nunique()==1
    assert pred.normalization_scale.iloc[0]==pytest.approx(expected)
    p.loc[p.harvest_year.ge(1998),'actual_value']*=10
    changed=m.predict(p)
    assert np.allclose(pred.normalization_scale,changed.normalization_scale)


def test_late_and_nonfinite_features_abstain():
    p=panel();p.loc[p.harvest_year.eq(2020),'sst_available']=pd.Timestamp('2019-10-21')
    p.loc[p.harvest_year.eq(2021),'august_ground_mm']=np.inf
    assert not m.predict(p,start=2020,end=2021).eligible.any()


def test_units_normalized_and_country_weights_equal():
    p=pd.DataFrame(dict(country=['ZM','ZM','ZW'],actual_value=[2.,2.,200.],satellite=[1.,1.,0.],normalization_scale=[2.,2.,200.]))
    score=m.normalized_metrics(p,'satellite')
    assert score['rmse']==pytest.approx(np.sqrt((.25+1)/2))
    assert score['mae']==.75


def test_shared_country_shocks_do_not_narrow_ci():
    p=pd.DataFrame(dict(country='ZM',harvest_year=np.arange(2000,2025),actual_value=np.sin(np.arange(25))+3,
        satellite=1.,baseline=0.,normalization_scale=2.))
    a=m.cluster_ci(p,'baseline',draws=200)
    b=m.cluster_ci(pd.concat([p,p.assign(country='ZW')]),'baseline',draws=200)
    assert a==pytest.approx(b)


def synthetic_predictions():
    p=pd.DataFrame(dict(country='ZM',harvest_year=np.arange(2000,2025),actual_value=10.,eligible=True,reason='',normalization_scale=10.))
    for name in m.core.MODELS:p[name]=0.
    p['satellite']=9.
    return p


def test_one_bad_country_cannot_be_hidden_by_pooled_success():
    a=synthetic_predictions();b=a.assign(country='ZW',trend=9.5)
    result=m.pooled_score(pd.concat([a,b]))
    assert result['comparisons'][result['strongest_pooled_baseline']]['rmse_reduction']>.05
    assert not result['country_improves_over_own_strongest']['ZW']
    assert not result['confirmation_gate_passed']


def test_missing_country_or_insufficient_country_sample_fails():
    p=synthetic_predictions()
    assert not m.pooled_score(p)['confirmation_gate_passed']
    assert not m.pooled_score(pd.concat([p,p.iloc[:19].assign(country='ZW')]))['confirmation_gate_passed']
    assert m.pooled_score(pd.concat([p,p.assign(country='ZW')]))['confirmation_gate_passed']


def test_all_abstentions_are_explicit_and_do_not_raise():
    p=panel();p['august_ground_mm']=np.nan
    predictions=m.predict(p)
    assert not predictions.eligible.any()
    assert predictions[m.core.MODELS].isna().all().all()
    assert m.core.score(predictions)['forecast_gate_passed'] is False
    predictions['country']='ZM'
    assert m.pooled_score(predictions)['confirmation_gate_passed'] is False


def test_confirmation_ground_rejects_mutated_frozen_mask(tmp_path):
    ground=tmp_path/'ground';ground.mkdir()
    countries={}
    for country in ['ZM','ZW']:
        path=ground/f'{country}_mask.npz';path.write_bytes(b'frozen-mask')
        countries[country]=dict(mask_file=path.name,mask_sha256=m.core.sha(path))
    geography=ground/'geography_freeze.json'
    geography.write_text(json.dumps(dict(countries=countries)))
    (ground/'source_manifest.json').write_text(json.dumps([dict(path=geography.name,sha256=m.core.sha(geography))]))
    m.verify_confirmation_ground(tmp_path)
    (ground/'ZW_mask.npz').write_bytes(b'changed-mask')
    with pytest.raises(ValueError,match='Changed frozen national mask'):
        m.verify_confirmation_ground(tmp_path)
