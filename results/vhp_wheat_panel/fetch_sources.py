from pathlib import Path
import requests,hashlib,json,gzip,io,time
from datetime import datetime,timezone
from concurrent.futures import ThreadPoolExecutor
out=Path(__file__).resolve().parent/'raw'
urls={}
for state,province,climate in [('KS',17,14),('OK',37,34)]:
 urls[f'{state}_satellite.txt']=f'https://www.star.nesdis.noaa.gov/smcd/emb/vci/VH/get_TS_admin.php?TagCropland=WHEA&country=USA&provinceID={province}&type=Mean&year1=1982&year2=2025&yearlyTag=Weekly'
 for var in ['pcp','tavg']:urls[f'{state}_{var}.csv']=f'https://www.ncei.noaa.gov/access/monitoring/climate-at-a-glance/statewide/time-series/{climate}/{var}/1/0/1981-2025.csv'
def download(pair):
 name,url=pair
 for trial in range(3):
  try:r=requests.get(url,timeout=90);r.raise_for_status();break
  except requests.RequestException:
   if trial==2:raise
   time.sleep(3)
 (out/name).write_bytes(r.content)
 print(name,len(r.content),r.text[:105].replace('\n',' '),flush=True)
 return dict(path=name,url=url,sha256=hashlib.sha256(r.content).hexdigest(),retrieved_at=datetime.now(timezone.utc).isoformat())
with ThreadPoolExecutor(max_workers=6)as pool:sources=list(pool.map(download,urls.items()))
(out/'manifest.json').write_text(json.dumps(sources,indent=2)+'\n')
url='https://www.nass.usda.gov/datasets/qs.crops_20260926.txt.gz';n=0;kept=0
with requests.get(url,stream=True,timeout=(30,180))as r:
 r.raise_for_status()
 with gzip.GzipFile(fileobj=r.raw)as z,io.TextIOWrapper(z,encoding='utf-8')as text,(out/'nass_ks_ok_winter_wheat.tsv').open('w')as target:
  header=text.readline();target.write(header);fields=header.rstrip('\n').split('\t')
  for line in text:
   n+=1
   if ('\tKANSAS\t' in line or '\tOKLAHOMA\t' in line) and '\tWHEAT\t' in line and '\tWINTER\t' in line:
    row=dict(zip(fields,line.rstrip('\n').split('\t')))
    if row.get('SOURCE_DESC')=='SURVEY' and row.get('AGG_LEVEL_DESC')=='STATE' and row.get('DOMAIN_DESC')=='TOTAL' and row.get('FREQ_DESC')=='ANNUAL' and row.get('PRODN_PRACTICE_DESC')=='ALL PRODUCTION PRACTICES' and row.get('UTIL_PRACTICE_DESC') in ('ALL UTILIZATION PRACTICES','GRAIN') and row.get('STATISTICCAT_DESC') in ('YIELD','AREA PLANTED','AREA HARVESTED','PRODUCTION') and int(row['YEAR'])>=1981:
     target.write(line);kept+=1
p=out/'nass_ks_ok_winter_wheat.tsv'
sources.append(dict(path=p.name,url=url,sha256=hashlib.sha256(p.read_bytes()).hexdigest(),retrieved_at=datetime.now(timezone.utc).isoformat(),filter='Kansas andOklahoma winter-wheat survey annual state total allproductionpractices acreage/production/yield,year>=1981. Full matchingrowsretained.',bulk_rows_scanned=n,rows_kept=kept))
(out/'manifest.json').write_text(json.dumps(sources,indent=2)+'\n');print('Done',n,kept,flush=True)
