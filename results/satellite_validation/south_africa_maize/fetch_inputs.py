"""Fetch frozen SouthAfrica source features and independent FAOSTAT labels."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,gzip,csv,io,zipfile,importlib.util,shutil
import numpy as np
import pandas as pd
import requests

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
EA=ROOT/'results/satellite_validation/east_africa'
REGION={'south':-30,'north':-24,'west':25,'east':31}


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_ground(path):
 import h5py
 with h5py.File(path) as data:
  values=data['precip'][:].astype(float);lat=data['lat'][:]
  values[(values<0)|(values>10000)]=np.nan
  weights=np.cos(np.deg2rad(lat))[None,:,None];valid=np.isfinite(values)
  denominator=(weights*valid).sum(axis=(1,2))
  means=np.divide(np.nansum(values*weights,axis=(1,2)),denominator,out=np.full(len(values),np.nan),where=denominator>0)
  units=data['time'].attrs['units'];units=units.decode() if isinstance(units,bytes) else str(units)
  unit,origin=units.split(' since ');dates=pd.Timestamp(origin)+pd.to_timedelta(data['time'][:],unit={'days':'D','hours':'h'}[unit])
  return pd.DataFrame(dict(date=dates,value=means,valid_cells=valid.sum(axis=(1,2))))

def main():
 assert (OUT/'protocol.json').exists() and (OUT/'protocol_addendum.json').exists()
 # Reuse only audited read/subset helpers, not another candidate's labels/models.
 spec=importlib.util.spec_from_file_location('ea_sources',EA/'fetch_inputs.py');helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
 helper.OUT=OUT
 (OUT/'inputs').mkdir(exist_ok=True);(OUT/'metadata').mkdir(exist_ok=True)
 manifest=[]
 for name in ['oisst_september_nino34.nc','oisst_september_nino34.nc.source.json']:
  shutil.copyfile(EA/'inputs'/name,OUT/'inputs'/name)
 record=json.loads((OUT/'inputs/oisst_september_nino34.nc.source.json').read_text());manifest.append(record)
 sst=helper.read_region(OUT/'inputs/oisst_september_nino34.nc','sst',{'south':-5,'north':5,'west':190,'east':240})
 sst.rename(columns={'value':'september_nino34_sst_c'}).to_csv(OUT/'inputs/september_sst.csv',index=False)
 ground=[]
 for year in range(1982,2025):
  source=helper.download((f'inputs/cpc_augsep_{year}.nc',helper.BASE+f'cpc_global_precip/precip.{year}.nc',
    {'var':'precip',**REGION,'horizStride':1,'time_start':f'{year}-08-01T00:00:00Z','time_end':f'{year}-09-30T23:59:59Z','timeStride':1,'accept':'netcdf4'}))
  manifest.append(source)
  daily=read_ground(OUT/f'inputs/cpc_augsep_{year}.nc')
  if not np.array_equal(daily.date.to_numpy(),pd.date_range(f'{year}-08-01',f'{year}-09-30').to_numpy()):raise ValueError('Missingcalendarweatherday')
  import h5py
  with h5py.File(OUT/f'inputs/cpc_augsep_{year}.nc')as f:ncells=len(f['lat'])*len(f['lon'])
  for month in [8,9]:
   part=daily[daily.date.dt.month.eq(month)];good=part.valid_cells.ge(.9*ncells).all()
   ground.append(dict(year=year,month=month,precip_mm=float(part.value.sum()) if good else np.nan,n_days=len(part),minimum_valid_cells=int(part.valid_cells.min()),grid_cells=ncells,eligible=bool(good)))
  if year%5==0:print('CPC',year,flush=True)
 pd.DataFrame(ground).to_csv(OUT/'inputs/ground_augsep.csv',index=False)
 bulk=ROOT/'work/south_africa_maize/faostat.zip'
 url='https://bulks-faostat.fao.org/production/Production_Crops_Livestock_E_All_Data_(Normalized).zip'
 if not bulk.exists():
  r=requests.get(url,timeout=180);r.raise_for_status();bulk.parent.mkdir(parents=True,exist_ok=True);bulk.write_bytes(r.content)
 with zipfile.ZipFile(bulk)as archive:
  names=[n for n in archive.namelist() if n.endswith('.csv') and 'All_Data' in n]
  if len(names)!=1:raise ValueError('UnexpectedFAOarchive')
  with archive.open(names[0])as raw,io.TextIOWrapper(raw,encoding='utf-8-sig',errors='replace')as text,(OUT/'inputs/faostat_south_africa_maize.csv').open('w')as target:
   reader=csv.DictReader(text);writer=csv.DictWriter(target,fieldnames=reader.fieldnames);writer.writeheader();count=0
   for row in reader:
    if row.get('Area')=='South Africa' and row.get('Item') in ['Maize (corn)','Maize'] and row.get('Element') in ['Production','Area harvested','Yield'] and int(row['Year'])>=1980:
     writer.writerow(row);count+=1
 print('FAOSTATmatchingrows',count,flush=True)
 manifest.append(dict(path='inputs/faostat_south_africa_maize.csv',url=url,bulk_sha256=digest(bulk),archive_member=names[0],sha256=digest(OUT/'inputs/faostat_south_africa_maize.csv'),retrieved_at_utc=datetime.now(timezone.utc).isoformat(),filter='SouthAfrica,Maizecorn,production/harvestedarea/yield,1980onward;fullsourcecolumnspreserved'))
 docs={'fao_calendar.pdf':'https://files-faostat.fao.org/production/QCL/QCL_methodology_e.pdf','fao_release.html':'https://www.fao.org/statistics/events/events-detail/agricultural-production-statistics-2010-2025.-december-2025-update/','south_africa_2024_september.pdf':'https://www.namc.co.za/wp-content/uploads/2024/10/September-2024-SASDE-report.-1-Oct-2024-FINAL.pdf'}
 for name,url in docs.items():manifest.append(helper.download((f'metadata/{name}',url,None)))
 for name in ['oisst_metadata.html','cpc_gauge_readme.txt']:
  shutil.copyfile(EA/'metadata'/name,OUT/'metadata'/name)
  meta=json.loads((EA/'metadata'/f'{name}.source.json').read_text());manifest.append(meta)
 for name in ['inputs/september_sst.csv','inputs/ground_augsep.csv']:
  manifest.append(dict(path=name,sha256=digest(OUT/name),method='Fixedboxcosinelatitudeaverage;SeptSSTorallAug/Septdailygaugetotals,>=90%daygridcoverage;nooutcomesusedinextraction'))
 manifest.append(dict(path='fetch_inputs.py',sha256=digest(Path(__file__)),helper_path=str((EA/'fetch_inputs.py').relative_to(ROOT)),helper_sha256=digest(EA/'fetch_inputs.py')))
 (OUT/'source_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 print('Fetcheddone;no model or outcomefit',flush=True)

if __name__=='__main__':main()
