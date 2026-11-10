"""Build a place directory; it is not a freight dataset."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
from urllib.request import urlopen

SOURCE = "https://raw.githubusercontent.com/hflabs/city/ae661bffe572880472249097c9b29c42b09650ea/city.csv"
parser = argparse.ArgumentParser()
parser.add_argument("--source", type=Path)
args = parser.parse_args()
raw = args.source.read_bytes() if args.source else urlopen(SOURCE, timeout=30).read()
rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
regions = {}
cities = []
for row in rows:
    region_id = row["kladr_id"][:2]
    label = {"обл": f'{row["region"]} область', "край": f'{row["region"]} край',
             "Респ": f'Республика {row["region"]}', "АО": f'{row["region"]} автономный округ',
             "Аобл": f'{row["region"]} автономная область'}.get(row["region_type"], row["region"])
    original_label = label
    label = {"42": "Кемеровская область — Кузбасс", "86": "Ханты-Мансийский автономный округ — Югра",
             "14": "Республика Саха (Якутия)", "09": "Карачаево-Черкесская Республика",
             "07": "Кабардино-Балкарская Республика", "18": "Удмуртская Республика",
             "20": "Чеченская Республика", "21": "Чувашская Республика — Чувашия",
             "15": "Республика Северная Осетия — Алания"}.get(region_id, label)
    regions[region_id] = {"id": region_id, "label": label, "originalLabel": original_label}
    name = row["city"] or row["settlement"] or row["region"]
    cities.append({"id": row["fias_id"] or row["kladr_id"], "regionId": region_id,
                   "label": name, "area": row["area"],
                   "lat": float(row["geo_lat"]), "lon": float(row["geo_lon"])})
output = {"source": {"source_id": "hflabs_city_2021", "organization": "HFLabs / DaData",
          "url": SOURCE, "version_date": "2021-10-11", "retrieved_at": "2026-11-10",
          "sha256": hashlib.sha256(raw).hexdigest(), "license": "CC-BY-SA-4.0",
          "definition": "Справочник городов, не наблюдения перевозок",
          "limitations": "Снимок 2021 года, возможны пропуски и устаревшие названия"},
          "regions": sorted(regions.values(), key=lambda r: r["label"]),
          "cities": sorted(cities, key=lambda c: (c["label"], c["regionId"], c["id"]))}
target = Path(__file__).resolve().parents[1] / "src/geography-catalog.json"
target.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
print(f"Regions: {len(regions)}, cities: {len(cities)}; SHA-256: {output['source']['sha256']}")
