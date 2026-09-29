#!/usr/bin/env python3
"""Frozen geographic replication of Texas wheat method in Kansas/Oklahoma."""
from pathlib import Path
import json
import argparse
import numpy as np
import pandas as pd
try:
    from . import satellite_vhp_wheat_validation as core
except ImportError:
    import satellite_vhp_wheat_validation as core

DEFAULT=core.ROOT/'results/vhp_wheat_panel'
STATES={'KS':'17: Kansas','OK':'37: Oklahoma'}


def load_state(out,state):
    raw=out/'raw'
    for source in json.loads((raw/'manifest.json').read_text()):
        if core.sha(raw/source['path']) != source['sha256']:
            raise ValueError('Raw source hash mismatch')
    satellite=core.parse_satellite(raw/f'{state}_satellite.txt',STATES[state])
    weather=core.weather_features(core.parse_weather(raw/f'{state}_pcp.csv'),core.parse_weather(raw/f'{state}_tavg.csv'),satellite.year)
    labels=core.parse_nass(raw/'nass_ks_ok_winter_wheat.tsv',state)
    panel=satellite.merge(weather,on='year',validate='one_to_one').merge(labels,on='year',validate='one_to_one')
    panel['forecast_at']=pd.to_datetime(panel.year.astype(str)+'-06-15')
    lag=satellite[['year']+core.SAT].copy();lag.year+=1
    panel=panel.merge(lag.rename(columns={k:'prior_'+k for k in core.SAT}),on='year',how='left',validate='one_to_one')
    for target in ['yield','production_per_planted_acre']:
        lag=labels[['year',target]].copy();lag.year+=1
        panel=panel.merge(lag.rename(columns={target:'prior_'+target}),on='year',how='left',validate='one_to_one')
    return panel.sort_values('year').reset_index(drop=True)


def equal_state_metrics(frame, model):
    scores=[core.metrics(g.actual,g[model]) for _,g in frame.groupby('state')]
    return dict(rmse=float(np.sqrt(np.mean([x['rmse']**2 for x in scores]))),
                mae=float(np.mean([x['mae'] for x in scores])))


def cluster_ci(frame,base,draws=10000):
    if frame.duplicated(['state','year']).any():
        raise ValueError('Duplicate state/year')
    years=np.arange(int(frame.year.min()),int(frame.year.max())+1)
    states=sorted(frame.state.unique())
    errors=[]
    for model in [base,'satellite']:
        e=frame.assign(error=(frame.actual-frame[model])**2).pivot(index='year',columns='state',values='error')
        errors.append(e.reindex(index=years,columns=states).to_numpy())
    n=len(years);rng=np.random.default_rng(20260927)
    starts=rng.integers(0,n,size=(draws,(n+1)//2))
    ids=np.stack([starts,(starts+1)%n],axis=-1).reshape(draws,-1)[:,:n]
    # One calendar-year draw is shared across every state. State cells missing
    # in that year remain absent; each state's MSE gets equal weight.
    estimates=[]
    for error in errors:
        sample=error[ids,:];counts=np.isfinite(sample).sum(axis=1)
        mse=np.divide(np.nansum(sample,axis=1),counts,out=np.full(counts.shape,np.nan),where=counts>0)
        estimates.append(np.sqrt(np.mean(mse,axis=1)))
    with np.errstate(divide='ignore',invalid='ignore'):
        gains=1-estimates[1]/estimates[0]
    gains=gains[np.isfinite(gains)]
    return list(map(float,np.quantile(gains,[.025,.975]))) if len(gains) else [None,None]


def pooled_score(frame, expected_states=None):
    held=frame[frame.eligible].copy()
    held=held[np.isfinite(held[['actual']+core.MODELS]).all(axis=1)]
    if held.empty:return dict(status='insufficient_data',frozen_gate_passed=False)
    models={m:equal_state_metrics(held,m) for m in core.MODELS}
    comparisons={}
    for name in core.MODELS:
        if name=='satellite':continue
        a,b=models['satellite'],models[name]
        comparisons[name]=dict(rmse_reduction=1-a['rmse']/b['rmse'],mae_reduction=1-a['mae']/b['mae'],
                               rmse_reduction_ci95=cluster_ci(held,name))
    counts=held.groupby('state').year.nunique().to_dict();main=comparisons['weather']
    passed=bool((expected_states is None or set(counts)==set(expected_states)) and min(counts.values())>=20 and main['rmse_reduction']>=.05 and main['mae_reduction']>0
                and main['rmse_reduction_ci95'][0] is not None and main['rmse_reduction_ci95'][0]>0
                and all(comparisons[n]['rmse_reduction']>0 and comparisons[n]['mae_reduction']>0 for n in ['mean','persistence','trend']))
    result=dict(states=sorted(counts),n_calendar_years=int(held.year.nunique()),n_per_state=counts,models=models,
                comparisons=comparisons,frozen_gate_passed=passed,
                aggregation='Equal state MSE then root; paired calendar-year clusters, not independent state-years')
    if 'official_june_yield' in held:
        part=held[np.isfinite(held.official_june_yield)]
        if len(part):
            a=equal_state_metrics(part,'satellite');b=equal_state_metrics(part,'official_june_yield')
            result['official_june_comparison']=dict(n_per_state=part.groupby('state').year.nunique().to_dict(),
                satellite=a,official=b,rmse_reduction=1-a['rmse']/b['rmse'],mae_reduction=1-a['mae']/b['mae'],
                rmse_reduction_ci95=cluster_ci(part,'official_june_yield'))
    return result


def run(out=DEFAULT):
    results={};all_predictions=[]
    for state in STATES:
        panel=load_state(out,state);panel.to_csv(out/f'{state}_panel.csv',index=False)
        for target,key in [('yield','primary_yield'),('production_per_planted_acre','secondary_production_intensity')]:
            predictions=core.predict(panel,target);predictions['state']=state
            predictions.to_csv(out/f'{state}_{key}_predictions.csv',index=False)
            results.setdefault(key,{'states':{}})['states'][state]=core.evaluate(predictions)
            all_predictions.append(predictions)
    for key in results:
        inherited=pd.read_csv(core.DEFAULT/f'{key}_predictions.csv');inherited['state']='TX'
        results[key]['states']['TX']=core.evaluate(inherited)
        all_predictions.append(inherited)
    combined=pd.concat(all_predictions,ignore_index=True);combined.to_csv(out/'all_predictions.csv',index=False)
    for target,key in [('yield','primary_yield'),('production_per_planted_acre','secondary_production_intensity')]:
        rows=combined[combined.target.eq(target)]
        results[key]['confirmation_KS_OK']=pooled_score(rows[rows.state.isin(['KS','OK'])], ['KS','OK'])
        results[key]['exploratory_all_three']=pooled_score(rows, ['KS','OK','TX'])
    results.update(protocol_sha256=core.sha(out/'protocol.json'),code_sha256=core.sha(Path(__file__)),
                   core_code_sha256=core.sha(Path(core.__file__)),original_vintage_operational_verification=False,
                   trading_alpha_verified=False,
                   selection_disclosure='Kansas/Oklahoma fixed after seeing failedTexas; no model/window changes; all states/targets retained. Cross-state clustered uncertainty; current-vintage only.')
    (out/'summary.json').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results,indent=2))
    return results


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output-dir',type=Path,default=DEFAULT)
    run(parser.parse_args().output_dir)
