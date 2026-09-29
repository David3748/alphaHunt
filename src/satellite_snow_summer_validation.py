#!/usr/bin/env python3
"""Exploratory frozen May snow -> July-September natural-runoff study.

Continuation after the separate failed March study. Offline default uses the
same raw daily MODIS observation/QA extraction, never future seasonal fill.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import numpy as np
import pandas as pd
import satellite_snow_daily_validation as daily

ROOT=Path(__file__).resolve().parents[1]
DEFAULT=ROOT/'results/satellite_validation/snow_summer'
PRIOR=ROOT/'results/satellite_validation/snow_daily'
ALPHA=5.0
YEARS=range(2000,2025)


def rebuild_ground(out=DEFAULT):
    manifest=out/'ground/source_manifest.json'
    if manifest.exists():daily.verify_named_sources(ROOT,json.loads(manifest.read_text()))
    path=PRIOR/'ground/rebuild.py'
    spec=importlib.util.spec_from_file_location('frozen_snow_ground',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    rain=module.read_cdec(PRIOR/'ground/five_station_monthly.csv','5SI',2,'M')
    snow=module.read_cdec(PRIOR/'ground/huntington_swe.csv','HNT',3,'D')
    rows=[]
    for year in YEARS:
        season=rain.reindex(pd.date_range(f'{year-1}-10-01',f'{year}-05-01',freq='MS'))
        values=season.value_mm.where(season.flag.isin(['','r']))
        late=snow.loc[f'{year}-05-29':f'{year}-05-31']
        late=late.loc[late.value_mm.notna() & late.flag.eq('')]
        rows.append(dict(year=year,winter_precip_mm=values.sum() if values.count()==8 else np.nan,
                         precipitation_months=int(values.count()),precipitation_revised_months=int(season.flag.eq('r').sum()),
                         ground_swe_mm=float(late.value_mm.iloc[-1]) if len(late) else np.nan,
                         ground_swe_date=late.index[-1] if len(late) else pd.NaT,
                         assumed_forecast_at=f'{year}-06-15',original_release_verified=False))
    frame=pd.DataFrame(rows);frame.to_csv(out/'ground/summer_ground.csv',index=False)
    manifest={str(p.relative_to(ROOT)):daily.sha256(p) for p in [path,PRIOR/'ground/five_station_monthly.csv',PRIOR/'ground/huntington_swe.csv',out/'ground/basin_full.geojson']}
    (out/'ground/source_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return frame


def load_panel(out=DEFAULT):
    source=json.loads((out/'source_manifest.json').read_text())
    daily.verify_named_sources(out,{'sbf_monthly_fnf.csv':source['cdec_sha256']})
    snow=pd.read_csv(out/'annual_snow.csv')
    ground=pd.read_csv(out/'ground/summer_ground.csv')
    fnf=daily.parse_fnf(out/'sbf_monthly_fnf.csv')
    records=[]
    for year in YEARS:
        records.append(dict(year=year,
                            runoff_af=daily.complete_sum(fnf,pd.date_range(f'{year}-07-01',periods=3,freq='MS')),
                            winter_flow_af=daily.complete_sum(fnf,pd.date_range(f'{year-1}-10-01',periods=7,freq='MS')),
                            winter_flow_may_af=daily.complete_sum(fnf,pd.date_range(f'{year-1}-10-01',periods=8,freq='MS')),
                            prior_runoff_af=daily.complete_sum(fnf,pd.date_range(f'{year-1}-07-01',periods=3,freq='MS'))))
    return pd.DataFrame(records).merge(snow,on='year',validate='one_to_one').merge(ground,on='year',validate='one_to_one')


def ridge_predict(train,row,columns,alpha=ALPHA):
    x=train[columns].to_numpy(dtype=float); y=train.runoff_af.to_numpy(dtype=float)
    center=x.mean(axis=0); scale=x.std(axis=0)
    scale=np.where(scale>0,scale,1.)
    z=(x-center)/scale; ymean=y.mean()
    beta=np.linalg.solve(z.T@z+alpha*np.eye(len(columns)),z.T@(y-ymean))
    test=(row[columns].to_numpy(dtype=float)-center)/scale
    return max(0.,float(ymean+test@beta))


def predict(panel,with_swe=False,fresh_flow=False):
    flow='winter_flow_may_af' if fresh_flow else 'winter_flow_af'
    required=['winter_precip_mm',flow,'snow_frequency']
    if with_swe:required.append('ground_swe_mm')
    panel=panel.sort_values('year').copy()
    if panel.year.duplicated().any():raise ValueError('Duplicate year')
    panel['complete']=panel.eligible_snow & np.isfinite(panel[required]).all(axis=1)
    rows=[]
    for _,row in panel.iterrows():
        train=panel.loc[(panel.year<row.year)&panel.complete&np.isfinite(panel.runoff_af)]
        record=row.to_dict();record.update(eligible=False,n_train=len(train),reason='')
        if not row.complete:record['reason']='Missing feature or insufficient snow pixel coverage'
        elif len(train)<10:record['reason']='Fewer than ten prior eligible training years'
        else:
            specs=[('weather_flow',['winter_precip_mm',flow]),
                   ('weather_flow_snow',['winter_precip_mm',flow,'snow_frequency'])]
            if with_swe:
                specs += [('weather_flow_swe',['winter_precip_mm',flow,'ground_swe_mm']),
                          ('weather_flow_swe_snow',['winter_precip_mm',flow,'ground_swe_mm','snow_frequency'])]
            for name,columns in specs:record[name]=ridge_predict(train,row,columns)
            record.update(eligible=True,training_first_year=int(train.year.min()),training_last_year=int(train.year.max()),
                          climatology=float(train.runoff_af.mean()),persistence=row.prior_runoff_af)
        rows.append(record)
    return pd.DataFrame(rows)


def run(out=DEFAULT):
    daily.rebuild_snow(out,years=YEARS,month=5)
    rebuild_ground(out)
    panel=load_panel(out);panel.to_csv(out/'annual_panel.csv',index=False)
    result={}
    for name,with_swe,fresh in [('primary',False,False),('ground_swe',True,False),
                               ('fresh_may_flow',False,True),('fresh_may_flow_ground_swe',True,True)]:
        predictions=predict(panel,with_swe=with_swe,fresh_flow=fresh)
        predictions.to_csv(out/f'{name}_predictions.csv',index=False)
        result[name]=daily.evaluate_swe(predictions) if with_swe else daily.evaluate(predictions)
    result['candidate']='May snow -> July-September natural runoff, June15 issue'
    result['estimator']='Training-standardized ridge alpha5, unpenalized intercept, nonnegative forecasts'
    result['all_strong_comparator_gates_passed']=bool(
        result['primary'].get('frozen_gate_passed') and result['fresh_may_flow'].get('frozen_gate_passed')
        and all(result[k].get('rmse_reduction_vs_ground_swe',-1)>=.05
                and result[k].get('mae_reduction_vs_ground_swe',-1)>0
                and result[k].get('rmse_reduction_ci95',[-1])[0]>0 for k in ('ground_swe','fresh_may_flow_ground_swe')))
    result['limitations']=['Exploratory horizon selected after failed March experiment; source target table previously available',
                           'Current-vintage scientific forecast only; historical releases unverified',
                           'PC mirror missing May2024 onward; years remain abstentions',
                           'Single basin and small annual sample; no familywise discovery or market alpha claim']
    result['input_sha256']={str(p.relative_to(out)):daily.sha256(p) for p in [out/'annual_snow.csv',out/'ground/summer_ground.csv',out/'sbf_monthly_fnf.csv',out/'protocol.json']}
    result['code_sha256']={p.name:daily.sha256(p) for p in [Path(__file__),Path(daily.__file__)]}
    (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--fetch',action='store_true');p.add_argument('--workers',type=int,default=8)
    p.add_argument('--output-dir',type=Path,default=DEFAULT);args=p.parse_args();out=args.output_dir
    if args.fetch:daily.fetch(out,workers=args.workers,years=YEARS,month=5)
    print(json.dumps(run(out),indent=2))

if __name__=='__main__':main()
