"""Direct official GPCC v2022 targets; repair a stale PSL mirror before first fit."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import gzip
import hashlib
import io
import json
import h5py
import numpy as np
import pandas as pd
import requests
from scipy.io import netcdf_file

OUT = Path(__file__).resolve().parent
SCRATCH = OUT.parents[2] / 'work/east_africa_raw'


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch(pair):
    year, month = pair
    name=f'monitoring_v2022_10_{year}_{month:02d}.nc.gz'
    url=f'https://opendata.dwd.de/climate_environment/GPCC/monitoring_v2022/{year}/{name}'
    path=SCRATCH/name
    sidecar=path.with_suffix(path.suffix+'.source.json')
    if not path.exists() or not sidecar.exists():
        response=requests.get(url,timeout=90)
        response.raise_for_status()
        path.write_bytes(response.content)
        metadata={'url':url,'raw_download_sha256':file_hash(path),'retrieved_at_utc':datetime.now(timezone.utc).isoformat(),
                  'http_last_modified':response.headers.get('Last-Modified')}
        sidecar.write_text(json.dumps(metadata,indent=2)+'\n')
    metadata=json.loads(sidecar.read_text())
    if file_hash(path)!=metadata['raw_download_sha256']:
        raise ValueError('Cached GPCC download changed')
    with netcdf_file(io.BytesIO(gzip.decompress(path.read_bytes())),mmap=False) as remote:
        lat,lon=remote.variables['lat'][:].copy(),remote.variables['lon'][:].copy()
        yi=(lat>=-5)&(lat<=5);xi=(lon>=34)&(lon<=42)
        if remote.variables['p'].units!=b'mm/month' or remote.variables['s'].units!=b'gauges per grid':
            raise ValueError('Unexpected GPCC variable units')
        date=pd.to_datetime(str(int(remote.variables['time'][:][0])),format='%Y%m%d')
        if date != pd.Timestamp(year,month,1):
            raise ValueError('GPCC internal date does not match request')
        p=remote.variables['p'][:].copy()[0,yi][:,xi].astype(float)
        s=remote.variables['s'][:].copy()[0,yi][:,xi].astype(float)
        p[p<0]=np.nan;s[s<0]=np.nan
    metadata['date']=str(date.date())
    return metadata,date,lat[yi],lon[xi],p,s


def main():
    assert (OUT/'prefit_gpcc_source_repair.json').exists()
    SCRATCH.mkdir(parents=True,exist_ok=True)
    pairs=[(year,month) for year in range(1982,2026) for month in (11,12)]
    with ThreadPoolExecutor(max_workers=4) as executor:
        results=list(executor.map(fetch,pairs))
    lat,lon=results[0][2:4]
    if not all(np.array_equal(lat,row[2]) and np.array_equal(lon,row[3]) for row in results):
        raise ValueError('GPCC grid changed')
    rain=np.stack([row[4] for row in results]);gauges=np.stack([row[5] for row in results])
    weights=np.cos(np.deg2rad(lat))[None,:,None]
    valid=np.isfinite(rain)
    denominator=(valid*weights).sum(axis=(1,2))
    if (denominator<=0).any():
        raise ValueError('No valid target cells')
    mean=np.nansum(rain*weights,axis=(1,2))/denominator
    frame=pd.DataFrame({'date':[row[1] for row in results],'precip_mm':mean,'valid_cells':valid.sum(axis=(1,2)),
                        'gauges_within_region':np.nansum(gauges,axis=(1,2)),
                        'cells_with_gauge':(gauges>0).sum(axis=(1,2))})
    frame.to_csv(OUT/'inputs/gpcc_v2022_monthly.csv',index=False)
    path=OUT/'inputs/gpcc_v2022_regions.h5'
    with h5py.File(path,'w') as snapshot:
        snapshot.create_dataset('latitude',data=lat).attrs['units']='degrees_north'
        snapshot.create_dataset('longitude',data=lon).attrs['units']='degrees_east'
        snapshot.create_dataset('date_yyyymmdd',data=np.array([int(row[1].strftime('%Y%m%d')) for row in results]))
        snapshot.create_dataset('precipitation',data=rain,compression='gzip').attrs['units']='mm/month'
        snapshot.create_dataset('gauges',data=gauges,compression='gzip').attrs['units']='gauges per grid'
    manifest=[{'path':'inputs/gpcc_v2022_regions.h5','sha256':file_hash(path),'storage':'Lossless fixed regional subsets of original GPCC p and s variables; global archives only in scratch; raw source hashes retained.','original_sources':[row[0] for row in results]},
              {'path':'inputs/gpcc_v2022_monthly.csv','sha256':file_hash(OUT/'inputs/gpcc_v2022_monthly.csv'),'method':'Cosine-latitude area weights of fixed land rectangle, no missing-month zero fill; Nov/Dec only.'}]
    (OUT/'gpcc_source_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('Direct GPCC:88target months1982–2025; validcells',int(frame.valid_cells.min()),int(frame.valid_cells.max()),
          'regionalgaugecounts',int(frame.gauges_within_region.min()),int(frame.gauges_within_region.max()))

if __name__=='__main__':
    main()
