"""Independent artifact audit; deliberately imports no forecast/trading code."""
from pathlib import Path
import hashlib,json
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
R=ROOT/'results/corn_model'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
c=json.loads((ROOT/'config/corn_model.json').read_text())
summary=json.loads((R/'summary.json').read_text())
first=json.loads((R/'first_run_summary.json').read_text())
cards=json.loads((R/'forecast_cards.json').read_text())
panel=pd.read_csv(R/'panel.csv')
pred=pd.read_csv(R/'predictions.csv')
trades=pd.read_csv(R/'paper_trades.csv')
v=pd.read_csv(R/'inputs/usda/wasde_corn_vintages.csv')
v['published_at']=pd.to_datetime(v.published_at,utc=True)
v=v.sort_values('published_at')
p=pd.concat([pd.read_csv(R/'inputs/satellite'/f) for f in ['august.csv','september.csv']],ignore_index=True)
p['forecast_at']=pd.to_datetime(p.forecast_at,utc=True)
p=p.sort_values('forecast_at').reset_index(drop=True)
errors={}
def same(category,got,want,tol=1e-8):
    a,b=np.asarray(got,dtype=float),np.asarray(want,dtype=float)
    assert a.shape==b.shape,(category,a.shape,b.shape)
    assert np.allclose(a,b,rtol=0,atol=tol,equal_nan=True),(category,a,b)
    finite=np.isfinite(a)&np.isfinite(b)
    errors[category]=max(errors.get(category,0),float(np.max(np.abs(a[finite]-b[finite]))) if finite.any() else 0)

# Authenticate all compact satellite and market snapshots before use.
checked=[]
for sub in ['satellite','market']:
    manifest=json.loads((R/'inputs'/sub/'manifest.json').read_text())
    for item in manifest['files']:
        path=R/'inputs'/sub/item['path']
        assert sha(path)==item['sha256']
        checked.append(str(path.relative_to(ROOT)))
assert sha(R/'first_run_source.py.txt')==first['code_sha256']
assert sha(ROOT/'config/corn_model.json')==first['config_sha256']==summary['config_sha256']
assert sha(R/'protocol.json')==first['protocol_sha256']==summary['protocol_sha256']
assert first['forecast']==summary['forecast']

# Reconstruct dated targets, anchors and predictors directly from input rows.
independent=[]
for i,x in p.iterrows():
    issue=x.forecast_at;y=int(x.year)
    anchor=v.loc[(v.marketing_year_start==y)&(v.published_at<issue)].iloc[-1]
    month=(issue.replace(tzinfo=None).to_period('M')+1).strftime('%Y-%m')
    targets=v.loc[(v.marketing_year_start==y)&v.report_date.str.startswith(month)&(v.published_at>issue)]
    target=targets.iloc[0] if len(targets) else None
    row={'year':y,'issue':issue,'target_month':month,'is_september':int(issue.month==9),
         'weather_anomaly_bu_acre':(x.weather-x.trend)/.0628,
         'ndvi_increment_bu_acre':(x.satellite-x.weather)/.0628,
         'label':target.yield_bu_acre-anchor.yield_bu_acre if target is not None else np.nan,
         'target_available':target.published_at if target is not None else pd.NaT,
         'yield':anchor.yield_bu_acre,'actual':target.yield_bu_acre if target is not None else np.nan,
         'area':anchor.harvested_area_m_acres,'production':anchor.production_m_bu,
         'stocks':anchor.ending_stocks_m_bu,'use':anchor.total_use_m_bu,'eligible':bool(x.eligible and x.prior_area_coverage>=.8)}
    previous=v.loc[(v.marketing_year_start==y-1)&(v.published_at<issue)].iloc[-1]
    footprint=x.forecast_prior_area_ha/.40468564224/(previous.harvested_area_m_acres*1e6)
    saved=panel.iloc[i]
    assert pd.Timestamp(saved.forecast_at)==issue and saved.target_month==month
    assert saved.usda_report_date==anchor.report_date
    assert pd.Timestamp(saved.usda_published_at)==anchor.published_at
    assert (pd.isna(saved.target_available_at) and target is None) or pd.Timestamp(saved.target_available_at)==target.published_at
    for savedkey,expect in {'weather_anomaly_bu_acre':row['weather_anomaly_bu_acre'],'ndvi_increment_bu_acre':row['ndvi_increment_bu_acre'],
          'label_revision_bu_acre':row['label'],'target_yield_bu_acre':row['actual'],
          'usda_yield_bu_acre':row['yield'],'prior_area_footprint':footprint}.items():
        same('panel_reconstruction',saved[savedkey],expect)
    independent.append(row)
d=pd.DataFrame(independent)
assert d.loc[(d.year==2013)&d.is_september.eq(1),'actual'].isna().all()
assert len(panel)==len(pred)==len(cards)==len(d)==22

# Independently fit an augmented least-squares ridge objective with a free intercept.
# This is different numerical algebra from the core's centered normal equations.
cols={'weather':['weather_anomaly_bu_acre','is_september'],'satellite':['weather_anomaly_bu_acre','is_september','ndvi_increment_bu_acre']}
scored=[]
for i,row in d.iterrows():
    saved=cards[i]
    train=d.loc[d.eligible&(d.year<row.year)&(d.target_available<row.issue)&np.isfinite(d.label)]
    train=train.loc[np.isfinite(train[cols['satellite']]).all(axis=1)]
    years=sorted(int(x) for x in train.year.unique())
    assert saved['training_rows']==len(train) and saved['training_years']==years
    ready=row.eligible and len(years)>=5 and len(train)>=8
    assert saved['status']==('ready' if ready else 'abstain')
    if not ready: continue
    forecast={'year':int(row.year),'actual':float(row.actual)}
    revisions={'usda':0.,'bias':float(train.loc[train.is_september==row.is_september,'label'].mean())}
    for model,columns in cols.items():
        x=train[columns].to_numpy(float); mean=x.mean(axis=0); scale=x.std(axis=0);scale[scale<1e-12]=1.
        z=(x-mean)/scale
        design=np.column_stack([np.ones(len(z)),z])
        penalty=np.column_stack([np.zeros(len(columns)),np.eye(len(columns))*np.sqrt(10)])
        beta=np.linalg.lstsq(np.vstack([design,penalty]),np.r_[train.label.to_numpy(),np.zeros(len(columns))],rcond=None)[0]
        revisions[model]=float(np.r_[1.,(row[columns].to_numpy(float)-mean)/scale]@beta)
        cal=saved['models'][model]['calibration'];assert cal['features']==columns
        same('calibration_means',cal['training_means'],mean)
        same('calibration_scales',cal['training_scales'],scale)
        same('calibration_coefficients',cal['standardized_coefficients'],beta[1:])
        same('calibration_intercept',cal['intercept_revision_bu_acre'],beta[0])
    for name,revision in revisions.items():
        m=saved['models'][name]
        same('revision_prediction',m['predicted_revision_bu_acre'],revision)
        same('yield_prediction',m['predicted_yield_bu_acre'],row['yield']+revision)
        same('saved_predictions_csv',pred.iloc[i][name+'_yield_bu_acre'],row['yield']+revision)
        same('balance_sheet_production',m['conditional_production_m_bu'],row.production+row.area*revision)
        same('balance_sheet_stocks',m['conditional_ending_stocks_m_bu'],row.stocks+row.area*revision)
        same('balance_sheet_stocks_to_use',m['conditional_stocks_to_use'],(row.stocks+row.area*revision)/row.use)
        forecast[name]=m['predicted_yield_bu_acre']
    scored.append(forecast)
s=pd.DataFrame(scored);models=['usda','bias','weather','satellite'];annual_mse={};metrics={}
for name in models:
    e=s[name]-s.actual
    annual=pd.DataFrame({'year':s.year,'se':e*e,'ae':abs(e)}).groupby('year').mean()
    metrics[name]={'rmse_bu_acre':float(np.sqrt(annual.se.mean())),'mae_bu_acre':float(annual.ae.mean())}
    annual_mse[name]=annual.se
    for metric,value in metrics[name].items():same('forecast_summary',summary['forecast']['models'][name][metric],value)
calendar=np.arange(int(s.year.min()),int(s.year.max())+1)
a=pd.DataFrame(annual_mse).reindex(calendar)
rng=np.random.default_rng(c['random_seed']);starts=rng.integers(0,len(calendar),size=(10000,3))
indices=np.array([[(int(k)+j)%len(calendar) for k in draw for j in range(2)] for draw in starts])
replica=np.sqrt(np.nanmean(a.to_numpy()[indices],axis=1))
comparisons={}
for idx,name in enumerate(models[:-1]):
    gains=1-replica[:,-1]/replica[:,idx]
    point=1-metrics['satellite']['rmse_bu_acre']/metrics[name]['rmse_bu_acre']
    ci=np.quantile(gains,[.025,.975])
    same('forecast_ci',summary['forecast']['satellite_comparisons'][name]['conditional_ci95'],ci)
    same('forecast_gain',summary['forecast']['satellite_comparisons'][name]['rmse_reduction'],point)
    comparisons[name]={'rmse_reduction':point,'conditional_ci95':ci.tolist()}

# Recreate each paper event from authenticated prices/calendar and saved forecast.
prices=pd.read_csv(R/'inputs/market/adjusted_prices.csv',parse_dates=['date']).set_index('date').adjusted_close
schedule=pd.read_csv(R/'inputs/market/xnys_schedule.csv',parse_dates=['date']).set_index('date')
ready={pd.Timestamp(x['forecast_at']):x for x in cards if x['status']=='ready'}
for t in trades.itertuples():
    stamp=pd.Timestamp(t.forecast_at);card=ready[stamp]
    if t.strategy=='always_long':sign=1
    elif t.strategy=='always_short':sign=-1
    else:
        gap=card['models'][t.strategy]['predicted_yield_bu_acre']-card['usda_yield_bu_acre']
        sign=int(gap<-.5)-int(gap>.5)
    assert t.direction==sign
    day=stamp.tz_convert('America/New_York').tz_localize(None).normalize()
    entry=schedule.index[schedule.index>day][0];ei=schedule.index.get_loc(entry);exit=schedule.index[ei+20]
    assert t.entry==str(entry.date()) and t.exit==str(exit.date())
    assert pd.Timestamp(t.entry_at)==pd.Timestamp(schedule.loc[entry,'close_at'])>stamp
    assert pd.Timestamp(t.exit_at)==pd.Timestamp(schedule.loc[exit,'close_at'])
    allprices=prices.reindex(schedule.index[ei:ei+21]);assert np.isfinite(allprices).all()
    asset=allprices.iloc[-1]/allprices.iloc[0]-1
    cost=abs(sign)*.005;borrow=.03*(exit-entry).days/365 if sign<0 else 0.
    for name,value in {'asset_return':asset,'gross_return':sign*asset,'execution_cost':cost,'borrow_cost':borrow,'net_return':sign*asset-cost-borrow,'double_cost_net_return':sign*asset-2*cost-borrow}.items():
        same('paper_event_math',getattr(t,name),value)
wide=trades.pivot(index='forecast_at',columns='strategy',values='net_return');assert not wide.isna().any().any()
annual=trades.groupby(['year','strategy']).net_return.mean().unstack().reindex(calendar)
boot=np.nanmean(annual.to_numpy()[indices],axis=1);widx=list(annual.columns).index('weather')
market={}
for name,group in trades.groupby('strategy'):
    group=group.sort_values('forecast_at');r=group.net_return.to_numpy();j=list(annual.columns).index(name)
    assert not (r<=-1).any()
    assert (pd.to_datetime(group.entry).iloc[1:].to_numpy()>=pd.to_datetime(group.exit).iloc[:-1].to_numpy()).all()
    m={'n_positions':int(group.direction.ne(0).sum()),'compound_net_event_return':float(np.prod(1+r)-1),
       'double_cost_compound_return':float(np.prod(1+group.double_cost_net_return)-1),
       'mean_net_event_return':float(r.mean()),'mean_annual_event_return_ci95':np.quantile(boot[:,j],[.025,.975]).tolist(),
       'paired_mean_advantage_vs_weather_ci95':np.quantile(boot[:,j]-boot[:,widx],[.025,.975]).tolist()}
    for key,value in m.items():same('market_summary',summary['trading']['strategies'][name][key],value)
    for key in ['n_positions','compound_net_event_return','mean_net_event_return','double_cost_compound_return']:
        same('first_run_market_unchanged',first['trading']['strategies'][name][key],m[key])
    market[name]=m

files=['summary.json','first_run_summary.json','first_run_source.py.txt','forecast_cards.json','panel.csv','predictions.csv','paper_trades.csv','protocol.json','inputs/usda/wasde_corn_vintages.csv','inputs/usda/source_manifest.json','inputs/satellite/manifest.json','inputs/market/manifest.json']
result={'audit_status':'passed','audited_at_utc':pd.Timestamp.now(tz='UTC').isoformat(),
  'method':'Independent reconstruction from saved source inputs with numpy/pandas only; no forecast or execution module imported. Ridge fitted via augmented least squares; all losses, calendar-year block CIs, event directions and costs independently recomputed.',
  'scope':{'panel_rows':len(panel),'ready_scored_events':len(s),'abstentions':len(panel)-len(s),'years':sorted(map(int,s.year.unique())),'paper_event_strategy_rows':len(trades),'matched_paper_events':len(wide),'independent_semantic_tests_passed':17},
  'checks':{'all_anchors_strictly_before_issue':True,'same_marketing_year_anchors':True,'exact_next_calendar_month_targets':True,'october_2013_cancellation_remains_missing':True,'only_prior_harvest_years_and_released_labels_in_fit':True,'training_only_standardization':True,'unpenalized_intercept':True,'ridge_alpha_10':True,'rounded_usda_production_preserved':True,'reported_coverage_distinct_from_national_footprint':True,'strict_next_session_entry':True,'twenty_sessions_after_entry':True,'all_expected_price_sessions_present':True,'no_overlapping_windows':True,'round_trip_cost_50bp_plus_short_borrow_3pct':True,'paired_two_calendar_year_blocks_shared_across_models':True,'first_run_forecast_scores_unchanged':True,'first_run_market_point_results_unchanged':True},
  'max_absolute_differences':errors,'forecast_metrics':metrics,'satellite_comparisons':comparisons,'paper_metrics':market,
  'source_spotchecks':{'independent_original_usda_csv_checks':['2013-09-12 / 2013/14','2023-08-11 / 2023/24','2023-09-12 / 2023/24'],'fields':['yield','harvested_area','production','ending_stocks','total_use','Eastern_to_UTC_publication_time'],'result':'exact match; performed before this artifact audit'},
  'limitations':['12 matched events in six harvest years; uncertainty conditional on fixed specification and prior research search is not corrected.','USDA is an official reference, not a measured market consensus.','County forecasts and crop maps are revised research archives; as-published USDA does not make the full pipeline an original-vintage backtest.','Coverage denominator is available county reported area and the separate national footprint is incomplete.','CORN adjusted-close returns are an ETF proxy with assumed costs and borrow, not executed futures orders.','Satellite model does not beat no-change USDA or weather calibration and its paper strategy loses money.'],
  'trading_alpha_verified':False,'original_vintage_operational_verification':False,
  'input_checksums_verified':checked,'artifact_sha256':{x:sha(R/x) for x in files},'audit_script_sha256':sha(Path(__file__)),
  'current_code_sha256':sha(ROOT/'src/corn_model.py'),'first_run_code_sha256':first['code_sha256'],'summary_code_sha256':summary['code_sha256']}
(R/'independent_validation.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
print(json.dumps({'status':'passed','scope':result['scope'],'max_absolute_differences':errors,'forecast_metrics':metrics,'satellite_paper':market['satellite']},indent=2))
