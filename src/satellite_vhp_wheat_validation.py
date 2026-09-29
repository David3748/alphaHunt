#!/usr/bin/env python3
"""Fixed NOAA crop-masked March vegetation -> Texas wheat June15 nowcast.

Offline reproducible. Current archives are not original-vintage certification.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'results/vhp_wheat'
BASE = ['year', 'prior_target', 'winter_precip_inches', 'winter_tavg_f']
SAT = ['SMN', 'SMT']
MODELS = ['weather', 'satellite', 'mean', 'persistence', 'trend', 'placebo']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_satellite(path, province="44: Texas"):
    text = Path(path).read_text()
    if province not in text or "area with 'WHEA'" not in text or "version='GC_current'" not in text:
        raise ValueError('Unexpected NOAA crop, province or archive version')
    lines = re.findall(r'(?:^|>)(\d{4},[^\n<]+)', text, flags=re.M)
    rows = [[float(x.strip()) for x in line.strip().rstrip(',').split(',')] for line in lines]
    frame = pd.DataFrame(rows, columns=['year', 'week', 'SMN', 'SMT', 'VCI', 'TCI', 'VHI'])
    frame[['year', 'week']] = frame[['year', 'week']].astype(int)
    if frame.duplicated(['year', 'week']).any():
        raise ValueError('Duplicate satellite year/week')
    selected = frame[frame.week.between(9, 13)].copy()
    selected.loc[~selected.SMN.between(-1, 1), 'SMN'] = np.nan
    selected.loc[~selected.SMT.between(180, 360), 'SMT'] = np.nan
    result = []
    for year, group in selected.groupby('year'):
        complete = len(group) == 5 and np.isfinite(group[SAT]).all().all()
        end = pd.Timestamp(year=int(year), month=1, day=1) + pd.Timedelta(days=90)
        result.append(dict(year=year, SMN=group.SMN.mean() if complete else np.nan,
                           SMT=group.SMT.mean() if complete else np.nan,
                           satellite_weeks=len(group), satellite_period_end=end,
                           satellite_available=end + pd.Timedelta(days=56)))
    return pd.DataFrame(result)


def parse_weather(path):
    frame = pd.read_csv(path, comment='#', dtype={'Date': str})
    frame['date'] = pd.to_datetime(frame.Date, format='%Y%m')
    if frame.date.duplicated().any():
        raise ValueError('Duplicate weather month')
    values = pd.to_numeric(frame.Value, errors='coerce')
    values = values.where(np.isfinite(values) & values.gt(-90))
    return pd.Series(values.to_numpy(), index=frame.date)


def weather_features(precip, temp, years):
    rows = []
    for year in years:
        dates = pd.date_range(f'{year-1}-10-01', f'{year}-05-01', freq='MS')
        p, t = precip.reindex(dates), temp.reindex(dates)
        rows.append(dict(year=year, winter_precip_inches=p.sum() if p.notna().all() else np.nan,
                         winter_tavg_f=t.mean() if t.notna().all() else np.nan,
                         ground_months=int((p.notna() & t.notna()).sum()),
                         ground_available=pd.Timestamp(f'{year}-05-31') + pd.Timedelta(days=14)))
    return pd.DataFrame(rows)


def parse_nass(path, state="TX"):
    raw = pd.read_csv(path, sep='\t', dtype=str).fillna('')
    criteria = {'SOURCE_DESC': 'SURVEY', 'AGG_LEVEL_DESC': 'STATE', 'STATE_ALPHA': state,
                'COMMODITY_DESC': 'WHEAT', 'CLASS_DESC': 'WINTER', 'DOMAIN_DESC': 'TOTAL',
                'FREQ_DESC': 'ANNUAL', 'PRODN_PRACTICE_DESC': 'ALL PRODUCTION PRACTICES',
                'UTIL_PRACTICE_DESC': 'ALL UTILIZATION PRACTICES'}
    for col, value in criteria.items():
        raw = raw[raw[col].eq(value)]
    raw['year'] = raw.YEAR.astype(int)
    raw['value'] = pd.to_numeric(raw.VALUE.str.replace(',', '', regex=False), errors='coerce')
    definitions = {'yield': ('YIELD', 'BU / ACRE', 'YEAR'),
                   'production_bu': ('PRODUCTION', 'BU', 'YEAR'),
                   'planted_acres': ('AREA PLANTED', 'ACRES', 'YEAR'),
                   'harvested_acres': ('AREA HARVESTED', 'ACRES', 'YEAR'),
                   'official_june_yield': ('YIELD', 'BU / ACRE', 'YEAR - JUN FORECAST'),
                   'official_may_yield': ('YIELD', 'BU / ACRE', 'YEAR - MAY FORECAST'),
                   'march_planted_acres': ('AREA PLANTED', 'ACRES', 'YEAR - MAR ACREAGE')}
    result = pd.DataFrame({'year': range(1981, 2026)}).set_index('year')
    for name, (stat, unit, period) in definitions.items():
        part = raw[raw.STATISTICCAT_DESC.eq(stat) & raw.UNIT_DESC.eq(unit) & raw.REFERENCE_PERIOD_DESC.eq(period)]
        if part.year.duplicated().any():
            raise ValueError(f'Duplicate NASS series for {name}')
        result[name] = part.set_index('year').value
    result['production_per_planted_acre'] = result.production_bu / result.planted_acres.where(result.planted_acres.gt(0))
    result['label_available'] = pd.to_datetime(result.index.astype(str) + '-11-01')
    return result.reset_index()


def load_panel(out=DEFAULT):
    raw = out / 'raw'
    for source in json.loads((raw / 'manifest.json').read_text()):
        if sha(raw / source['path']) != source['sha256']:
            raise ValueError(f"Changed raw source: {source['path']}")
    sat = parse_satellite(raw / 'noaa_texas_wheat_weekly.txt')
    wx = weather_features(parse_weather(raw / 'weather_pcp.csv'), parse_weather(raw / 'weather_tavg.csv'), sat.year)
    labels = parse_nass(raw / 'nass_texas_winter_wheat.tsv')
    panel = sat.merge(wx, on='year', validate='one_to_one').merge(labels, on='year', validate='one_to_one')
    panel['forecast_at'] = pd.to_datetime(panel.year.astype(str) + '-06-15')
    prior_sat = sat[['year'] + SAT].copy(); prior_sat.year += 1
    panel = panel.merge(prior_sat.rename(columns={k: 'prior_' + k for k in SAT}), on='year', how='left', validate='one_to_one')
    for target in ('yield', 'production_per_planted_acre'):
        lag = labels[['year', target]].copy(); lag.year += 1
        panel = panel.merge(lag.rename(columns={target: 'prior_' + target}), on='year', how='left', validate='one_to_one')
    return panel.sort_values('year').reset_index(drop=True)


def ridge_predict(train, row, columns, target, alpha=5.):
    x = train[columns].to_numpy(float); y = train[target].to_numpy(float)
    center, scale = x.mean(axis=0), x.std(axis=0)
    scale = np.where(scale > 0, scale, 1.)
    z = (x-center)/scale
    beta = np.linalg.solve(z.T @ z + alpha*np.eye(len(columns)), z.T @ (y-y.mean()))
    return float(y.mean() + ((row[columns].to_numpy(float)-center)/scale) @ beta)


def predict(panel, target='yield', start=2000, end=2025):
    panel = panel.sort_values('year').copy()
    if panel.year.duplicated().any():
        raise ValueError('Duplicate forecast year')
    panel['prior_target'] = panel['prior_' + target]
    required = BASE + SAT + ['prior_SMN', 'prior_SMT']
    complete = np.isfinite(panel[required].to_numpy(float)).all(axis=1)
    timing = panel.satellite_available.notna() & panel.ground_available.notna() & panel.forecast_at.notna()
    timing &= (panel.satellite_available <= panel.forecast_at) & (panel.ground_available <= panel.forecast_at)
    panel['usable'] = complete & timing
    rows = []
    for _, row in panel[panel.year.between(start, end)].iterrows():
        train = panel[(panel.year < row.year) & panel.usable & (panel.label_available < row.forecast_at)
                      & np.isfinite(panel[target])]
        record = dict(year=int(row.year), forecast_at=str(row.forecast_at.date()), target=target,
                      actual=float(row[target]), eligible=False, n_train=len(train), reason='')
        if not row.usable:
            record['reason'] = 'Incomplete or unavailable features'
        elif len(train) < 15:
            record['reason'] = 'Fewer than15prior eligible training years'
        else:
            for name, columns in [('weather', BASE), ('satellite', BASE+SAT),
                                  ('placebo', BASE+['prior_SMN', 'prior_SMT'])]:
                record[name] = ridge_predict(train, row, columns, target)
            # Unregularized simple trend is an intentionally strong comparator.
            fit = np.polyfit(train.year.to_numpy()-1980, train[target], 1)
            record.update(eligible=True, training_first_year=int(train.year.min()),
                          training_last_year=int(train.year.max()), mean=float(train[target].mean()),
                          persistence=float(row.prior_target), trend=float(np.polyval(fit, row.year-1980)))
            if target == 'yield':
                record['official_june_yield'] = row.official_june_yield
                record['official_may_yield'] = row.official_may_yield
        rows.append(record)
    return pd.DataFrame(rows)


def metrics(actual, predicted):
    errors = np.asarray(actual)-np.asarray(predicted)
    return dict(rmse=float(np.sqrt(np.mean(errors**2))), mae=float(np.mean(np.abs(errors))))


def paired_ci(years, actual, base, satellite, draws=10000):
    frame = pd.DataFrame(dict(year=years, actual=actual, base=base, satellite=satellite)).set_index('year')
    calendar = frame.reindex(range(int(frame.index.min()), int(frame.index.max())+1))
    n = len(calendar); rng = np.random.default_rng(20260927)
    starts = rng.integers(0, n, size=(draws, (n+1)//2))
    ids = np.stack([starts, (starts+1)%n], axis=-1).reshape(draws, -1)[:, :n]
    eb = (calendar.actual-calendar.base).to_numpy()[ids]
    es = (calendar.actual-calendar.satellite).to_numpy()[ids]
    counts = np.isfinite(eb).sum(axis=1)
    valid = counts > 0
    with np.errstate(invalid='ignore', divide='ignore'):
        gains = 1-np.sqrt(np.nansum(es[valid]**2,axis=1)/counts[valid])/np.sqrt(np.nansum(eb[valid]**2,axis=1)/counts[valid])
    finite = gains[np.isfinite(gains)]
    return list(map(float,np.quantile(finite,[.025,.975]))) if len(finite) else [None,None]


def evaluate(predictions):
    held = predictions[predictions.eligible].copy()
    held = held[np.isfinite(held[['actual']+MODELS]).all(axis=1)]
    result = dict(n_evaluation=len(held), evaluated_years=held.year.astype(int).tolist(),
                  abstentions=predictions.loc[~predictions.eligible,['year','reason']].to_dict('records'))
    if held.empty:
        return dict(**result, frozen_gate_passed=False, status='insufficient_data')
    result['models'] = {name: metrics(held.actual,held[name]) for name in MODELS}
    comparisons = {}
    for name in MODELS:
        if name == 'satellite': continue
        a,b=result['models']['satellite'],result['models'][name]
        comparisons[name] = dict(rmse_reduction=1-a['rmse']/b['rmse'],mae_reduction=1-a['mae']/b['mae'],
            annual_wins=int(((held.actual-held.satellite).abs() < (held.actual-held[name]).abs()).sum()),
            rmse_reduction_ci95=paired_ci(held.year,held.actual,held[name],held.satellite))
    result['comparisons'] = comparisons
    main = comparisons['weather']
    result['frozen_gate_passed'] = bool(len(held)>=20 and main['rmse_reduction']>=.05 and main['mae_reduction']>0
        and main['rmse_reduction_ci95'][0] is not None and main['rmse_reduction_ci95'][0]>0
        and all(comparisons[n]['rmse_reduction']>0 and comparisons[n]['mae_reduction']>0 for n in ['mean','persistence','trend']))
    if 'official_june_yield' in held:
        part = held.dropna(subset=['official_june_yield'])
        if len(part):
            b=metrics(part.actual,part.official_june_yield); a=metrics(part.actual,part.satellite)
            result['official_june_comparison'] = dict(n=len(part),official=b,satellite=a,
                rmse_reduction=1-a['rmse']/b['rmse'],mae_reduction=1-a['mae']/b['mae'],
                rmse_reduction_ci95=paired_ci(part.year,part.actual,part.official_june_yield,part.satellite),
                warning='QuickStats named historicalJune forecasts; exact initial issue dates and revision audit not reconstructed.')
    return result


def run(out=DEFAULT):
    panel=load_panel(out);panel.to_csv(out/'panel.csv',index=False)
    result={}
    for target,key in [('yield','primary_yield'),('production_per_planted_acre','secondary_production_intensity')]:
        predictions=predict(panel,target);predictions.to_csv(out/f'{key}_predictions.csv',index=False)
        result[key]=evaluate(predictions)
    result.update(protocol_sha256=sha(out/'protocol.json'),protocol_addendum_sha256=sha(out/'protocol_addendum.json'),code_sha256=sha(Path(__file__)),
        archive_sha256=sha(out/'raw/manifest.json'),original_vintage_operational_verification=False,
        trading_alpha_verified=False,
        claims='Exploratory current-vintage, chronologically held-out June15 pre-final-report nowcast. Not fully preharvest or a family-wise discovery. Current masks, processing, weather and labels have vintage limitations.')
    (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output-dir',type=Path,default=DEFAULT)
    run(parser.parse_args().output_dir)
