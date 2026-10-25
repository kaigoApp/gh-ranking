"""地域別レポート用に、令和2年国勢調査の町丁・字ごとの年齢（5歳階級）別人口と、その町の中心の位置を
data/report/smallarea.csv に書く。このコンテナからは e-Stat につながらないので、GitHub Actions
（report-data.yml）で動かす。

  統計：e-Stat 統計GIS「年齢（５歳階級、４区分）別、男女別人口」（T001082、町丁・字等別）
  位置：e-Stat 統計GIS「令和２年国勢調査 町丁・字等別境界データ」（世界測地系 緯度経度 Shape）

  KEY_CODE, 市区町村, 町丁・字, 経度, 緯度, 総数, 0-4歳, 5-9歳, …, 75歳以上（男女計）
"""
import csv
import io
import pathlib
import re
import sys
import urllib.request
import zipfile

import shapefile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from regional_report import TARGETS  # noqa: E402
from services import PREFS  # noqa: E402

STAT = "https://www.e-stat.go.jp/gis/statmap-search/data?statsId=T001082&code={:02d}&downloadType=2"
SHAPE = ("https://www.e-stat.go.jp/gis/statmap-search/data?dlserveyId=A002005212020&code={:02d}"
         "&coordSys=1&format=shape&downloadType=5&datum=2000")
AGES = ["0～4歳", "5～9歳", "10～14歳", "15～19歳", "20～24歳", "25～29歳", "30～34歳", "35～39歳", "40～44歳",
        "45～49歳", "50～54歳", "55～59歳", "60～64歳", "65～69歳", "70～74歳", "75歳以上"]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=300).read()


def num(s):
    s = (s or "").strip()
    return int(s) if s.isdigit() else 0


def norm(s):
    return re.sub(r"[\s　]", "", s or "").replace("~", "～").replace("〜", "～")


def places(code, cities, notes):
    """KEY_CODE → (市区町村, 町丁・字, 経度, 緯度)。中心は境界データの X_CODE/Y_CODE（なければ形の真ん中）。"""
    z = zipfile.ZipFile(io.BytesIO(get(SHAPE.format(code))))
    base = next(n[:-4] for n in z.namelist() if n.lower().endswith(".shp"))
    ext = {n[-4:].lower(): n for n in z.namelist() if n.startswith(base)}
    sf = shapefile.Reader(shp=io.BytesIO(z.read(ext[".shp"])), dbf=io.BytesIO(z.read(ext[".dbf"])),
                          shx=io.BytesIO(z.read(ext[".shx"])), encoding="cp932")
    names = [f[0] for f in sf.fields[1:]]
    if len(notes) < 3:
        notes.append(f"== 境界 {code:02d} {z.namelist()}\n{names}\n{sf.record(0)}")
    out = {}
    for sr in sf.iterShapeRecords():
        r = dict(zip(names, sr.record))
        city = r.get("CITY_NAME") or ""
        if not any(city.startswith(c) for c in cities):
            continue
        if str(r.get("HCODE", "8101")) != "8101":  # 8154 は水面など
            continue
        x, y = r.get("X_CODE"), r.get("Y_CODE")
        if not x or not y:
            b = sr.shape.bbox
            x, y = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        out[str(r["KEY_CODE"])] = (city, r.get("S_NAME") or "", round(float(x), 5), round(float(y), 5))
    return out


def stats(code, notes):
    z = zipfile.ZipFile(io.BytesIO(get(STAT.format(code))))
    name = next(n for n in z.namelist() if n.lower().endswith((".txt", ".csv")))
    lines = list(csv.reader(io.StringIO(z.read(name).decode("cp932"))))
    head, label = lines[0], [norm(x) for x in lines[1]]
    if len(notes) < 4:
        notes.append(f"== 統計 {code:02d} {name}\n" + "\n".join(f"{i}\t{h}\t{l}" for i, (h, l) in enumerate(zip(head, label))))
    # 男女計の列：「総数」で始まり、男・女の列より前にあるもの
    cut = next((i for i, l in enumerate(label) if l.startswith("男")), len(label))
    col = {"総数": next(i for i, l in enumerate(label) if l.startswith("総数"))}
    for a in AGES:
        col[a] = next(i for i, l in enumerate(label[:cut]) if l.endswith(a))
    out = {}
    for r in lines[2:]:
        if r and r[0].strip():
            out[r[0].strip()] = [num(r[col[k]]) if col[k] < len(r) else 0 for k in col]
    return list(col), out


def main():
    out = ROOT / "data" / "report" / "smallarea.csv"
    dump = ROOT / "data" / "report" / "smallarea_layout.txt"
    want, notes, rows = {}, [], []
    for pref, city in TARGETS:
        want.setdefault(PREFS.index(pref) + 1, []).append(city)
    keys = None
    for code, cities in sorted(want.items()):
        try:
            pl = places(code, cities, notes)
            keys, st = stats(code, notes)
        except Exception as e:  # noqa: BLE001
            notes.append(f"{code:02d}: 取得できない {e!r}")
            continue
        miss = 0
        for k, (city, town, x, y) in sorted(pl.items()):
            v = st.get(k)
            if v is None:
                miss += 1
                continue
            rows.append([k, city, town, x, y] + v)
        notes.append(f"{code:02d} {cities}: 町丁 {len(pl)}、統計なし {miss}")
    dump.write_text("\n\n".join(notes) + "\n", encoding="utf-8")
    if not rows:
        sys.exit("1件も取れなかった（smallarea_layout.txt を見る）")
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["key", "muni", "town", "lng", "lat"] + keys)
        w.writerows(rows)
    print(out, len(rows))


if __name__ == "__main__":
    main()
