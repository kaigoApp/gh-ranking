"""グループホームの事業所ページ「くらしのまわり」に使う、病院・診療所・役所・お店の位置を集めて
data/around/poi.csv.gz に書く（列：kind, name, sub, lng, lat）。駅は data/report/stations.csv を使う。

  hospital / clinic : 国土数値情報「医療機関」（P04、2020年）。歯科だけの診療所は入れない
  office            : 国土数値情報「市町村役場等及び公的集会施設」（P05、2022年）の市役所・区役所・町村役場
  super / conv / drug : OpenStreetMap（Geofabrik の日本のデータ）の shop=supermarket・convenience・chemist、amenity=pharmacy

このコンテナからは国土数値情報・Geofabrik につながらないので、GitHub Actions（around-data.yml）で動かす。
OpenStreetMap のデータは ODbL。ページに「© OpenStreetMap contributors」を出す。
"""
import csv
import gzip
import io
import json
import pathlib
import subprocess
import sys
import urllib.request
import zipfile

import shapefile

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "around"
P04 = ["https://nlftp.mlit.go.jp/ksj/gml/data/P04/P04-20/P04-20_{:02d}_GML.zip"]
P05 = ["https://nlftp.mlit.go.jp/ksj/gml/data/P05/P05-22/P05-22_{:02d}_GML.zip",
       "https://nlftp.mlit.go.jp/ksj/gml/data/P05/P05-10/P05-10_{:02d}_GML.zip"]
OSM = "https://download.geofabrik.de/asia/japan-latest.osm.pbf"
LOG = []


def get(urls, pi):
    for u in urls:
        url = u.format(pi)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            return zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(req, timeout=300).read())), url
        except Exception as e:  # noqa: BLE001
            LOG.append(f"取れない {url} {e}")
    return None, None


def shapes(z, want):
    """zip の中の want を名前にふくむ shp を読む。(属性の辞書, 点) の並びを返す。"""
    shp = [n for n in z.namelist() if n.endswith(".shp") and want in n.split("/")[-1]]
    if not shp:
        LOG.append(f"shp がない {want} {z.namelist()[:10]}")
        return []
    base = shp[0][:-4]
    for enc in ("utf-8", "cp932"):
        try:
            r = shapefile.Reader(shp=io.BytesIO(z.read(base + ".shp")), dbf=io.BytesIO(z.read(base + ".dbf")),
                                 shx=io.BytesIO(z.read(base + ".shx")), encoding=enc)
            fields = [f[0] for f in r.fields[1:]]
            rows = [(dict(zip(fields, sr.record)), sr.shape.points) for sr in r.iterShapeRecords()]
            if rows:
                LOG.append(f"{base} {enc} {len(rows)} {rows[0][0]}")
            return rows
        except UnicodeDecodeError:
            continue
    return []


def medical(w):
    n = {}
    for pi in range(1, 48):
        z, url = get(P04, pi)
        if not z:
            continue
        for a, pts in shapes(z, "P04"):
            if not pts:
                continue
            kind = str(a.get("P04_001", "")).strip()
            name = str(a.get("P04_002", "")).strip()
            subj = " ".join(str(a.get(k, "")).strip() for k in ("P04_004", "P04_005", "P04_006") if a.get(k))
            if kind == "1":
                k = "hospital"
            elif kind == "2":
                k = "clinic"
            else:
                continue   # 歯科診療所など
            w.writerow([k, name, subj[:40], round(pts[0][0], 6), round(pts[0][1], 6)])
            n[k] = n.get(k, 0) + 1
    LOG.append(f"医療機関 {n}")


def offices(w):
    n = 0
    for pi in range(1, 48):
        z, url = get(P05, pi)
        if not z:
            continue
        for a, pts in shapes(z, "P05"):
            if not pts:
                continue
            name = next((str(v).strip() for v in a.values() if isinstance(v, str) and v.strip().endswith(("役所", "役場"))), "")
            if not name or "出張所" in name:
                continue
            w.writerow(["office", name, "", round(pts[0][0], 6), round(pts[0][1], 6)])
            n += 1
    LOG.append(f"役所 {n}")


def shops(w):
    pbf = pathlib.Path("japan.osm.pbf")
    if not pbf.exists():
        subprocess.run(["curl", "-fsSL", "-o", str(pbf), OSM], check=True)
    subprocess.run(["osmium", "tags-filter", "-O", "-o", "shops.pbf", str(pbf),
                    "nwr/shop=supermarket,convenience,chemist", "nwr/amenity=pharmacy"], check=True)
    subprocess.run(["osmium", "export", "-O", "-f", "geojsonseq", "-o", "shops.geojsonseq", "shops.pbf"], check=True)
    n = {}
    with open("shops.geojsonseq", encoding="utf-8") as f:
        for line in f:
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            ft = json.loads(line)
            t = ft.get("properties") or {}
            g = ft.get("geometry") or {}
            pts = []

            def walk(c):
                if c and isinstance(c[0], (int, float)):
                    pts.append(c)
                else:
                    for x in c:
                        walk(x)
            walk(g.get("coordinates") or [])
            if not pts:
                continue
            x = sum(p[0] for p in pts) / len(pts)
            y = sum(p[1] for p in pts) / len(pts)
            shop = t.get("shop", "")
            k = {"supermarket": "super", "convenience": "conv", "chemist": "drug"}.get(shop) or ("drug" if t.get("amenity") == "pharmacy" else "")
            if not k:
                continue
            name = t.get("name:ja") or t.get("name") or t.get("brand") or ""
            if not name:
                continue
            sub = "薬局" if t.get("amenity") == "pharmacy" and shop != "chemist" else ""
            w.writerow([k, name[:40], sub, round(x, 6), round(y, 6)])
            n[k] = n.get(k, 0) + 1
    LOG.append(f"お店 {n}")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT / "poi.csv.gz", "wt", encoding="utf-8", newline="") as gz:
        w = csv.writer(gz)
        w.writerow(["kind", "name", "sub", "lng", "lat"])
        for step in (medical, offices, shops):
            try:
                step(w)
            except Exception as e:  # noqa: BLE001
                LOG.append(f"失敗 {step.__name__} {e!r}")
    (OUT / "layout.txt").write_text("\n".join(LOG), encoding="utf-8")
    print("\n".join(LOG[-60:]))


if __name__ == "__main__":
    main()
