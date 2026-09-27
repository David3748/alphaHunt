#!/usr/bin/env python3
"""Frozen physical GOES irradiance nowcast, evaluated before monthly EIA release.

Operational satellite file creation and archive timestamps are independently gated
by the extractor. EIA final-vintage labels use a conservative publication embargo.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'results/satellite_validation/goes_solar'
BASELINES = ['persistence', 'seasonal_mean', 'season_trend', 'weather']
MODELS = [*BASELINES, 'satellite', 'prior_year_irradiance_placebo']


def timestamp(values):
    """Normalize source timestamps to UTC, represented without timezone."""
    return pd.to_datetime(values, utc=True).dt.tz_localize(None)


def load_panel(out: Path = DEFAULT, plant: str = '57695', live: bool = False) -> pd.DataFrame:
    raw = json.loads((out / f'inputs/eia_{plant}.json').read_text())['response']
    data = pd.DataFrame(raw['data'])
    if int(raw['total']) != len(data):
        raise ValueError('Incomplete EIA response')
    data = data.loc[(data.plantCode.astype(str) == plant) & (data.fuel2002 == 'SUN') & (data.primeMover == 'PV')].copy()
    data['date'] = pd.to_datetime(data.period) + pd.offsets.MonthEnd(0)
    data['actual_mwh'] = pd.to_numeric(data.generation)
    if data.date.duplicated().any():
        raise ValueError('Duplicate plant-month outcomes')
    source = [pd.read_csv(out / 'monthly_irradiance.csv')]
    if live:
        source.append(pd.read_csv(out / 'live_2026/monthly_irradiance.csv'))
    irradiance = pd.concat(source, ignore_index=True)
    irradiance = irradiance.loc[irradiance.plant_code.astype(str) == plant].copy()
    irradiance['date'] = pd.to_datetime(irradiance.date) + pd.offsets.MonthEnd(0)
    if irradiance.date.duplicated().any():
        raise ValueError('Duplicate satellite plant-month')
    irradiance['last_source_available_at'] = timestamp(irradiance.last_source_available_at)
    irradiance['forecast_at'] = timestamp(irradiance.forecast_at)
    irradiance = irradiance.set_index('date')
    weather = pd.read_csv(out / 'ground_weather/monthly_weather.csv')
    weather['date'] = pd.to_datetime(weather.date) + pd.offsets.MonthEnd(0)
    weather.loc[~weather.complete, ['tmean_c', 'dtr_c', 'prcp_mm']] = np.nan
    weather = weather.set_index('date')
    panel = data.set_index('date')[['actual_mwh']].join(irradiance, how='outer').join(weather[['tmean_c','dtr_c','prcp_mm']]).sort_index()
    panel['year'], panel['month'] = panel.index.year, panel.index.month
    panel['time'] = (panel.year - 2015) + (panel.month - 1) / 12
    panel['daily_mwh'] = panel.actual_mwh / panel.index.days_in_month
    panel['label_available_at'] = panel.index + pd.offsets.MonthEnd(2)
    panel['ghi'] = panel.ghi_kwh_m2_day
    panel['eligible'] = panel.eligible.eq(True)
    if (panel.actual_mwh.dropna() <= 0).any():
        raise ValueError('Nonpositive production')
    return panel


def design(frame: pd.DataFrame, weather: bool) -> np.ndarray:
    cols = [np.ones(len(frame)), frame.time.to_numpy(),
            *[frame.month.eq(month).astype(float).to_numpy() for month in range(2, 13)]]
    if weather:
        cols += [frame.tmean_c.to_numpy(), frame.dtr_c.to_numpy(), np.log1p(frame.prcp_mm.to_numpy())]
    return np.column_stack(cols)


def source_usable(row: pd.Series, issue: pd.Timestamp) -> bool:
    return bool(row.eligible and pd.notna(row.ghi) and np.isfinite(row.ghi) and row.ghi > 0
                and pd.notna(row.last_source_available_at)
                and row.last_source_available_at <= issue and np.isfinite(row.daylight_coverage) and .90 <= row.daylight_coverage <= 1)


def predict(panel: pd.DataFrame, start: str = '2022-01-01', end: str = '2025-12-31', require_weather: bool = True) -> pd.DataFrame:
    rows = []
    for date, row in panel.loc[start:end].iterrows():
        issue = date + pd.Timedelta(days=14)
        record = {'date': date, 'year': date.year, 'forecast_at': issue,
                  'target_label_available_at': row.label_available_at, 'actual_mwh': row.actual_mwh}
        historical_dates = [date - pd.DateOffset(years=y) + pd.offsets.MonthEnd(0) for y in (2,1)]
        if not all(d in panel.index for d in historical_dates):
            record.update(eligible=False, abstention_reason='Missing two-year same-month history')
            rows.append(record); continue
        past = panel.loc[historical_dates]
        usable = (source_usable(row, issue) and all(source_usable(r, issue) for _,r in past.iterrows())
                  and past.daily_mwh.notna().all() and (past.label_available_at <= issue).all())
        training = panel.loc[(panel.index < date) & (panel.label_available_at <= issue)]
        training = training.dropna(subset=['actual_mwh','tmean_c','dtr_c','prcp_mm']).tail(48)
        target = panel.loc[[date]]
        weather_available = target[['tmean_c','dtr_c','prcp_mm']].notna().all().all() and len(training) >= 36
        if not usable or (require_weather and not weather_available):
            record.update(eligible=False, abstention_reason='Unavailable satellite/history or insufficient common weather training', n_train=len(training))
            rows.append(record); continue
        base_daily, base_ghi = past.daily_mwh.mean(), past.ghi.mean()
        scale = date.days_in_month
        record.update(eligible=True, n_train=len(training), ghi=row.ghi, calibration_ghi=base_ghi,
                      source_available_at=row.last_source_available_at, daylight_coverage=row.daylight_coverage,
                      calibration_latest_label_release=past.label_available_at.max(),
                      persistence=past.daily_mwh.iloc[-1]*scale, seasonal_mean=base_daily*scale,
                      satellite=base_daily*row.ghi/base_ghi*scale,
                      prior_year_irradiance_placebo=base_daily*past.ghi.iloc[-1]/base_ghi*scale)
        if weather_available:
            record.update(training_last_month=training.index.max(), training_latest_label_release=training.label_available_at.max())
            for model in ['season_trend','weather']:
                coef = np.linalg.lstsq(design(training, model=='weather'), np.log(training.daily_mwh), rcond=None)[0]
                record[model] = float(np.exp((design(target, model=='weather')@coef)[0])*scale)
        rows.append(record)
    return pd.DataFrame(rows)


def bootstrap(errors: np.ndarray, groups: list[np.ndarray], seed: int = 20260927) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.array([np.sqrt((errors[np.concatenate([groups[i] for i in rng.integers(len(groups),size=len(groups))])]**2).mean(axis=0)) for _ in range(10000)])


def score(predictions: pd.DataFrame) -> dict:
    valid = predictions.loc[predictions.eligible].copy()
    if valid.empty:
        raise ValueError('No eligible predictions')
    errors = valid[MODELS].to_numpy()-valid.actual_mwh.to_numpy()[:,None]
    if not np.isfinite(errors).all():
        raise ValueError('Missing prediction/outcome among eligible rows')
    metrics = {m:{'rmse_mwh':float(np.sqrt((errors[:,i]**2).mean())), 'mae_mwh':float(np.abs(errors[:,i]).mean())} for i,m in enumerate(MODELS)}
    groups = [np.flatnonzero(valid.year.to_numpy()==y) for y in sorted(valid.year.unique())]
    samples = bootstrap(errors,groups)
    month = pd.to_datetime(valid.date).dt.month.to_numpy()
    seasonal_samples = bootstrap(errors,[np.flatnonzero(month==m) for m in sorted(set(month))])
    sat = MODELS.index('satellite')
    comparisons = {}
    for name in BASELINES:
        i = MODELS.index(name)
        comparisons[name] = {'rmse_reduction_pct':100*(1-metrics['satellite']['rmse_mwh']/metrics[name]['rmse_mwh']),
                             'mae_reduction_pct':100*(1-metrics['satellite']['mae_mwh']/metrics[name]['mae_mwh']),
                             'rmse_gain_year_bootstrap_ci95_mwh':np.quantile(samples[:,i]-samples[:,sat],[.025,.975]).tolist(),
                             'rmse_gain_season_bootstrap_ci95_mwh':np.quantile(seasonal_samples[:,i]-seasonal_samples[:,sat],[.025,.975]).tolist()}
    best = min(BASELINES,key=lambda m:metrics[m]['rmse_mwh'])
    gate = (all(v['rmse_reduction_pct']>=5 and v['mae_reduction_pct']>0 for v in comparisons.values())
            and comparisons[best]['rmse_gain_year_bootstrap_ci95_mwh'][0]>0
            and metrics['satellite']['rmse_mwh'] < metrics['prior_year_irradiance_placebo']['rmse_mwh'])
    def subset(part):
        return {'n':len(part),'rmse_mwh':{m:float(np.sqrt(np.mean((part[m]-part.actual_mwh)**2))) for m in MODELS}}
    return {'n_test_months':len(valid),'abstentions':int((~predictions.eligible).sum()),'n_year_clusters':len(groups),
            'metrics':metrics,'comparisons':comparisons,'strongest_nonsatellite_baseline':best,'primary_gate_passed':bool(gate),
            'by_year':{str(y):subset(part) for y,part in valid.groupby('year')},
            'regimes':{'before_april_2024':subset(valid.loc[pd.to_datetime(valid.date)<'2024-04-01']),
                       'april_2024_onward':subset(valid.loc[pd.to_datetime(valid.date)>='2024-04-01'])}}


def annual_confirmation(out: Path = DEFAULT) -> tuple[pd.DataFrame,dict]:
    features = pd.read_csv(out/'monthly_irradiance.csv')
    features = features.loc[features.plant_code.astype(str)=='57439'].copy()
    features['date'] = pd.to_datetime(features.date)
    features['last_source_available_at'] = timestamp(features.last_source_available_at)
    features['year'] = features.date.dt.year
    annual = {}
    for year,part in features.groupby('year'):
        issue = pd.Timestamp(year=int(year)+1,month=1,day=14)
        if len(part)!=12 or part.date.dt.month.nunique()!=12 or not part.eligible.eq(True).all() or not np.isfinite(part.daylight_coverage).all() or (part.daylight_coverage<.9).any() or (part.daylight_coverage>1).any() or part.last_source_available_at.isna().any() or (part.last_source_available_at>issue).any() or not np.isfinite(part.ghi_kwh_m2_day).all() or (part.ghi_kwh_m2_day<=0).any():
            continue
        days = part.date.dt.days_in_month
        annual[int(year)] = float(np.average(part.ghi_kwh_m2_day,weights=days))
    labels = pd.read_csv(out/'reporting_frequency/annual_generation.csv')
    labels = labels.loc[labels.plant_id.astype(str)=='57439'].set_index('year')
    rows=[]
    for year in range(2022,2026):
        issue = pd.Timestamp(year=year+1,month=1,day=14)
        history = [year-2,year-1]
        record={'year':year,'forecast_at':issue,'actual_mwh':float(labels.loc[year,'annual_generation_mwh'])}
        if not all(y in annual and y in labels.index for y in [*history,year]):
            record.update(eligible=False);rows.append(record);continue
        days=lambda y:366 if pd.Timestamp(year=y,month=12,day=31).is_leap_year else 365
        daily=[float(labels.loc[y,'annual_generation_mwh'])/days(y) for y in history]
        base=float(np.mean(daily))*days(year)
        record.update(eligible=True,satellite=base*annual[year]/np.mean([annual[y] for y in history]),
                      seasonal_mean=base,persistence=daily[-1]*days(year),ghi=annual[year],
                      latest_training_label_release=pd.Timestamp(year=year,month=11,day=1))
        rows.append(record)
    frame=pd.DataFrame(rows); valid=frame.loc[frame.eligible]
    metrics={m:float(np.sqrt(np.mean((valid[m]-valid.actual_mwh)**2))) for m in ['satellite','seasonal_mean','persistence']} if len(valid) else {}
    return frame,{'n_test_years':len(valid),'rmse_mwh':metrics,'point_confirmation_passed':bool(len(valid)==4 and metrics['satellite']<min(metrics['seasonal_mean'],metrics['persistence'])),
                  'interpretation':'Four secondary annual-meter outcomes; correlated weather with primary. Annual totals avoid pre2023 EIA monthly allocation.'}


def run(out:Path=DEFAULT)->dict:
    panel=load_panel(out); predictions=predict(panel); summary=score(predictions)
    annual, confirmation=annual_confirmation(out);summary['confirmation']=confirmation
    summary['forecast_usefulness_verified']=bool(summary['primary_gate_passed'] and confirmation['point_confirmation_passed'])
    summary['original_vintage_certified']=False
    summary['trading_alpha_verified']=False
    summary['scope']='Independent production nowcast accuracy at measured plants, before reported outcomes; no asset-return claim'
    summary['limits']=['Only four primary test years and four secondary annual outcomes','Current final EIA labels and revised ground weather; operational GOES files retain actual source/archive timing','Exploratory continuation after earlier failed candidates; no familywise discovery correction','PostApril2024 source resolution/algorithm changed; fixed footprint and regime metrics retained']
    summary['protocol']=json.loads((out/'protocol.json').read_text())
    inputs=[out/'monthly_irradiance.csv',out/'inputs/eia_57695.json',out/'ground_weather/monthly_weather.csv',out/'reporting_frequency/annual_generation.csv',out/'protocol.json']
    summary['input_hashes']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    panel.to_csv(out/'panel.csv',index_label='date',float_format='%.12g')
    predictions.to_csv(out/'predictions.csv',index=False,float_format='%.12g')
    annual.to_csv(out/'annual_confirmation_predictions.csv',index=False,float_format='%.12g')
    (out/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    return summary

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,default=DEFAULT)
    args=parser.parse_args(); result=run(args.out)
    print(json.dumps({k:result[k] for k in ['n_test_months','metrics','comparisons','primary_gate_passed','confirmation','forecast_usefulness_verified']},indent=2))
