"""地域別レポート用に、国土数値情報の「学校」（P29、2021年）と「鉄道」（N02、2023年）から
小学校・特別支援学校・駅の位置を取り出して data/report/schools.csv と stations.csv に書く。
このコンテナからは国土数値情報のサイトにつながらないので、GitHub Actions（report-data.yml）で動かす。
"""
import csv
import io
import pathlib
import sys
import urllib.request
import zipfile

import shapefile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from regional_report import TARGETS  # noqa: E402
from services import PREFS  # noqa: E402

P29 = "https://nlftp.mlit.go.jp/ksj/gml/data/P29/P29-21/P29-21_{:02d}_GML.zip"
N02 = "https://nlftp.mlit.go.jp/ksj/gml/data/N02/N02-23/N02-23_GML.zip"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(req, timeout=300).read()))


def shapes(z, want):
    """zip の中の want を名前にふくむ shp を読む。(属性の辞書, 点の並び) を返す。"""
    names = z.namelist()
    shp = [n for n in names if n.endswith(".shp") and want in n]
    if not shp:
        raise SystemExit(f"shp がない: {want} {names[:20]}")
    base = shp[0][:-4]
    for enc in ("utf-8", "cp932"):
        try:
            r = shapefile.Reader(shp=io.BytesIO(z.read(base + ".shp")), dbf=io.BytesIO(z.read(base + ".dbf")),
                                 shx=io.BytesIO(z.read(base + ".shx")), encoding=enc)
            fields = [f[0] for f in r.fields[1:]]
            out = [(dict(zip(fields, sr.record)), sr.shape.points) for sr in r.iterShapeRecords()]
            return fields, out
        except UnicodeDecodeError:
            continue
    raise SystemExit("文字コードが読めない")


def main():
    out = ROOT / "data" / "report"
    log = []
    with open(out / "schools.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["pref", "kind", "name", "lng", "lat"])
        for pi in sorted({PREFS.index(p) + 1 for p, _ in TARGETS}):
            fields, rows = shapes(get(P29.format(pi)), "P29")
            log.append(f"P29 {pi:02d} {fields} {rows[0][0] if rows else ''}")
            for a, pts in rows:
                name = next((str(v) for v in a.values() if isinstance(v, str) and "学校" in v), "")
                kind = "特別支援学校" if "特別支援" in name or "支援学校" in name or "養護学校" in name else \
                    "小学校" if name.endswith("小学校") or "小学校" in name and "中学校" not in name else ""
                if kind and pts:
                    w.writerow([PREFS[pi - 1], kind, name, round(pts[0][0], 6), round(pts[0][1], 6)])
    fields, rows = shapes(get(N02), "Station")
    log.append(f"N02 {fields} {rows[0][0] if rows else ''}")
    seen = set()
    with open(out / "stations.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "line", "lng", "lat"])
        for a, pts in rows:
            name, line = a.get("N02_005", ""), a.get("N02_003", "")
            if not pts:
                continue
            x = sum(p[0] for p in pts) / len(pts)
            y = sum(p[1] for p in pts) / len(pts)
            k = (name, round(x, 3), round(y, 3))
            if k in seen:
                continue
            seen.add(k)
            w.writerow([name, line, round(x, 6), round(y, 6)])
    (out / "places_layout.txt").write_text("\n".join(log), encoding="utf-8")


if __name__ == "__main__":
    main()
