"""Explicit network refresh; run only after a frozen forecast protocol exists."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import h5py
import numpy as np
import pandas as pd
import requests
import netCDF4

OUT = Path(__file__).resolve().parent
BASE = 'https://psl.noaa.gov/thredds/ncss/grid/Datasets/'
REGION = {'south': -5, 'north': 5, 'west': 34, 'east': 42}
BOXES = {'iod_west': {'south': -10, 'north': 10, 'west': 50, 'east': 70},
         'iod_east': {'south': -10, 'north': 0, 'west': 90, 'east': 110},
         'nino34': {'south': -5, 'north': 5, 'west': 190, 'east': 240}}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download(spec):
    name, url, params = spec
    path = OUT / name
    sidecar = path.with_suffix(path.suffix + '.source.json')
    if not path.exists():
        if params is not None:
            # NOAA NCSS failed with server-side disk-full errors. OPeNDAP
            # reads the same archive without generating a server temp file.
            dap = url.replace('/ncss/grid/', '/dodsC/')
            with netCDF4.Dataset(dap) as remote:
                lat, lon = remote.variables['lat'][:], remote.variables['lon'][:]
                times = remote.variables['time'][:]
                units = remote.variables['time'].units
                unit, origin = units.split(' since ')
                dates = pd.Timestamp(origin) + pd.to_timedelta(times, unit={'days': 'D', 'hours': 'h'}[unit])
                ti = np.flatnonzero((dates >= pd.Timestamp(params['time_start']).tz_localize(None)) &
                                    (dates <= pd.Timestamp(params['time_end']).tz_localize(None)))
                yi = np.flatnonzero((lat >= params['south']) & (lat <= params['north']))
                xi = np.flatnonzero((lon >= params['west']) & (lon <= params['east']))
                ts, ys, xs = slice(ti[0], ti[-1] + 1, params['timeStride']), slice(yi[0], yi[-1] + 1), slice(xi[0], xi[-1] + 1)
                values = np.ma.filled(remote.variables[params['var']][ts, ys, xs], np.nan)
                with h5py.File(path, 'w') as local:
                    local.create_dataset('lat', data=lat[ys])
                    local.create_dataset('lon', data=lon[xs])
                    local.create_dataset('time', data=times[ts]).attrs['units'] = units
                    local.create_dataset(params['var'], data=values, compression='gzip')
                source = {'path': name, 'url': dap, 'request': params,
                          'indices': {'time': [int(ti[0]), params['timeStride'], int(ti[-1])],
                                      'lat': [int(yi[0]), int(yi[-1])], 'lon': [int(xi[0]), int(xi[-1])]},
                          'retrieved_at_utc': datetime.now(timezone.utc).isoformat(), 'sha256': digest(path),
                          'storage': 'Lossless numeric OPeNDAP subset saved in local HDF5; same source archive as NCSS.',
                          'source_title': getattr(remote, 'title', None), 'source_history': getattr(remote, 'history', None)}
        else:
            response = requests.get(url, timeout=180)
            response.raise_for_status()
            path.write_bytes(response.content)
            source = {'path': name, 'url': response.url, 'retrieved_at_utc': datetime.now(timezone.utc).isoformat(),
                      'http_last_modified': response.headers.get('Last-Modified'), 'sha256': digest(path)}
        sidecar.write_text(json.dumps(source, indent=2) + '\n')
    source = json.loads(sidecar.read_text())
    if digest(path) != source['sha256']:
        raise ValueError('Cached source checksum mismatch')
    return source


def scalar(value):
    item = np.asarray(value).ravel()[0]
    return item.decode() if isinstance(item, bytes) else str(item)


def read_region(path, variable, bounds, allow_missing=False):
    with h5py.File(path) as data:
        lat, lon = data['lat'][:], data['lon'][:]
        keep_lat = (lat >= bounds['south']) & (lat <= bounds['north'])
        keep_lon = (lon >= bounds['west']) & (lon <= bounds['east'])
        values = data[variable][:].astype(float)[:, keep_lat][:, :, keep_lon]
        if variable == 'sst':
            values[(values < -3) | (values > 45)] = np.nan
        else:
            values[(values < 0) | (values > 10000)] = np.nan
        weights = np.cos(np.deg2rad(lat[keep_lat]))[None, :, None]
        valid = np.isfinite(values)
        denominators = (weights * valid).sum(axis=(1, 2))
        if (denominators <= 0).any() and not allow_missing:
            raise ValueError(f'No valid regional cells: {path}')
        means = np.divide(np.nansum(values * weights, axis=(1, 2)), denominators,
                          out=np.full(len(denominators), np.nan), where=denominators > 0)
        units = scalar(data['time'].attrs['units'])
        unit, origin = units.split(' since ')
        unit = {'days': 'D', 'hours': 'h'}.get(unit)
        if unit is None:
            raise ValueError('Unexpected time units')
        dates = pd.Timestamp(origin) + pd.to_timedelta(data['time'][:], unit=unit)
        return pd.DataFrame({'date': dates, 'value': means, 'valid_cells': valid.sum(axis=(1, 2))})


def main():
    assert (OUT / 'protocol.json').exists(), 'Freeze before retrieving values'
    specifications = []
    for name, bounds in BOXES.items():
        specifications.append((f'inputs/oisst_september_{name}.nc', BASE + 'noaa.oisst.v2.highres/sst.mon.mean.nc',
            {'var': 'sst', **bounds, 'horizStride': 1, 'time_start': '1982-09-01T00:00:00Z',
             'time_end': '2025-09-01T00:00:00Z', 'timeStride': 12, 'accept': 'netcdf4'}))
    specifications.append(('inputs/gpcc_region_monthly.nc', BASE + 'gpcc/monitor/precip.monitor.mon.total.1x1.v2020.nc',
        {'var': 'precip', **REGION, 'horizStride': 1, 'time_start': '1982-01-01T00:00:00Z',
         'time_end': '2025-12-01T00:00:00Z', 'timeStride': 1, 'accept': 'netcdf4'}))
    for year in range(1982, 2026):
        specifications.append((f'inputs/cpc_augsep_{year}.nc', BASE + f'cpc_global_precip/precip.{year}.nc',
            {'var': 'precip', **REGION, 'horizStride': 1, 'time_start': f'{year}-08-01T00:00:00Z',
             'time_end': f'{year}-09-30T23:59:59Z', 'timeStride': 1, 'accept': 'netcdf4'}))
    metadata = {'gpcc_product.html': 'https://opendata.dwd.de/climate_environment/GPCC/html/gpcc_monitoring_v2020_doi_download.html',
                'cpc_gauge_readme.txt': 'https://ftp.cpc.ncep.noaa.gov/precip/CPC_UNI_PRCP/GAUGE_GLB/DOCU/PRCP_CU_GAUGE_V1.0GLB_0.50deg_README.txt',
                'oisst_metadata.html': 'https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncdc:C01606',
                'physical_mechanism.html': 'https://www.metoffice.gov.uk/services/government/contingency-planners/seasonal-forecasts-and-climate-drivers-resources'}
    specifications += [(f'metadata/{name}', url, None) for name, url in metadata.items()]
    # libnetcdf is not thread safe. The small requested regions are read serially.
    manifest = [download(specification) for specification in specifications]
    sst = None
    for name, bounds in BOXES.items():
        frame = read_region(OUT / f'inputs/oisst_september_{name}.nc', 'sst', bounds)
        if len(frame) != 44 or set(frame.date.dt.year) != set(range(1982, 2026)) or not frame.date.dt.month.eq(9).all():
            raise ValueError('SST September-only source coverage mismatch')
        frame['year'] = frame.date.dt.year
        frame = frame.rename(columns={'value': f'{name}_sst_c', 'valid_cells': f'{name}_cells'})
        sst = frame if sst is None else sst.merge(frame.drop(columns='date'), on='year', validate='one_to_one')
    sst['iod_sst_difference_c'] = sst.iod_west_sst_c - sst.iod_east_sst_c
    sst.to_csv(OUT / 'inputs/september_sst.csv', index=False)
    # This originally selected GPCC mirror is retained for audit only: despite
    # time coordinates through2025, its values stop after April2021. Direct
    # official v2022 targets are rebuilt separately by fetch_gpcc.py.
    ground = []
    for year in range(1982, 2026):
        daily = read_region(OUT / f'inputs/cpc_augsep_{year}.nc', 'precip', REGION, allow_missing=True)
        expected = pd.date_range(f'{year}-08-01', f'{year}-09-30')
        if not np.array_equal(daily.date.to_numpy(), expected.to_numpy()):
            raise ValueError('Incomplete current weather observations')
        for month in (8, 9):
            rows = daily.loc[daily.date.dt.month == month]
            ground.append({'date': pd.Timestamp(year, month, 1), 'precip_mm': rows.value.sum(min_count=len(rows)),
                           'n_days': len(rows), 'n_valid_days': int(rows.value.notna().sum()),
                           'minimum_valid_cells': int(rows.valid_cells.min()),
                           'maximum_valid_cells': int(rows.valid_cells.max())})
    pd.DataFrame(ground).to_csv(OUT / 'inputs/cpc_augsep_monthly.csv', index=False)
    for name in ('september_sst.csv', 'cpc_augsep_monthly.csv'):
        manifest.append({'path': f'inputs/{name}', 'sha256': digest(OUT / 'inputs' / name),
                         'method': 'Fixed coordinate-center bounds; cosine-latitude weighting over valid land/ocean cells; month sums as specified in protocol.'})
    (OUT / 'source_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('Retrieved fixed inputs:44 SST years and88 CPC monthly controls; GPCC targets retrieved separately; no fit performed.')

if __name__ == '__main__':
    main()
