#!/usr/bin/env python3
"""Frozen partial-ridge correction and Zambia/Zimbabwe geographicreplication."""
from pathlib import Path
import argparse,json
import numpy as np
import pandas as pd
try:
    from . import satellite_south_africa_maize as core
except ImportError:
    import satellite_south_africa_maize as core

DEFAULT=core.ROOT/'results/satellite_validation/southern_africa_maize_replication'
COUNTRIES={'ZA':'South Africa','ZM':'Zambia','ZW':'Zimbabwe'}
CONTROLS=[x for x in core.BASE if x!='harvest_year']


def load_labels(path,country):
    raw=pd.read_csv(path);raw=raw[raw.Area.eq(country)&raw.Item.isin(['Maize (corn)','Maize'])]
    if raw.empty or raw.duplicated(['Year','Element']).any():raise ValueError('MissingcountryorduplicateFAOlabels')
    for element,unit in [('Production','t'),('Area harvested','ha')]:
        if not raw.loc[raw.Element.eq(element),'Unit'].eq(unit).all():raise ValueError('UnexpectedFAOunits')
    frame=raw.pivot(index='Year',columns='Element',values='Value').apply(pd.to_numeric,errors='coerce')
    frame=frame.rename(columns={'Production':'production_t','Area harvested':'harvested_ha'})
    if not(np.isfinite(frame[['production_t','harvested_ha']]).all().all() and frame[['production_t','harvested_ha']].gt(0).all().all()):raise ValueError('InvalidFAOproductionorarea')
    frame['actual_t_ha']=frame.production_t/frame.harvested_ha
    direct=raw[raw.Element.eq('Yield')].set_index('Year');factors=direct.Unit.map({'kg/ha':.001,'hg/ha':.0001,'t/ha':1.})
    if factors.isna().any() or not np.allclose(frame.loc[direct.index,'actual_t_ha'],pd.to_numeric(direct.Value)*factors,rtol=.002,atol=.005):raise ValueError('InconsistentpublishedFAOyield')
    frame['production_flag']=raw[raw.Element.eq('Production')].set_index('Year').Flag
    frame.index.name='harvest_year';return frame.reset_index()


def partial_ridge(train,row,columns,alpha=5.):
    """Penalize Z only; nuisance intercept+time U retain an exactlineartrend."""
    time_center=float(train.harvest_year.mean())
    u=np.column_stack([np.ones(len(train)),train.harvest_year.to_numpy(float)-time_center])
    current_u=np.array([1.,float(row.harvest_year)-time_center])
    x=train[columns].to_numpy(float);center=x.mean(axis=0);scale=x.std(axis=0);scale=np.where(scale>0,scale,1.)
    z=(x-center)/scale;current_z=(row[columns].to_numpy(float)-center)/scale;y=train.actual_value.to_numpy(float)
    y_residual=y-u@np.linalg.lstsq(u,y,rcond=None)[0]
    z_residual=z-u@np.linalg.lstsq(u,z,rcond=None)[0]
    beta=np.linalg.solve(z_residual.T@z_residual+alpha*np.eye(len(columns)),z_residual.T@y_residual)
    gamma=np.linalg.lstsq(u,y-z@beta,rcond=None)[0]
    return float(current_u@gamma+current_z@beta)


def predict(panel,start=2000,end=2024):
    panel=panel.sort_values('harvest_year').copy()
    if panel.harvest_year.duplicated().any():raise ValueError('Duplicatetargetyear')
    panel['usable']=np.isfinite(panel[core.BASE+core.SAT]).all(axis=1)
    panel['usable']&=(panel.sst_available<panel.forecast_at)&(panel.ground_available<panel.forecast_at)&(panel.prior_label_available<panel.forecast_at)&(panel.forecast_at<panel.target_start)
    rows=[];normalization_scale=None
    for _,row in panel[panel.harvest_year.between(start,end)].iterrows():
        train=panel[panel.usable&np.isfinite(panel.actual_value)&(panel.harvest_year<row.harvest_year)&(panel.label_available<row.forecast_at)]
        record=row.to_dict();record.update(eligible=False,n_train=len(train),reason='',normalization_scale=np.nan,**{name:np.nan for name in core.MODELS})
        if not row.usable:record['reason']='Missingorunavailablefeature'
        elif len(train)<15:record['reason']='Fewerthan15eligiblepriorlabels'
        else:
            if normalization_scale is None:
                normalization_scale=float(train.head(15).actual_value.mean())
                if not np.isfinite(normalization_scale) or normalization_scale<=0:raise ValueError('Invalidfixedinitialtrainingscale')
            fit=np.polyfit(train.harvest_year-1980,train.actual_value,1)
            record.update(eligible=True,normalization_scale=normalization_scale,
                baseline=partial_ridge(train,row,CONTROLS),satellite=partial_ridge(train,row,CONTROLS+core.SAT),
                mean=float(train.actual_value.mean()),recent_mean=float(train.tail(10).actual_value.mean()),
                persistence=float(row.prior_released_outcome),trend=float(np.polyval(fit,row.harvest_year-1980)),
                latest_training_harvest=int(train.harvest_year.max()),latest_training_label_available=train.label_available.max())
            prior='prior_september_nino34_sst_c';diagnostic=train[np.isfinite(train[prior])]
            if len(diagnostic)>=15 and np.isfinite(row[prior]):
                record.update(placebo=partial_ridge(diagnostic,row,CONTROLS+[prior]),
                    satellite_placebo_support=partial_ridge(diagnostic,row,CONTROLS+core.SAT),placebo_n_train=len(diagnostic))
        rows.append(record)
    return pd.DataFrame(rows)


def normalized_metrics(frame,model):
    values=[]
    for _,group in frame.groupby('country'):
        if group.normalization_scale.nunique()!=1:raise ValueError('Normalizationmustremainfixedwithincohort')
        errors=(group[model]-group.actual_value)/group.normalization_scale
        values.append([np.mean(errors**2),np.mean(abs(errors))])
    return dict(rmse=float(np.sqrt(np.mean(np.asarray(values)[:,0]))),mae=float(np.mean(np.asarray(values)[:,1])))


def cluster_ci(frame,model,draws=10000):
    if frame.duplicated(['country','harvest_year']).any():raise ValueError('Duplicatecountryyear')
    calendar=np.arange(int(frame.harvest_year.min()),int(frame.harvest_year.max())+1);countries=sorted(frame.country.unique())
    estimates=[];rng=np.random.default_rng(20260927);n=len(calendar)
    starts=rng.integers(0,n,size=(draws,(n+4)//5));ids=((starts[:,:,None]+np.arange(5))%n).reshape(draws,-1)[:,:n]
    for name in [model,'satellite']:
        x=frame.assign(error=((frame[name]-frame.actual_value)/frame.normalization_scale)**2).pivot(index='harvest_year',columns='country',values='error').reindex(index=calendar,columns=countries).to_numpy()[ids]
        count=np.isfinite(x).sum(axis=1);sums=np.nansum(x,axis=1)
        mse=np.divide(sums,count,out=np.full(sums.shape,np.nan),where=count>0)
        estimates.append(np.sqrt(np.mean(mse,axis=1)))
    with np.errstate(divide='ignore',invalid='ignore'):gain=1-estimates[1]/estimates[0]
    gain=gain[np.isfinite(gain)]
    return list(map(float,np.quantile(gain,[.025,.975]))) if len(gain) else [None,None]


def pooled_score(frame):
    held=frame[frame.eligible].copy();held=held[np.isfinite(held[['actual_value','normalization_scale']+core.MODELS]).all(axis=1)]
    if held.empty:return dict(confirmation_gate_passed=False,status='insufficient_data')
    counts=held.groupby('country').harvest_year.nunique().to_dict()
    country_results={c:core.score(g) for c,g in held.groupby('country')}
    individual_positive={c: r['comparisons'][r['strongest_baseline']]['rmse_reduction']>0 for c,r in country_results.items()}
    metrics={m:normalized_metrics(held,m) for m in core.MODELS};comparisons={}
    for name in core.BASELINES:
        a,b=metrics['satellite'],metrics[name]
        comparisons[name]=dict(rmse_reduction=1-a['rmse']/b['rmse'],mae_reduction=1-a['mae']/b['mae'],rmse_reduction_ci95=cluster_ci(held,name))
    strongest=min(core.BASELINES,key=lambda m:metrics[m]['rmse'])
    gate=bool(set(counts)=={'ZM','ZW'} and min(counts.values())>=20 and all(individual_positive.values())
        and comparisons[strongest]['rmse_reduction']>=.05 and all(c['mae_reduction']>0 and c['rmse_reduction_ci95'][0] is not None and c['rmse_reduction_ci95'][0]>0 for c in comparisons.values()))
    return dict(confirmation_gate_passed=gate,n_per_country=counts,n_calendar_years=int(held.harvest_year.nunique()),
        country_improves_over_own_strongest=individual_positive,normalization_scales=held.groupby('country').normalization_scale.first().to_dict(),
        normalized_metrics=metrics,strongest_pooled_baseline=strongest,comparisons=comparisons,
        description='Equal-country normalizederrors; sharedfive-calendar-yearblocks; ZambiaandZimbabwe jointlyrequired.')


def load_ground(out,code):
    if code=='ZA':return pd.read_csv(core.DEFAULT/'inputs/ground_augsep.csv')
    return pd.read_csv(out/'ground'/f'{code}_ground_augsep.csv')


def verify_inherited_inputs():
    manifest=json.loads((core.DEFAULT/'source_manifest.json').read_text())
    for path in ['inputs/september_sst.csv','inputs/ground_augsep.csv']:
        match=[x for x in manifest if x['path']==path]
        if len(match)!=1 or core.sha(core.DEFAULT/path)!=match[0]['sha256']:
            raise ValueError('Changedinheritedfrozeninput')


def verify_confirmation_ground(out):
    ground=out/'ground'
    manifest=json.loads((ground/'source_manifest.json').read_text())
    for entry in manifest:
        if core.sha(ground/entry['path'])!=entry['sha256']:
            raise ValueError('Changed confirmation ground input: '+entry['path'])
    geography=json.loads((ground/'geography_freeze.json').read_text())
    if set(geography['countries'])!={'ZM','ZW'}:raise ValueError('Unexpected confirmation geography')
    for entry in geography['countries'].values():
        if core.sha(ground/entry['mask_file'])!=entry['mask_sha256']:
            raise ValueError('Changed frozen national mask')


def development(out=DEFAULT):
    verify_inherited_inputs()
    labels=load_labels(out/'inputs/faostat_maize.csv','South Africa')
    ground=load_ground(out,'ZA');sst=pd.read_csv(core.DEFAULT/'inputs/september_sst.csv');result={}
    for target,key in [('yield','primary_yield'),('production','secondary_production')]:
        for regime in ['fao_lag','completed_harvest_stress']:
            panel=core.build_panel(labels,ground,sst,regime,target)
            pred=predict(panel);pred['country']='ZA';pred['outcome']=key
            panel.to_csv(out/f'ZA_{key}_{regime}_panel.csv',index=False)
            pred.to_csv(out/f'ZA_{key}_{regime}_predictions.csv',index=False)
            result.setdefault(key,{})[regime]=core.score(pred)
    result['claims']='SouthAfricadevelopmentdiagnosticonalreadyseenoutcomes;notindependentconfirmation.'
    result['protocol_sha256']=core.sha(out/'protocol.json');result['code_sha256']=core.sha(Path(__file__))
    (out/'development_summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2));return result

def run(out=DEFAULT):
    verify_inherited_inputs()
    verify_confirmation_ground(out)
    # FAOlabelssnapshottedonlyafterreplicationprotocolwasfrozen.
    manifest=json.loads((out/'inputs/labels_manifest.json').read_text())
    if core.sha(out/'inputs/faostat_maize.csv')!=manifest['filtered_sha256']:raise ValueError('ChangedFAOlabels')
    sst=pd.read_csv(core.DEFAULT/'inputs/september_sst.csv');result={};all_predictions=[]
    for code,country in COUNTRIES.items():
        labels=load_labels(out/'inputs/faostat_maize.csv',country);labels.to_csv(out/f'{code}_labels.csv',index=False)
        ground=load_ground(out,code)
        for target,key in [('yield','primary_yield'),('production','secondary_production')]:
            for regime in ['fao_lag','completed_harvest_stress']:
                panel=core.build_panel(labels,ground,sst,regime,target)
                panel.to_csv(out/f'{code}_{key}_{regime}_panel.csv',index=False)
                pred=predict(panel);pred['country']=code;pred['outcome']=key;pred.to_csv(out/f'{code}_{key}_{regime}_predictions.csv',index=False)
                all_predictions.append(pred)
                result.setdefault(key,{}).setdefault(regime,{'countries':{}})['countries'][code]=core.score(pred)
    combined=pd.concat(all_predictions,ignore_index=True);combined.to_csv(out/'all_predictions.csv',index=False)
    for key in ['primary_yield','secondary_production']:
        for regime in ['fao_lag','completed_harvest_stress']:
            subset=combined[combined.outcome.eq(key)&combined.release_regime.eq(regime)&combined.country.isin(['ZM','ZW'])]
            result[key][regime]['confirmation']=pooled_score(subset)
        result[key]['robust_confirmation_gate_passed']=all(result[key][r]['confirmation']['confirmation_gate_passed'] for r in ['fao_lag','completed_harvest_stress'])
    result.update(protocol_sha256=core.sha(out/'protocol.json'),code_sha256=core.sha(Path(__file__)),
                  core_code_sha256=core.sha(Path(core.__file__)),original_vintage_operational_verification=False,
                  labels_manifest_sha256=core.sha(out/'inputs/labels_manifest.json'),
                  ground_manifest_sha256=core.sha(out/'ground/source_manifest.json'),
                  geography_freeze_sha256=core.sha(out/'ground/geography_freeze.json'),
                  trading_alpha_verified=False,cross_candidate_multiple_testing_adjusted=False,
                  claims='SouthAfricaisdevelopmentafterobservingoldmodelbias. OnlyfixedZambiaandZimbabwejointreplicationcanqualify;primaryyieldandprespecifiedproductionsecondaryseparate,current-vintageonly.')
    (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2));return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output-dir',type=Path,default=DEFAULT)
    parser.add_argument('--development-only',action='store_true')
    args=parser.parse_args()
    development(args.output_dir) if args.development_only else run(args.output_dir)
