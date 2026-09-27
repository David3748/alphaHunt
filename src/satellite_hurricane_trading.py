#!/usr/bin/env python3
"""Frozen insurance-versus-market diagnostic, separate from storm forecast skill."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results/satellite_validation/hurricane_trading'


def load_prices(path:Path,symbol:str)->pd.Series:
    payload=json.loads(path.read_text())['chart']
    if payload.get('error') or len(payload['result'])!=1: raise ValueError('Invalid market response')
    data=payload['result'][0]
    if data['meta']['symbol']!=symbol or data['meta']['currency']!='USD': raise ValueError('Unexpected symbol/currency')
    dates=pd.to_datetime(data['timestamp'],unit='s',utc=True).tz_convert('America/New_York').normalize().tz_localize(None)
    prices=pd.Series(data['indicators']['adjclose'][0]['adjclose'],index=dates,name=symbol)
    if prices.index.duplicated().any() or not prices.index.is_monotonic_increasing:raise ValueError('Duplicate/unordered prices')
    if not np.isfinite(prices).all() or (prices<=0).any():raise ValueError('Missing/nonpositive prices')
    return prices


def event_return(direction:int,kie_return:float,spy_return:float,days:int,cost_multiplier:float=1)->dict:
    if direction not in (-1,0,1) or days<=0:raise ValueError('Invalid position/holding period')
    gross=.5*direction*(kie_return-spy_return)
    execution=.002*abs(direction)*cost_multiplier
    borrow=.03*.5*days/365*abs(direction)
    net=gross-execution-borrow
    if net<=-1:raise ValueError('Bankruptcy; compounding invalid')
    return {'direction':direction,'gross_return':gross,'execution_cost':execution,'borrow_cost':borrow,'net_return':net}


def make_events(forecasts:pd.DataFrame,prices:pd.DataFrame,cost_multiplier:float=1)->pd.DataFrame:
    forecasts=forecasts.loc[forecasts.eligible & forecasts.year.between(2006,2025)].sort_values('year')
    if forecasts.year.duplicated().any():raise ValueError('Duplicate forecast year')
    rows=[]
    for item in forecasts.to_dict('records'):
        year=int(item['year']);issue=pd.Timestamp(item['forecast_at'])
        if issue!=pd.Timestamp(year=year,month=8,day=1):raise ValueError('Unexpected issue date')
        if not all(np.isfinite(item[name]) for name in ['satellite','baseline','climatology']):raise ValueError('Invalid forecast')
        window=prices.loc[issue:pd.Timestamp(year=year,month=11,day=30)]
        if window.isna().any().any():raise ValueError('Missing quote in market window')
        if len(window)<70:raise ValueError('Incomplete market window')
        entry,finish=window.index[0],window.index[-1]
        if (entry-issue).days>4 or (pd.Timestamp(year=year,month=11,day=30)-finish).days>4:raise ValueError('Missing market-window boundary quotes')
        returns=window.iloc[-1]/window.iloc[0]-1;days=(finish-entry).days
        signals={name:-int(np.sign(item[name]-item['climatology'])) for name in ['satellite','baseline']}
        signals.update(incremental_overlay=-int(np.sign(item['satellite']-item['baseline'])),always_long=1,always_short=-1,cash=0)
        for name,direction in signals.items():
            rows.append({'year':year,'forecast_at':issue,'entry':entry,'exit':finish,'days':days,
                         'kie_return':float(returns.KIE),'spy_return':float(returns.SPY),'strategy':name,
                         **event_return(direction,float(returns.KIE),float(returns.SPY),days,cost_multiplier)})
    if not rows:raise ValueError('No trading events')
    return pd.DataFrame(rows)


def score(events:pd.DataFrame)->dict:
    wide=events.pivot(index='year',columns='strategy',values='net_return').sort_index()
    if wide.isna().any().any():raise ValueError('Unequal strategy support')
    values=wide.to_numpy();rng=np.random.default_rng(20260927)
    draws=rng.integers(len(wide),size=(10000,len(wide)))
    sample=values[draws].mean(axis=1);base=wide.columns.get_loc('baseline')
    scores={}
    for i,name in enumerate(wide.columns):
        selected=events.loc[events.strategy==name]
        scores[name]={'n_seasons':len(wide),'mean_net_season_return':float(values[:,i].mean()),
                      'compound_net_event_return':float(np.prod(1+values[:,i])-1),
                      'mean_net_return_ci95':np.quantile(sample[:,i],[.025,.975]).tolist(),
                      'paired_mean_net_advantage_vs_baseline_ci95':np.quantile(sample[:,i]-sample[:,base],[.025,.975]).tolist(),
                      'long_events':int(selected.direction.eq(1).sum()),'short_events':int(selected.direction.eq(-1).sum())}
    sat=scores['satellite']
    return {'strategies':scores,'economic_gate_passed':bool(sat['mean_net_return_ci95'][0]>0 and sat['paired_mean_net_advantage_vs_baseline_ci95'][0]>0),
            'trading_alpha_verified':False,'interpretation':'Exploratory broad-insurance ETF spread test, not isolated catastrophe pricing or risk-adjusted alpha. Returns include both-leg execution and short borrow costs. No trades executed.'}


def run(out:Path=OUT)->dict:
    forecasts_path=ROOT/'results/satellite_validation/hurricane/predictions.csv'
    forecasts=pd.read_csv(forecasts_path)
    prices=pd.concat([load_prices(out/'inputs'/f'{symbol.lower()}_yahoo_chart.json',symbol) for symbol in ['KIE','SPY']],axis=1,join='outer')
    events=make_events(forecasts,prices);summary=score(events)
    doubled=make_events(forecasts,prices,2);summary['double_execution_cost_sensitivity']=score(doubled)
    summary['protocol']=json.loads((out/'protocol.json').read_text())
    summary['source_manifest']=json.loads((out/'source_manifest.json').read_text())
    summary['predictions_sha256']=hashlib.sha256(forecasts_path.read_bytes()).hexdigest()
    prices.to_csv(out/'daily_prices.csv',index_label='date',float_format='%.12g')
    events.to_csv(out/'events.csv',index=False,float_format='%.12g')
    doubled.to_csv(out/'events_double_cost.csv',index=False,float_format='%.12g')
    (out/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    return summary
if __name__=='__main__':
    print(json.dumps(run(),indent=2))
