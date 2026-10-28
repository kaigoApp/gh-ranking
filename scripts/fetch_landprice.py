"""地域別レポート用に、国土数値情報の「地価公示」（L01）から、対象の市区の標準地（住宅地・商業地）の
価格と位置を取り出して data/report/landprice.csv に書く。候補地ごとの家賃の高い安いの目安に使う。
このコンテナからは国土数値情報のサイトにつながらないので、GitHub Actions（report-data.yml）で動かす。

  年, 用途（住宅地・商業地）, 価格（円/㎡）, 経度, 緯度, 最寄り駅, 駅までの距離（m）, 所在
"""
import csv
import io
import pathlib
import sys
import urllib.error
import urllib.request
import zipfile

import shapefile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from regional_report import TARGETS  # noqa: E402
from services import PREFS  # noqa: E402

URL = "https://nlftp.mlit.go.jp/ksj/gml/data/L01/L01-{yy}/L01-{yy}_{pi:02d}_GML.zip"
USE = {"000": "住宅地", "005": "商業地"}


def get(pi):
    for yy in (26, 25):
        try:
            req = urllib.request.Request(URL.format(yy=yy, pi=pi), headers={"User-Agent": "Mozilla/5.0"})
            return 2000 + yy, zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(req, timeout=300).read()))
        except urllib.error.HTTPError:
            continue
    raise SystemExit(f"地価公示が取れない: {pi:02d}")


def main():
    out = ROOT / "data" / "report"
    log, n = [], 0
    want = {}
    for pref, city in TARGETS:
        want.setdefault(PREFS.index(pref) + 1, []).append(city)
    with open(out / "landprice.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["year", "use", "price", "lng", "lat", "station", "station_m", "addr"])
        for pi, cities in sorted(want.items()):
            year, z = get(pi)
            shp = next(x for x in z.namelist() if x.endswith(".shp"))
            base = shp[:-4]
            r = None
            for enc in ("utf-8", "cp932"):
                try:
                    r = shapefile.Reader(shp=io.BytesIO(z.read(base + ".shp")), dbf=io.BytesIO(z.read(base + ".dbf")),
                                         shx=io.BytesIO(z.read(base + ".shx")), encoding=enc)
                    rows = [(dict(zip([fl[0] for fl in r.fields[1:]], sr.record)), sr.shape.points) for sr in r.iterShapeRecords()]
                    break
                except UnicodeDecodeError:
                    continue
            if len(log) < 2:
                log.append(f"{pi:02d} {year} {shp}\n{rows[0][0]}")
            for a, pts in rows:
                use = str(a.get("L01_002", "")).zfill(3)
                addr = str(a.get("L01_025", ""))
                if use not in USE or not pts or not any(c in addr for c in cities):
                    continue
                price = int(a.get("L01_008") or 0)
                if price <= 0:
                    continue
                w.writerow([year, USE[use], price, round(pts[0][0], 6), round(pts[0][1], 6), a.get("L01_048", ""),
                            a.get("L01_050", ""), addr])
                n += 1
            log.append(f"{pi:02d} {year}: {cities} 累計 {n} 地点")
    (out / "landprice_layout.txt").write_text("\n\n".join(log) + "\n", encoding="utf-8")
    print("地価公示", n)


if __name__ == "__main__":
    main()
