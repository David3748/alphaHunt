from pathlib import Path
import requests,hashlib,json,csv,gzip,io,time
from datetime import datetime,timezone
from concurrent.futures import ThreadPoolExecutor
out=Path('results/vhp_wheat/raw')
urls={
'noaa_texas_wheat_weekly.txt':'https://www.star.nesdis.noaa.gov/smcd/emb/vci/VH/get_TS_admin.php?TagCropland=WHEA&country=USA&provinceID=44&type=Mean&year1=1982&year2=2025&yearlyTag=Weekly',
'weather_pcp.csv':'https://www.ncei.noaa.gov/access/monitoring/climate-at-a-glance/statewide/time-series/41/pcp/1/0/1981-2025.csv',
'weather_tavg.csv':'https://www.ncei.noaa.gov/access/monitoring/climate-at-a-glance/statewide/time-series/41/tavg/1/0/1981-2025.csv',
'smoothing.pdf':'https://www.star.nesdis.noaa.gov/jpss/documents/AMM/NPP/VIIRS-VH_ARR_Prov.pdf',
'noaa_background.html':'https://www.star.nesdis.noaa.gov/smcd/emb/vci/VH/VH-Syst_10ap30.php',
'nass_2024_review.pdf':'https://www.nass.usda.gov/Statistics_by_State/Texas/Publications/Current_News_Release/2024_Rls/tx-wheat-review-2024.pdf'}
def download(x):
 name,url=x;r=requests.get(url,timeout=90);r.raise_for_status();(out/name).write_bytes(r.content)
 return dict(path=name,url=url,sha256=hashlib.sha256(r.content).hexdigest(),retrieved_at=datetime.now(timezone.utc).isoformat(),bytes=len(r.content))
with ThreadPoolExecutor(max_workers=6)as pool:sources=list(pool.map(download,urls.items()))
(out/'manifest.json').write_text(json.dumps(sources,indent=2)+'\n');print('Small sources downloaded',flush=True)
url='https://www.nass.usda.gov/datasets/qs.crops_20260926.txt.gz'
start=time.time();n=0;kept=0
with requests.get(url,stream=True,timeout=(30,180))as r:
 r.raise_for_status()
 with gzip.GzipFile(fileobj=r.raw)as z,io.TextIOWrapper(z,encoding='utf-8')as text,(out/'nass_texas_winter_wheat.tsv').open('w')as target:
  header=text.readline();target.write(header);fields=header.rstrip('\n').split('\t')
  for line in text:
   n+=1
   if '\tTEXAS\t' in line and '\tWHEAT\t' in line and '\tWINTER\t' in line:
    row=dict(zip(fields,line.rstrip('\n').split('\t')))
    if row.get('SOURCE_DESC')=='SURVEY' and row.get('AGG_LEVEL_DESC')=='STATE' and row.get('DOMAIN_DESC')=='TOTAL' and row.get('FREQ_DESC')=='ANNUAL' and row.get('PRODN_PRACTICE_DESC')=='ALL PRODUCTION PRACTICES' and row.get('UTIL_PRACTICE_DESC') in ('ALL UTILIZATION PRACTICES','GRAIN') and row.get('STATISTICCAT_DESC') in ('YIELD','AREA PLANTED','AREA HARVESTED','PRODUCTION') and int(row['YEAR'])>=1981:
     target.write(line);kept+=1
   if n%5000000==0:print(f'Bulk scanned {n} rows, kept {kept}, elapsed{time.time()-start:.1f}s',flush=True)
p=out/'nass_texas_winter_wheat.tsv'
sources.append(dict(path=p.name,url=url,sha256=hashlib.sha256(p.read_bytes()).hexdigest(),retrieved_at=datetime.now(timezone.utc).isoformat(),filter='Texas state winter-wheat survey annual total, yield/production/planted/harvested, all production practices, 1981 onward. Full raw matching rows retained.',bulk_rows_scanned=n,rows_kept=kept,bulk_bytes_retained=False))
(out/'manifest.json').write_text(json.dumps(sources,indent=2)+'\n');print(f'Done: {n} scanned {kept} kept {time.time()-start:.1f}s',flush=True)
