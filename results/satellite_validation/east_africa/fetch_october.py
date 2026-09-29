"""Retrieve pre-fit added Oct1-9 gauge controls, separate processes for libnetcdf."""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import json
import pandas as pd
from fetch_inputs import BASE, OUT, REGION, download, read_region, digest


def fetch(year):
    source = download((f'inputs/cpc_october1to9_{year}.nc', BASE + f'cpc_global_precip/precip.{year}.nc',
        {'var':'precip', **REGION, 'horizStride':1, 'time_start':f'{year}-10-01T00:00:00Z',
         'time_end':f'{year}-10-09T23:59:59Z', 'timeStride':1, 'accept':'netcdf4'}))
    daily = read_region(OUT / source['path'], 'precip', REGION)
    expected = pd.date_range(f'{year}-10-01', f'{year}-10-09')
    if daily.date.tolist() != expected.tolist():
        raise ValueError('Incomplete or future October controls')
    row = {'date':pd.Timestamp(year,10,1), 'precip_mm':daily.value.sum(),'n_days':9,
           'minimum_valid_cells':int(daily.valid_cells.min()),'maximum_valid_cells':int(daily.valid_cells.max())}
    return source,row

if __name__ == '__main__':
    assert (OUT / 'prefit_information_set_amendment.json').exists()
    with ProcessPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(fetch,range(1982,2026)))
    pd.DataFrame([row for _,row in results]).to_csv(OUT / 'inputs/cpc_october_controls.csv',index=False)
    manifest=[source for source,_ in results]
    manifest.append({'path':'inputs/cpc_october_controls.csv','sha256':digest(OUT / 'inputs/cpc_october_controls.csv'),
                     'method':'Cosine-latitude weighted mean of fixed Kenya-centered land rectangle; sum October1-9only; exactly9dailyobservations required.'})
    (OUT / 'october_source_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('Retrieved44 fixed early-October gauge totals; no fit performed.')
