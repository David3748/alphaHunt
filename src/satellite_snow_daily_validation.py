#!/usr/bin/env python3
"""Frozen March daily MODIS snow -> April-July natural runoff experiment.

Offline default uses compact source-pixel snapshots. --fetch refreshes official
current archives, not historical first-release vintages. No retrospective fill.
"""
from __future__ import annotations
import argparse
import calendar
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'results/satellite_validation/snow_daily'
STAC = 'https://planetarycomputer.microsoft.com/api/stac/v1/search'
TOKEN = 'https://planetarycomputer.microsoft.com/api/sas/v1/token/modiseuwest/modis-061-cogs'
FNF = 'https://cdec.water.ca.gov/dynamicapp/req/CSVDataServlet'
MODELS = ('weather_flow', 'weather_flow_snow', 'climatology', 'persistence')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_named_sources(base, sources):
    for relative, expected in sources.items():
        path = Path(base) / relative
        if not path.is_file() or sha256(path) != expected:
            raise ValueError(f'Source integrity mismatch: {relative}')


def verify_pixel_sources(out):
    source = json.loads((out / 'source_manifest.json').read_text())
    verify_named_sources(out, {'modis_manifest.json': source['manifest_sha256']})
    verify_named_sources(out / 'pixels', source['pixel_snapshots'])


def get(url, **kwargs):
    import requests
    for attempt in range(4):
        try:
            response = requests.get(url, timeout=90, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def fetch(out=DEFAULT, workers=8, years=range(2000, 2026), month=3, flow_station='SBF'):
    # Raster dependencies needed only for explicit network extraction.
    import rasterio
    from rasterio.features import bounds, geometry_mask
    from rasterio.warp import transform_geom
    from rasterio.windows import from_bounds, Window
    raw = out / 'pixels'
    raw.mkdir(parents=True, exist_ok=True)
    manifest_path = out / 'modis_manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    else:
        manifest = []
        for year in years:
            params = dict(collections='modis-10A1-061', bbox='-119.80256,36.96619,-118.65450,37.73916',
                          datetime=f'{year}-{month:02d}-01T00:00:00Z/{year}-{month:02d}-{calendar.monthrange(year,month)[1]}T23:59:59Z',
                          query=json.dumps({'platform': {'eq': 'terra'}}), limit=100)
            page = get(STAC, params=params).json()
            if any(link['rel'] == 'next' for link in page.get('links', [])):
                raise ValueError('Unexpected pagination; manifest would be incomplete')
            for feature in page['features']:
                if not feature['id'].startswith('MOD10A1.') or '.h08v05.' not in feature['id']:
                    raise ValueError('Unexpected platform or basin tile')
                prop = feature['properties']
                acquisition = prop.get('datetime') or prop.get('start_datetime')
                manifest.append(dict(id=feature['id'], date=acquisition[:10],
                                     created=prop.get('created'), updated=prop.get('updated'),
                                     assets={k: feature['assets'][k]['href'] for k in
                                             ('NDSI_Snow_Cover', 'NDSI_Snow_Cover_Basic_QA')},
                                     item_sha256=hashlib.sha256(json.dumps(feature, sort_keys=True).encode()).hexdigest()))
        # Scientific current-vintage study: latest processing version per day,
        # chosen from metadata before any feature/outcome inspection.
        candidates = sorted(manifest, key=lambda x: (x['date'], x['created'] or '', x['id']))
        selected = {item['date']: item for item in candidates}
        discarded = [item for item in candidates if item['id'] != selected[item['date']]['id']]
        (out / 'superseded_granules.json').write_text(json.dumps(discarded, indent=2) + '\n')
        manifest = sorted(selected.values(), key=lambda x: x['date'])
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    token = get(TOKEN, params={'_': str(time.time())}).json()['token']
    basin_path = out / 'ground/basin_full.geojson'
    basin = json.loads(basin_path.read_text())['features'][0]['geometry']
    env = dict(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR', CPL_VSIL_CURL_ALLOWED_EXTENSIONS='.tif',
               GDAL_HTTP_MAX_RETRY='3', GDAL_HTTP_RETRY_DELAY='1')
    with rasterio.Env(**env):
        with rasterio.open(manifest[0]['assets']['NDSI_Snow_Cover'] + '?' + token) as ds:
            geometry = transform_geom('EPSG:4326', ds.crs, basin)
            floating = from_bounds(*bounds(geometry), ds.transform)
            window = Window(int(np.floor(floating.col_off)), int(np.floor(floating.row_off)),
                            int(np.ceil(floating.width)) + 2, int(np.ceil(floating.height)) + 2)
            mask = geometry_mask([geometry], out_shape=(int(window.height), int(window.width)),
                                 transform=ds.window_transform(window), invert=True, all_touched=False)
            reference = dict(crs=ds.crs.to_wkt(), transform=list(ds.transform),
                             window=list(window.flatten()), basin_pixels=int(mask.sum()),
                             pixel_size_m=abs(ds.transform.a), polygon_sha256=sha256(basin_path),
                             pixel_rule='Pixel center within authoritative basin; equal-area sinusoidal grid')
    (out / 'extraction_geometry.json').write_text(json.dumps(reference, indent=2) + '\n')
    np.savez_compressed(out / 'basin_mask.npz', mask=mask)

    def read_day(item):
        target = raw / (item['date'] + '.npz')
        if target.exists():
            return item['date'], None
        try:
            values = {}
            with rasterio.Env(**env):
                for variable in ('NDSI_Snow_Cover', 'NDSI_Snow_Cover_Basic_QA'):
                    with rasterio.open(item['assets'][variable] + '?' + token) as ds:
                        if not np.allclose(list(ds.transform), reference['transform'], rtol=0, atol=1e-6) or ds.crs != rasterio.crs.CRS.from_wkt(reference['crs']):
                            raise ValueError('MODIS grid changed')
                        values[variable] = ds.read(1, window=window)[mask]
            np.savez_compressed(target, **values)
            return item['date'], None
        except Exception as exc:
            return item['date'], f'{type(exc).__name__}: {exc}'

    failures = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(read_day, item) for item in manifest]
        for done, future in enumerate(as_completed(futures), 1):
            date, error = future.result()
            if error:
                failures.append(dict(date=date, error=error))
            if done % 50 == 0:
                print(f'{done}/{len(manifest)} MODIS daily pairs processed; {len(failures)} failures', flush=True)
    (out / 'extraction_failures.json').write_text(json.dumps(failures, indent=2) + '\n')
    source = get(FNF, params=dict(Stations=flow_station, SensorNums=65, dur_code='M',
                                 Start='1999-10-01', End=f'{max(years)}-12-31'))
    flow_path = out / f'{flow_station.lower()}_monthly_fnf.csv'
    flow_path.write_bytes(source.content)
    metadata = dict(retrieved_at_utc=datetime.now(timezone.utc).isoformat(),
                    stac=STAC, cdec_url=source.url, manifest_sha256=sha256(manifest_path),
                    cdec_sha256=sha256(flow_path),
                    pixel_snapshots={p.name: sha256(p) for p in sorted(raw.glob('*.npz'))},
                    source_scope='Current-vintage raw daily Terra observations, screened NDSI and Basic QA only',
                    release_warning='Acquisition month precedes the nominal following-month issue; current reprocessing may be later. First-release values and publication times are unverified.')
    (out / 'source_manifest.json').write_text(json.dumps(metadata, indent=2) + '\n')


def snow_feature(snow, qa, minimum_days=3):
    if snow.shape != qa.shape or snow.ndim != 2:
        raise ValueError('Expected matching [daily observation, basin pixel] arrays')
    valid = (snow >= 0) & (snow <= 100) & ((qa == 0) | (qa == 1))
    count = valid.sum(axis=0)
    qualified = count >= minimum_days
    coverage = float(qualified.mean())
    frequency = np.divide(((snow > 0) & valid).sum(axis=0), count,
                          out=np.full(count.shape, np.nan), where=count > 0)
    value = float(frequency[qualified].mean()) if qualified.any() else np.nan
    return dict(march_snow_frequency=value, snow_pixel_coverage=coverage,
                mean_valid_days=float(count.mean()), eligible_snow=coverage >= .90,
                basin_pixels=len(count), qualified_pixels=int(qualified.sum()))


def rebuild_snow(out=DEFAULT, years=range(2000, 2026), month=3):
    verify_pixel_sources(out)
    manifest = json.loads((out / 'modis_manifest.json').read_text())
    n = json.loads((out / 'extraction_geometry.json').read_text())['basin_pixels']
    rows = []
    for year in years:
        items = [item for item in manifest if item['date'].startswith(f'{year}-{month:02d}-')]
        arrays = []
        for item in items:
            path = out / 'pixels' / (item['date'] + '.npz')
            if path.exists():
                with np.load(path) as data:
                    arrays.append((data['NDSI_Snow_Cover'], data['NDSI_Snow_Cover_Basic_QA']))
        if arrays:
            snow = np.stack([a[0] for a in arrays]); qa = np.stack([a[1] for a in arrays])
        else:
            snow = qa = np.empty((0, n), dtype=np.uint8)
        record = snow_feature(snow, qa)
        record.update(year=year, forecast_at=f'{year}-{month+1:02d}-15', expected_days=calendar.monthrange(year,month)[1],
                      source_days=len(items), retrieved_days=len(arrays),
                      max_acquisition=max((a['date'] for a in items), default=None),
                      max_reprocessing_created=max((a['created'] or '' for a in items), default=None))
        rows.append(record)
    frame = pd.DataFrame(rows)
    if month != 3:
        frame = frame.rename(columns={'march_snow_frequency': 'snow_frequency'})
    frame.to_csv(out / 'annual_snow.csv', index=False)
    return frame


def parse_fnf(path):
    frame = pd.read_csv(path)
    if not frame.SENSOR_NUMBER.eq(65).all() or not frame.STATION_ID.eq('SBF').all() or not frame.UNITS.eq('AF').all():
        raise ValueError('Wrong natural-flow series or units')
    dates = pd.to_datetime(frame['DATE TIME'], format='%Y%m%d %H%M')
    values = pd.to_numeric(frame.VALUE, errors='coerce')
    if np.isinf(values).any():
        raise ValueError('Infinite natural flow')
    if dates.duplicated().any():
        raise ValueError('Duplicate monthly natural-flow dates')
    return pd.Series(values.to_numpy(), index=dates).sort_index()


def complete_sum(series, dates):
    values = series.reindex(dates)
    return float(values.sum()) if values.notna().all() else np.nan


def load_panel(out=DEFAULT):
    ground_summary = json.loads((out / 'ground/summary.json').read_text())
    verify_named_sources(out / 'ground', {'winter_ground.csv': ground_summary['feature_file_sha256']})
    source = json.loads((out / 'source_manifest.json').read_text())
    verify_named_sources(out, {'sbf_monthly_fnf.csv': source['cdec_sha256']})
    snow = pd.read_csv(out / 'annual_snow.csv')
    ground = pd.read_csv(out / 'ground/winter_ground.csv')
    fnf = parse_fnf(out / 'sbf_monthly_fnf.csv')
    rows = []
    for year in snow.year:
        rows.append(dict(year=year,
                         runoff_af=complete_sum(fnf, pd.date_range(f'{year}-04-01', periods=4, freq='MS')),
                         winter_flow_af=complete_sum(fnf, pd.date_range(f'{year-1}-10-01', periods=5, freq='MS')),
                         prior_runoff_af=complete_sum(fnf, pd.date_range(f'{year-1}-04-01', periods=4, freq='MS'))))
    return pd.DataFrame(rows).merge(snow, on='year', validate='one_to_one').merge(ground, on='year', validate='one_to_one')


def design(frame, columns):
    return np.column_stack([np.ones(len(frame))] + [frame[col].to_numpy(dtype=float) for col in columns])


def predict(panel, with_swe=False):
    required = ['winter_flow_af', 'winter_precip_mm', 'march_snow_frequency']
    if with_swe:
        required.append('ground_swe_mm')
    panel = panel.sort_values('year').copy()
    if panel.year.duplicated().any():
        raise ValueError('Duplicate forecast years')
    panel['complete'] = panel.eligible_snow & np.isfinite(panel[required]).all(axis=1)
    rows = []
    for _, row in panel.iterrows():
        train = panel.loc[(panel.year < row.year) & panel.complete & np.isfinite(panel.runoff_af)]
        record = row.to_dict()
        record.update(eligible=False, n_train=len(train), reason='')
        if not row.complete:
            record['reason'] = 'Missing feature or insufficient snow pixel coverage'
        elif len(train) < 10:
            record['reason'] = 'Fewer than ten prior eligible training years'
        else:
            specs = [('weather_flow', ['winter_precip_mm', 'winter_flow_af']),
                     ('weather_flow_snow', ['winter_precip_mm', 'winter_flow_af', 'march_snow_frequency'])]
            if with_swe:
                specs += [('weather_flow_swe', ['winter_precip_mm', 'winter_flow_af', 'ground_swe_mm']),
                          ('weather_flow_swe_snow', ['winter_precip_mm', 'winter_flow_af', 'ground_swe_mm', 'march_snow_frequency'])]
            for model, columns in specs:
                x = design(train, columns)
                beta = np.linalg.lstsq(x, train.runoff_af.to_numpy(), rcond=None)[0]
                record[model] = max(0., float((design(pd.DataFrame([row]), columns) @ beta).item()))
            record.update(eligible=True, training_first_year=int(train.year.min()),
                          training_last_year=int(train.year.max()),
                          climatology=float(train.runoff_af.mean()), persistence=row.prior_runoff_af)
        rows.append(record)
    return pd.DataFrame(rows)


def metrics(y, p):
    e = np.asarray(y) - np.asarray(p)
    return dict(rmse_af=float(np.sqrt(np.mean(e ** 2))), mae_af=float(np.mean(np.abs(e))))


def paired_gain(y, base, satellite, draws=10000, years=None):
    y, base, satellite = map(np.asarray, (y, base, satellite))
    if years is None:
        years = np.arange(len(y))
    years = np.asarray(years, dtype=int)
    if len(years) != len(y) or len(np.unique(years)) != len(years):
        raise ValueError('Unique calendar years required')
    calendar = np.arange(years.min(), years.max()+1)
    errors = pd.DataFrame({'base': y-base, 'satellite': y-satellite}, index=years).reindex(calendar)
    n = len(calendar)
    rng = np.random.default_rng(20260927)
    starts = rng.integers(0, n, size=(draws, (n + 1) // 2))
    indices = np.stack([starts, (starts + 1) % n], axis=-1).reshape(draws, -1)[:, :n]
    eb, es = errors.base.to_numpy()[indices], errors.satellite.to_numpy()[indices]
    nonempty = np.isfinite(eb).any(axis=1) & np.isfinite(es).any(axis=1)
    eb, es = eb[nonempty], es[nonempty]
    gains = 1 - np.sqrt(np.nanmean(es ** 2, axis=1)) / np.sqrt(np.nanmean(eb ** 2, axis=1))
    return [float(v) for v in np.quantile(gains, [.025, .975])]


def evaluate(predictions):
    held = predictions.loc[predictions.eligible]
    if held.empty:
        return dict(status='insufficient_data', evaluated_years=[])
    held = held.loc[np.isfinite(held[['runoff_af'] + list(MODELS)]).all(axis=1)]
    if held.empty:
        return dict(status='insufficient_data', evaluated_years=[])
    result = dict(status='evaluated', evaluated_years=held.year.astype(int).tolist(),
                  n_evaluation=len(held), models={m: metrics(held.runoff_af, held[m]) for m in MODELS})
    sat = result['models']['weather_flow_snow']
    comparisons = {}
    for model in ('weather_flow', 'climatology', 'persistence'):
        base = result['models'][model]
        comparisons[model] = dict(rmse_reduction=1-sat['rmse_af']/base['rmse_af'],
                                 mae_reduction=1-sat['mae_af']/base['mae_af'],
                                 rmse_reduction_ci95=paired_gain(held.runoff_af, held[model], held.weather_flow_snow, years=held.year))
    result['comparisons'] = comparisons
    primary = comparisons['weather_flow']
    result['frozen_gate_passed'] = bool(primary['rmse_reduction'] >= .05 and primary['mae_reduction'] > 0 and primary['rmse_reduction_ci95'][0] > 0)
    result['claims'] = 'Current-vintage chronological scientific forecast study only; no first-release availability or market-alpha verification.'
    result['abstentions'] = predictions.loc[~predictions.eligible, ['year', 'reason']].to_dict('records')
    return result


def evaluate_swe(predictions):
    names = ('weather_flow', 'weather_flow_snow', 'weather_flow_swe', 'weather_flow_swe_snow')
    held = predictions.loc[predictions.eligible]
    if held.empty:
        return dict(status='insufficient_data')
    held = held.loc[np.isfinite(held[['runoff_af'] + list(names)]).all(axis=1)]
    if held.empty:
        return dict(status='insufficient_data')
    scores = {m: metrics(held.runoff_af, held[m]) for m in names}
    baseline, satellite = scores['weather_flow_swe'], scores['weather_flow_swe_snow']
    return dict(n_evaluation=len(held), evaluated_years=held.year.astype(int).tolist(), models=scores,
                rmse_reduction_vs_ground_swe=1-satellite['rmse_af']/baseline['rmse_af'],
                mae_reduction_vs_ground_swe=1-satellite['mae_af']/baseline['mae_af'],
                rmse_reduction_ci95=paired_gain(held.runoff_af, held.weather_flow_swe, held.weather_flow_swe_snow, years=held.year),
                limitation='Matched training and test support; one upper-basin ground SWE station is not official basin-wide DWR forecasting skill.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT)
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--extract-only', action='store_true')
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args(); out=args.output_dir; out.mkdir(parents=True, exist_ok=True)
    if args.fetch:
        fetch(out, args.workers)
    rebuild_snow(out)
    if args.extract_only:
        return
    panel=load_panel(out); panel.to_csv(out/'annual_panel.csv', index=False)
    predictions=predict(panel); predictions.to_csv(out/'predictions.csv', index=False)
    result=evaluate(predictions)
    swe_predictions=predict(panel, with_swe=True); swe_predictions.to_csv(out/'ground_swe_predictions.csv', index=False)
    result['ground_swe_diagnostic']=evaluate_swe(swe_predictions)
    result['input_sha256']={str(p.relative_to(out)):sha256(p) for p in [out/'annual_snow.csv',out/'ground/winter_ground.csv',out/'sbf_monthly_fnf.csv',out/'protocol.json',out/'protocol_addendum.json']}
    result['code_sha256']=sha256(Path(__file__))
    (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':
    main()
