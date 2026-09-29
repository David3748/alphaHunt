"""Extract only prespecified countries/maize from frozen officialFAObulk."""
from pathlib import Path
from datetime import datetime,timezone
import csv,io,zipfile,hashlib,json
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[2]
assert (OUT/'protocol.json').exists()
bulk=ROOT/'work/south_africa_maize/faostat.zip'
with zipfile.ZipFile(bulk)as archive:
 name=[n for n in archive.namelist() if n.endswith('.csv') and 'All_Data' in n][0]
 with archive.open(name)as raw,io.TextIOWrapper(raw,encoding='utf-8-sig',errors='replace')as source,(OUT/'inputs/faostat_maize.csv').open('w')as target:
  reader=csv.DictReader(source);writer=csv.DictWriter(target,fieldnames=reader.fieldnames);writer.writeheader();counts={}
  for row in reader:
   if row.get('Area') in ['South Africa','Zambia','Zimbabwe'] and row.get('Item') in ['Maize (corn)','Maize'] and row.get('Element') in ['Production','Area harvested','Yield'] and int(row['Year'])>=1980:
    writer.writerow(row);counts[row['Area']]=counts.get(row['Area'],0)+1
manifest=dict(url='https://bulks-faostat.fao.org/production/Production_Crops_Livestock_E_All_Data_(Normalized).zip',bulk_sha256=hashlib.sha256(bulk.read_bytes()).hexdigest(),archive_member=name,selection='SouthAfrica,Zambia,Zimbabwe;maize;production,harvestedarea,yield;1980onward',rows_per_country=counts,extracted_at_utc=datetime.now(timezone.utc).isoformat(),filtered_sha256=hashlib.sha256((OUT/'inputs/faostat_maize.csv').read_bytes()).hexdigest(),protocol_sha256=hashlib.sha256((OUT/'protocol.json').read_bytes()).hexdigest())
(OUT/'inputs/labels_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print(counts)
