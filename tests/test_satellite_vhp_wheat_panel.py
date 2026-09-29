import numpy as np
import pandas as pd
import pytest
from src import satellite_vhp_wheat_panel as m


def test_equal_state_weight_is_not_equal_row_weight():
    p=pd.DataFrame(dict(state=['KS','KS','OK'],actual=[2.,2.,10.],satellite=[0.,0.,0.]))
    result=m.equal_state_metrics(p,'satellite')
    assert result['rmse']==pytest.approx(np.sqrt(52))
    assert result['mae']==6


def test_perfectly_correlated_states_do_not_create_fake_independence():
    p=pd.DataFrame(dict(year=np.arange(2000,2026),state='KS',actual=np.sin(np.arange(26))+3,
                        weather=np.zeros(26),satellite=np.ones(26)))
    a=m.cluster_ci(p,'weather',draws=300)
    q=pd.concat([p,p.assign(state='OK')])
    assert m.cluster_ci(q,'weather',draws=300)==pytest.approx(a)


def test_duplicate_state_year_rejected():
    p=pd.DataFrame(dict(year=[2000,2000],state=['KS','KS'],actual=[1,2],weather=[0,0],satellite=[.5,.5]))
    with pytest.raises(ValueError,match='Duplicate'):m.cluster_ci(p,'weather')


def synthetic_predictions():
    p=pd.DataFrame(dict(year=np.arange(2000,2026),state='KS',actual=2.,eligible=True,satellite=1.))
    for name in ['weather','mean','trend','persistence','placebo']:p[name]=0.
    return p


def test_missing_confirmation_state_cannot_pass():
    p=synthetic_predictions()
    assert not m.pooled_score(p,['KS','OK'])['frozen_gate_passed']
    assert m.pooled_score(pd.concat([p,p.assign(state='OK')]),['KS','OK'])['frozen_gate_passed']


def test_twenty_year_minimum_applies_to_each_state():
    p=synthetic_predictions()
    q=pd.concat([p,p.iloc[:19].assign(state='OK')])
    assert not m.pooled_score(q,['KS','OK'])['frozen_gate_passed']


def test_offline_state_sources_are_not_texas():
    ks=m.load_state(m.DEFAULT,'KS');ok=m.load_state(m.DEFAULT,'OK')
    assert len(ks)==44 and len(ok)==44
    assert not np.allclose(ks.winter_tavg_f,ok.winter_tavg_f)
    assert ks.loc[ks.year.eq(2024),'yield'].item()==43
    assert ok.loc[ok.year.eq(2024),'yield'].item()==38
    assert (ks.satellite_available < ks.forecast_at).all()
    assert (ok.ground_available < ok.forecast_at).all()
