#!/usr/bin/env python3
"""Frozen Kings River geographic confirmation of the May summer-snow model."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import numpy as np
import pandas as pd
import satellite_snow_daily_validation as daily
import satellite_snow_summer_validation as summer

ROOT=Path(__file__).resolve().parents[1]
DEFAULT=ROOT/'results/satellite_validation/snow_kings'
YEARS=range(2000,2024)


def rebuild_ground(out=DEFAULT):
    manifest=out/'ground/source_manifest.json'
    if manifest.exists():daily.verify_named_sources(ROOT,json.loads(manifest.read_text()))
    path=summer.PRIOR/'ground/rebuild.py'
    spec=importlib.util.spec_from_file_location('snow_ground_parser',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    rain_path=summer.PRIOR/'ground/five_station_monthly.csv'
    station=json.loads((out/'ground/selection_protocol.json').read_text())['selected_station']
    snow_path=out/f'ground/{station.lower()}_swe.csv'
    rain=module.read_cdec(rain_path,'5SI',2,'M');snow=module.read_cdec(snow_path,station,3,'D')
    records=[]
    for year in YEARS:
        season=rain.reindex(pd.date_range(f'{year-1}-10-01',f'{year}-05-01',freq='MS'))
        values=season.value_mm.where(season.flag.isin(['','r']))
        late=snow.loc[f'{year}-05-29':f'{year}-05-31'];late=late.loc[late.value_mm.notna()&late.flag.eq('')]
        records.append(dict(year=year,winter_precip_mm=values.sum() if values.count()==8 else np.nan,
                            precipitation_months=int(values.count()),precipitation_revised_months=int(season.flag.eq('r').sum()),
                            ground_swe_mm=float(late.value_mm.iloc[-1]) if len(late) else np.nan,
                            ground_swe_date=late.index[-1] if len(late) else pd.NaT,
                            assumed_forecast_at=f'{year}-06-15',original_release_verified=False))
    frame=pd.DataFrame(records);frame.to_csv(out/'ground/summer_ground.csv',index=False)
    paths=[path,rain_path,snow_path,out/'ground/basin_full.geojson',out/'ground/selection_protocol.json',out/'ground/kgf_metadata.html']
    (out/'ground/source_manifest.json').write_text(json.dumps({str(p.relative_to(ROOT)):daily.sha256(p)for p in paths},indent=2)+'\n')
    return frame


def parse_kings_fnf(path):
    frame=pd.read_csv(path)
    if not (frame.STATION_ID.eq('KGF').all() and frame.SENSOR_NUMBER.eq(65).all() and frame.DURATION.eq('M').all() and frame.UNITS.eq('AF').all()):
        raise ValueError('Expected KGF monthly full-natural-flow sensor65 AF')
    dates=pd.to_datetime(frame['DATE TIME'],format='%Y%m%d %H%M');values=pd.to_numeric(frame.VALUE,errors='coerce')
    if dates.duplicated().any() or np.isinf(values).any():raise ValueError('Duplicate date or infinite flow')
    return pd.Series(values.to_numpy(),index=dates).sort_index()


def load_panel(out=DEFAULT):
    source=json.loads((out/'source_manifest.json').read_text())
    daily.verify_named_sources(out,{'kgf_monthly_fnf.csv':source['cdec_sha256']})
    snow=pd.read_csv(out/'annual_snow.csv');ground=pd.read_csv(out/'ground/summer_ground.csv')
    fnf=parse_kings_fnf(out/'kgf_monthly_fnf.csv');records=[]
    for year in YEARS:
        records.append(dict(year=year,runoff_af=daily.complete_sum(fnf,pd.date_range(f'{year}-07-01',periods=3,freq='MS')),
                            winter_flow_af=daily.complete_sum(fnf,pd.date_range(f'{year-1}-10-01',periods=7,freq='MS')),
                            winter_flow_may_af=daily.complete_sum(fnf,pd.date_range(f'{year-1}-10-01',periods=8,freq='MS')),
                            prior_runoff_af=daily.complete_sum(fnf,pd.date_range(f'{year-1}-07-01',periods=3,freq='MS'))))
    return pd.DataFrame(records).merge(snow,on='year',validate='one_to_one').merge(ground,on='year',validate='one_to_one')


def initial_scale(panel):
    initial=panel.set_index('year').reindex(range(2000,2010)).runoff_af
    if not np.isfinite(initial).all() or initial.mean()<=0:raise ValueError('Ten complete initial training labels required for fixed basin scale')
    return float(initial.mean())


def pooled_metrics(frames,panels,with_swe=False,draws=10000):
    base='weather_flow_swe' if with_swe else 'weather_flow'
    sat=base+'_snow'
    cleaned={}
    for basin,frame in frames.items():
        frame=frame.loc[frame.eligible].set_index('year')
        if frame.empty:return {'status':'insufficient_data'}
        cleaned[basin]=frame.loc[np.isfinite(frame[['runoff_af',base,sat]]).all(axis=1)]
    common=sorted(set.intersection(*(set(f.index)for f in cleaned.values())))
    if not common:return {'status':'insufficient_data'}
    calendar=np.arange(min(common),max(common)+1)
    scales={basin:initial_scale(panels[basin])for basin in cleaned}
    errors={}
    for model in (base,sat):
        errors[model]=np.stack([((frame.runoff_af-frame[model])/scales[basin]).reindex(common).reindex(calendar).to_numpy()for basin,frame in cleaned.items()])
    base_loss=np.mean(errors[base]**2,axis=0);sat_loss=np.mean(errors[sat]**2,axis=0)
    # One vector of year blocks is applied to all basins. Missing years stay gaps.
    rng=np.random.default_rng(20260927);n=len(calendar)
    starts=rng.integers(0,n,size=(draws,(n+1)//2))
    indices=np.stack([starts,(starts+1)%n],axis=-1).reshape(draws,-1)[:,:n]
    eb,es=base_loss[indices],sat_loss[indices];keep=np.isfinite(eb).any(axis=1)&np.isfinite(es).any(axis=1)
    gains=1-np.sqrt(np.nanmean(es[keep],axis=1))/np.sqrt(np.nanmean(eb[keep],axis=1))
    rb=float(np.sqrt(np.nanmean(base_loss)));rs=float(np.sqrt(np.nanmean(sat_loss)))
    mb=float(np.nanmean(np.abs(errors[base])));ms=float(np.nanmean(np.abs(errors[sat])))
    return dict(evaluated_years=common,n_calendar_years=len(common),n_basins=len(frames),
                initial_2000_2009_mean_af=scales,normalized_rmse_baseline=rb,normalized_rmse_satellite=rs,
                rmse_reduction=1-rs/rb,mae_reduction=1-ms/mb,
                rmse_reduction_ci95=[float(v)for v in np.quantile(gains,[.025,.975])],
                pooling='Equal basin weight; errors normalized by fixed initial2000-2009 basin mean, shared calendar blocks. Basins are not independent years.')


def run(out=DEFAULT):
    daily.rebuild_snow(out,years=YEARS,month=5);rebuild_ground(out)
    panel=load_panel(out);panel.to_csv(out/'annual_panel.csv',index=False)
    prior_panel=pd.read_csv(summer.DEFAULT/'annual_panel.csv')
    results={};pooled={}
    for name,with_swe,fresh in [('primary',False,False),('ground_swe',True,False),('fresh_may_flow',False,True),('fresh_may_flow_ground_swe',True,True)]:
        predictions=summer.predict(panel,with_swe=with_swe,fresh_flow=fresh)
        predictions.to_csv(out/f'{name}_predictions.csv',index=False)
        results[name]=daily.evaluate_swe(predictions)if with_swe else daily.evaluate(predictions)
        prior=pd.read_csv(summer.DEFAULT/f'{name}_predictions.csv')
        pooled[name]=pooled_metrics({'Kings':predictions,'SanJoaquin':prior},{'Kings':panel,'SanJoaquin':prior_panel},with_swe=with_swe)
    kings=results['fresh_may_flow'].get('comparisons',{}).get('weather_flow',{})
    both=pooled['fresh_may_flow']
    confirmation=bool(kings.get('rmse_reduction',-1)>=.05 and kings.get('mae_reduction',-1)>0
                      and both.get('rmse_reduction',-1)>=.05 and both.get('mae_reduction',-1)>0
                      and both.get('rmse_reduction_ci95',[-1])[0]>0)
    raw=pd.read_csv(out/'kgf_monthly_fnf.csv');dates=pd.to_datetime(raw['DATE TIME'],format='%Y%m%d %H%M')
    flags=raw.DATA_FLAG.fillna('').str.strip()
    target=dates.dt.month.isin([7,8,9]) & dates.dt.year.between(2000,2023)
    quality={'all_monthly_flag_counts':flags.value_counts().to_dict(),'summer_target_flag_counts':flags[target].value_counts().to_dict(),
             'flagged_summer_targets':[{'date':str(d.date()),'flag':f}for d,f in zip(dates[target],flags[target])if f],
             'handling':'All finite official monthly AF values retained under frozen current-vintage protocol; flags preserved and counted, not post-result exclusions.'}
    (out/'target_quality.json').write_text(json.dumps(quality,indent=2)+'\n')
    result={'kings':results,'pooled':pooled,'geographic_confirmation_gate_passed':confirmation,'target_quality':quality,
            'claims':'Geographic current-vintage forecast confirmation only if gate passes; adjacent basins share weather, no independent-year doubling or financial-alpha claim.'}
    sources=[out/'annual_snow.csv',out/'ground/summer_ground.csv',out/'kgf_monthly_fnf.csv',out/'protocol.json',out/'ground_selection_addendum.json',out/'ground/selection_protocol.json',summer.DEFAULT/'annual_panel.csv',summer.DEFAULT/'summary.json']
    sources += [summer.DEFAULT/f'{name}_predictions.csv'for name in ('primary','ground_swe','fresh_may_flow','fresh_may_flow_ground_swe')]
    result['input_sha256']={str(p.relative_to(ROOT)):daily.sha256(p)for p in sources}
    result['code_sha256']={p.name:daily.sha256(p)for p in [Path(__file__),Path(summer.__file__),Path(daily.__file__)]}
    (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n');return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--fetch',action='store_true');p.add_argument('--workers',type=int,default=8);p.add_argument('--output-dir',type=Path,default=DEFAULT)
    args=p.parse_args()
    if args.fetch:daily.fetch(args.output_dir,workers=args.workers,years=YEARS,month=5,flow_station='KGF')
    print(json.dumps(run(args.output_dir),indent=2))

if __name__=='__main__':main()
