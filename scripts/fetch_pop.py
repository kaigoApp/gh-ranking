"""総務省「住民基本台帳に基づく人口、人口動態及び世帯数」の市区町村別人口を取得する。

人口あたりの事業所数を出すために使う。Excel ファイル（xlsx）を標準ライブラリだけで読み、
data/pop.csv（団体コード・都道府県・市区町村名・人口）にして保存する。
同じ URL から取得済みなら何もしない。新しい年のファイルが出たら URL と時点を書きかえる。

  python scripts/fetch_pop.py
"""
import csv
import io
import pathlib
import re
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

# 【総計】令和8年住民基本台帳人口・世帯数、令和7年人口動態（市区町村別）
# https://www.soumu.go.jp/menu_news/s-news/01gyosei02_02000428.html
URL = "https://www.soumu.go.jp/main_content/001083789.xlsx"
AS_OF = "2026年1月1日"
ROOT = pathlib.Path(__file__).resolve().parent.parent
DEST = ROOT / "data" / "pop.csv"
UA = "jimofuku (+https://github.com/kaigoApp/gh-ranking)"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def norm(s):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s or "")))


def col_index(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_rows(xlsx: bytes):
    with zipfile.ZipFile(io.BytesIO(xlsx)) as z:
        strings = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
                strings.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        sheet = sorted(n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))[0]
        root = ET.fromstring(z.read(sheet))
    for row in root.iter(f"{{{NS['m']}}}row"):
        vals = {}
        for c in row.findall("m:c", NS):
            t = c.get("t")
            v = c.find("m:v", NS)
            if t == "s" and v is not None:
                val = strings[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{{{NS['m']}}}t"))
            else:
                val = v.text if v is not None else ""
            vals[col_index(c.get("r"))] = val
        if vals:
            yield [vals.get(i, "") for i in range(max(vals) + 1)]


def parse(xlsx: bytes):
    rows = list(read_rows(xlsx))
    for i, r in enumerate(rows[:12]):
        print(f"見出し候補 {i}: {[norm(x) for x in r[:12]]}")
    # 見出しの行：「市区町村名」がある行。人口の「計」はその行か次の行にある
    hi = next(i for i, r in enumerate(rows[:20]) if any("市区町村名" in norm(x) for x in r))
    head = [norm(x) for x in rows[hi]]
    c_code = next(i for i, h in enumerate(head) if h.startswith("団体コード"))
    c_pref = next(i for i, h in enumerate(head) if h.startswith("都道府県名"))
    c_muni = next(i for i, h in enumerate(head) if h.startswith("市区町村名"))
    # 人口の「計」は見出しの数行上にある（「計ケイ」のように読みがなが付くことがある）。最初に出てくる「計」が人口の計
    c_pop = None
    for r in rows[max(0, hi - 3):hi + 1]:
        for i, h in enumerate(r[:10]):
            if norm(h).startswith("計") and c_pop is None:
                c_pop = i
    if c_pop is None:
        raise ValueError(f"人口の列が見つかりません: {head}")
    print(f"列: コード={c_code} 都道府県={c_pref} 市区町村={c_muni} 人口={c_pop}")
    out = []
    for r in rows[hi + 1:]:
        if len(r) <= max(c_code, c_pref, c_muni, c_pop):
            continue
        code, pref, muni, pop = norm(r[c_code]), norm(r[c_pref]), norm(r[c_muni]), norm(r[c_pop])
        if not re.fullmatch(r"\d{5,6}", code) or not muni:
            continue
        if muni == "-":  # 都道府県の合計の行。市区町村名は空にして残す
            muni = ""
        try:
            n = int(float(pop))
        except ValueError:
            continue
        out.append(dict(code=code, pref=pref, muni=muni, pop=n))
    return out


def main():
    if DEST.exists():
        with DEST.open(encoding="utf-8") as f:
            if f.readline().strip() == f"# {URL} {AS_OF}":
                print("人口データは取得済みです")
                return 0
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        rows = parse(data)
    except Exception as e:  # noqa: BLE001
        print(f"::warning::人口データを取得できませんでした: {e}")
        return 0
    if len(rows) < 1500:
        print(f"::warning::人口データの行数が少なすぎます（{len(rows)}行）。保存しません")
        return 0
    buf = io.StringIO()
    buf.write(f"# {URL} {AS_OF}\n")
    w = csv.DictWriter(buf, fieldnames=["code", "pref", "muni", "pop"], lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    DEST.write_text(buf.getvalue(), encoding="utf-8")
    print(f"人口データ: {len(rows):,}行（例：{rows[:3]}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
