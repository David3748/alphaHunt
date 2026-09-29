#!/usr/bin/env python3
"""Frozen SeptemberOISST -> next-calendar-year SouthAfrican maizeyield.

Current-vintage scientific forecast; earlyplantingOct20 issue, nofutureweather.
"""
from pathlib import Path
import argparse
import hashlib
import json
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
DEFAULT=ROOT/'results/satellite_validation/south_africa_maize'
BASE=['harvest_year','prior_released_outcome','august_ground_mm','september_ground_mm']
SAT=['september_nino34_sst_c']
MODELS=['baseline','satellite','mean','recent_mean','persistence','trend']
BASELINES=[m for m in MODELS if m!='satellite']


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_labels(path):
    raw=pd.read_csv(path)
    if not raw.Area.eq('South Africa').all() or not raw.Item.isin(['Maize (corn)','Maize']).all():
        raise ValueError('Unexpected FAOSTAT country or crop')
    if raw.duplicated(['Year','Element']).any():raise ValueError('Duplicate FAOyear/element')
    for variable,unit in [('Production','t'),('Area harvested','ha')]:
        if not raw.loc[raw.Element.eq(variable),'Unit'].eq(unit).all():raise ValueError('UnexpectedFAOproduction/areaunits')
    frame=raw.pivot(index='Year',columns='Element',values='Value').apply(pd.to_numeric,errors='coerce')
    frame=frame.rename(columns={'Production':'production_t','Area harvested':'harvested_ha'})
    if not (np.isfinite(frame[['production_t','harvested_ha']]).all().all() and frame.production_t.gt(0).all() and frame.harvested_ha.gt(0).all()):
        raise ValueError('Missing or nonpositive maizeproduction/area')
    frame['actual_t_ha']=frame.production_t/frame.harvested_ha
    # Validate independent publishedyield units against the ratio, if supplied.
    direct=raw[raw.Element.eq('Yield')].set_index('Year')
    if len(direct):
        factors=direct.Unit.map({'t/ha':1.,'kg/ha':.001,'100 mg/ha':.0000001,'hg/ha':.0001})
        if factors.isna().any():raise ValueError('UnknownFAOyieldunit')
        converted=pd.to_numeric(direct.Value,errors='raise')*factors
        if not np.allclose(frame.loc[direct.index,'actual_t_ha'],converted,rtol=.002,atol=.005):
            raise ValueError('FAOyield disagreeswithproduction/area')
    frame.index.name='harvest_year'
    flags=raw[raw.Element.eq('Production')].set_index('Year').Flag
    frame['production_flag']=flags
    return frame.reset_index()


def build_panel(labels,ground,sst,release_regime='fao_lag', target='yield'):
    if release_regime not in ['fao_lag','completed_harvest_stress']:raise ValueError('Unknownreleaseassumption')
    labels=labels.copy().sort_values('harvest_year')
    if target not in ['yield','production']:raise ValueError('Unknowntarget')
    labels['actual_value']=labels.actual_t_ha if target=='yield' else labels.production_t/1e6
    if labels.harvest_year.duplicated().any():raise ValueError('Duplicateharvestyear')
    if release_regime=='fao_lag':
        labels['label_available']=pd.to_datetime((labels.harvest_year+1).astype(str)+'-12-31 23:59:00')
    else:
        labels['label_available']=pd.to_datetime(labels.harvest_year.astype(str)+'-09-30')
    ground=ground.copy();sst=sst.copy();sst['date']=pd.to_datetime(sst.date)
    if ground.duplicated(['year','month']).any() or sst.date.duplicated().any():raise ValueError('Duplicatesourceperiod')
    if not ground.month.isin([8,9]).all() or not sst.date.dt.month.eq(9).all():raise ValueError('Unexpectedfeaturemonths')
    expected=ground.month.map({8:31,9:30})
    if not ground.n_days.eq(expected).all():raise ValueError('Incompletegroundcalendar')
    ground=ground.set_index(['year','month']);sst=sst.set_index(sst.date.dt.year)
    indexed=labels.set_index('harvest_year');rows=[]
    for harvest in range(1983,min(2024,int(labels.harvest_year.max()))+1):
        issue=pd.Timestamp(harvest-1,10,20,12)
        prior=labels[(labels.harvest_year<harvest)&(labels.label_available<issue)&np.isfinite(labels.actual_value)]
        year=harvest-1
        rain={m:ground.precip_mm.get((year,m),np.nan) for m in [8,9]}
        for m in [8,9]:
            if (year,m) in ground.index and not bool(ground.loc[(year,m),'eligible']):rain[m]=np.nan
        row=dict(harvest_year=harvest,forecast_at=issue,target_start=pd.Timestamp(harvest,5,1),target_end=pd.Timestamp(harvest,8,31,23,59),
                 label_available=indexed.label_available.get(harvest,pd.NaT),actual_value=indexed.actual_value.get(harvest,np.nan),
                 production_t=indexed.production_t.get(harvest,np.nan),harvested_ha=indexed.harvested_ha.get(harvest,np.nan),
                 prior_released_outcome=float(prior.actual_value.iloc[-1]) if len(prior) else np.nan,
                 prior_released_harvest=int(prior.harvest_year.iloc[-1]) if len(prior) else np.nan,
                 prior_label_available=prior.label_available.iloc[-1] if len(prior) else pd.NaT,
                 august_ground_mm=rain[8],september_ground_mm=rain[9],
                 ground_source_end=pd.Timestamp(year,9,30,23,59),ground_available=pd.Timestamp(year,9,30,23,59)+pd.Timedelta(days=10),
                 sst_source_end=pd.Timestamp(year,9,30),sst_available=pd.Timestamp(year,9,30)+pd.Timedelta(days=20),
                 september_nino34_sst_c=sst.september_nino34_sst_c.get(year,np.nan),
                 prior_september_nino34_sst_c=sst.september_nino34_sst_c.get(year-1,np.nan),release_regime=release_regime)
        rows.append(row)
    return pd.DataFrame(rows)


def ridge(train,row,columns,alpha=5.):
    x=train[columns].to_numpy(float);y=train.actual_value.to_numpy(float)
    center=x.mean(axis=0);scale=x.std(axis=0);scale=np.where(scale>0,scale,1.)
    z=(x-center)/scale
    beta=np.linalg.solve(z.T@z+alpha*np.eye(len(columns)),z.T@(y-y.mean()))
    return float(y.mean()+((row[columns].to_numpy(float)-center)/scale)@beta)


def predict(panel,start=2000,end=2024):
    panel=panel.sort_values('harvest_year').copy()
    if panel.harvest_year.duplicated().any():raise ValueError('Duplicateharvestforecastyear')
    panel['usable']=np.isfinite(panel[BASE+SAT]).all(axis=1)
    timing=(panel.sst_available<panel.forecast_at)&(panel.ground_available<panel.forecast_at)&(panel.prior_label_available<panel.forecast_at)&(panel.forecast_at<panel.target_start)
    panel['usable']&=timing
    rows=[]
    for _,row in panel[panel.harvest_year.between(start,end)].iterrows():
        train=panel[panel.usable&np.isfinite(panel.actual_value)&(panel.harvest_year<row.harvest_year)&(panel.label_available<row.forecast_at)]
        record=row.to_dict();record.update(eligible=False,n_train=len(train),reason='')
        if not row.usable:record['reason']='Incomplete or unavailableinput'
        elif len(train)<15:record['reason']='Fewerthan15 prior eligible releasedlabels'
        else:
            record.update(eligible=True,baseline=ridge(train,row,BASE),satellite=ridge(train,row,BASE+SAT),
                          mean=float(train.actual_value.mean()),recent_mean=float(train.tail(10).actual_value.mean()),
                          persistence=float(row.prior_released_outcome),latest_training_harvest=int(train.harvest_year.max()),
                          latest_training_label_available=train.label_available.max())
            beta=np.polyfit(train.harvest_year-1980,train.actual_value,1)
            record['trend']=float(np.polyval(beta,row.harvest_year-1980))
            # Prespecified stale-SST diagnostic on its separate common support.
            previous='prior_september_nino34_sst_c'
            diagnostic=train[np.isfinite(train[previous])]
            if len(diagnostic)>=15 and np.isfinite(row[previous]):
                record.update(placebo=ridge(diagnostic,row,BASE+[previous]),
                              satellite_placebo_support=ridge(diagnostic,row,BASE+SAT),placebo_n_train=len(diagnostic))
        rows.append(record)
    return pd.DataFrame(rows)


def metrics(y,p):
    e=np.asarray(y)-np.asarray(p)
    return dict(rmse=float(np.sqrt(np.mean(e**2))),mae=float(np.mean(abs(e))))


def paired_ci(years,y,base,sat,draws=10000):
    data=pd.DataFrame(dict(year=years,base_error=np.asarray(y)-np.asarray(base),sat_error=np.asarray(y)-np.asarray(sat))).set_index('year')
    calendar=data.reindex(range(int(data.index.min()),int(data.index.max())+1)).to_numpy()
    n=len(calendar);rng=np.random.default_rng(20260927);starts=rng.integers(0,n,size=(draws,(n+4)//5))
    ids=((starts[:,:,None]+np.arange(5))%n).reshape(draws,-1)[:,:n]
    errors=calendar[ids];count=np.isfinite(errors).sum(axis=1);squared=np.nansum(errors**2,axis=1)
    mse=np.divide(squared,count,out=np.full(squared.shape,np.nan),where=count>0)
    with np.errstate(divide='ignore',invalid='ignore'):gains=1-np.sqrt(mse[:,1]/mse[:,0])
    valid=gains[np.isfinite(gains)]
    return list(map(float,np.quantile(valid,[.025,.975]))) if len(valid) else [None,None]


def score(predictions):
    held=predictions[predictions.eligible].copy()
    held=held[np.isfinite(held[['actual_value']+MODELS]).all(axis=1)]
    if held.empty:return dict(forecast_gate_passed=False,n_test_years=0)
    scores={m:metrics(held.actual_value,held[m]) for m in MODELS}
    comparisons={}
    for m in BASELINES:
        a,b=scores['satellite'],scores[m]
        comparisons[m]=dict(rmse_reduction=1-a['rmse']/b['rmse'],mae_reduction=1-a['mae']/b['mae'],
                           rmse_reduction_ci95=paired_ci(held.harvest_year,held.actual_value,held[m],held.satellite),
                           annual_wins=int(((held.actual_value-held.satellite).abs()<(held.actual_value-held[m]).abs()).sum()))
    strongest=min(BASELINES,key=lambda m:scores[m]['rmse'])
    gate=bool(len(held)>=20 and comparisons[strongest]['rmse_reduction']>=.05 and all(c['mae_reduction']>0 and c['rmse_reduction_ci95'][0] is not None and c['rmse_reduction_ci95'][0]>0 for c in comparisons.values()))
    result=dict(n_test_years=len(held),test_harvest_years=held.harvest_year.astype(int).tolist(),metrics=scores,
                strongest_baseline=strongest,comparisons=comparisons,forecast_gate_passed=gate,
                abstentions=predictions.loc[~predictions.eligible,['harvest_year','reason']].to_dict('records'))
    if 'placebo' in held:
        p=held.dropna(subset=['placebo','satellite_placebo_support'])
        if len(p):
            a=metrics(p.actual_value,p.satellite_placebo_support);b=metrics(p.actual_value,p.placebo)
            result['placebo_diagnostic']=dict(n=len(p),matched_satellite=a,placebo=b,
                rmse_reduction=1-a['rmse']/b['rmse'],rmse_reduction_ci95=paired_ci(p.harvest_year,p.actual_value,p.placebo,p.satellite_placebo_support))
    return result


def run(out=DEFAULT):
    sources=json.loads((out/'source_manifest.json').read_text())
    for item in sources:
        if sha(out/item['path'])!=item['sha256']:raise ValueError('Sourcehashmismatch')
    labels=load_labels(out/'inputs/faostat_south_africa_maize.csv');labels.to_csv(out/'labels.csv',index=False)
    ground=pd.read_csv(out/'inputs/ground_augsep.csv');sst=pd.read_csv(out/'inputs/september_sst.csv')
    result={}
    for target,key in [('yield','primary_yield'),('production','secondary_production')]:
        target_result={'units':'tonnes per harvested hectare' if target=='yield' else 'million tonnes'}
        for regime in ['fao_lag','completed_harvest_stress']:
            panel=build_panel(labels,ground,sst,regime,target);panel.to_csv(out/f'{key}_{regime}_panel.csv',index=False)
            pred=predict(panel);pred.to_csv(out/f'{key}_{regime}_predictions.csv',index=False);target_result[regime]=score(pred)
        target_result['robust_forecast_gate_passed']=all(target_result[x]['forecast_gate_passed'] for x in ['fao_lag','completed_harvest_stress'])
        result[key]=target_result
    result.update(robust_primary_forecast_gate_passed=result['primary_yield']['robust_forecast_gate_passed'],
                   prespecified_secondary_forecast_gate_passed=result['secondary_production']['robust_forecast_gate_passed'],
                   protocol_sha256=sha(out/'protocol.json'),protocol_addendum_sha256=sha(out/'protocol_addendum.json'),
                   secondary_protocol_sha256=sha(out/'secondary_protocol.json'),
                   code_sha256=sha(Path(__file__)),source_manifest_sha256=sha(out/'source_manifest.json'),
                   original_vintage_operational_verification=False,satellite_only_incremental_attribution=False,
                   official_forecast_superiority_tested=False,trading_alpha_verified=False,cross_candidate_multiple_testing_adjusted=False,
                   claims='Current-vintage, chronologicallyheldout earlyplantingforecast of next-calendarharvestyield andprespecifiedsecondaryproduction; optimisticcompletedharvestinformationstress retained; nooriginal-vintage ortradedalphaclaim.')
    (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2));return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output-dir',type=Path,default=DEFAULT)
    run(parser.parse_args().output_dir)
