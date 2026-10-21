"""data/raw/ の ZIP を集計して public/index.html を作る。

最新時点と1つ前の時点を比べたランキングと、全時点の推移を出す。
"""
import collections
import csv
import io
import json
import pathlib
import re
import sys
import unicodedata
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
TEMPLATE = ROOT / "templates" / "index.html"
OUT = ROOT / "public" / "index.html"

PREFS = (
    "北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 栃木県 群馬県 埼玉県 千葉県 "
    "東京都 神奈川県 新潟県 富山県 石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県 三重県 "
    "滋賀県 京都府 大阪府 兵庫県 奈良県 和歌山県 鳥取県 島根県 岡山県 広島県 山口県 徳島県 "
    "香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県"
).split()


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").strip()
    return s.replace("�", "〓")  # 元データの文字化けを明示的な代替文字にする


def label(period: str) -> str:
    return f"{int(period[:4])}年{int(period[4:])}月末"


def read_csv(zpath: pathlib.Path) -> list[dict]:
    with zipfile.ZipFile(zpath) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        raw = z.read(name)
    for enc in ("utf-8-sig", "cp932"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", "replace")
    return list(csv.DictReader(io.StringIO(text)))


def col(header: list[str], *keys: str) -> str:
    """表記ゆれに備えて、キーワードをすべて含む列名を探す。"""
    for h in header:
        if all(k in h for k in keys):
            return h
    raise KeyError(keys)


def load(zpath: pathlib.Path) -> dict:
    rows = read_csv(zpath)
    h = list(rows[0].keys())
    c_code = col(h, "都道府県コード")
    c_no = col(h, "事業所番号")
    c_muni = col(h, "事業所住所", "市区町村")
    c_corpno = col(h, "法人番号")
    c_corp = next(x for x in h if x.startswith("法人の名称") and "かな" not in x)
    c_name = next(x for x in h if x.startswith("事業所の名称") and "かな" not in x)
    offices = {}
    for r in rows:
        try:
            pi = int(r[c_code][:2]) - 1
        except ValueError:
            continue
        if not 0 <= pi < 47:
            continue
        muni = norm(r[c_muni])
        corp_key = r[c_corpno].strip() or norm(r[c_corp])
        o = offices.setdefault(r[c_no].strip(), dict(
            p=pi, munis=set(), h=0, corp=corp_key, cn=norm(r[c_corp]),
            name=norm(r[c_name]), m=muni))
        o["munis"].add(muni)
        o["h"] += 1
    return offices


def aggregate(offices: dict) -> dict:
    a = dict(pref=collections.Counter(), pref_h=collections.Counter(),
             muni=collections.Counter(), corp=collections.Counter(),
             corp_name={}, muni_pref={})
    for o in offices.values():
        a["pref"][o["p"]] += 1
        a["pref_h"][o["p"]] += o["h"]
        a["corp"][o["corp"]] += 1
        a["corp_name"][o["corp"]] = o["cn"]
        for m in o["munis"]:
            a["muni"][m] += 1
            a["muni_pref"][m] = o["p"]
    return a


def main() -> int:
    files = sorted(RAW.glob("*_33.zip"))
    periods = [f.name[:6] for f in files]
    if len(files) < 2:
        print("2時点以上のデータが必要です", file=sys.stderr)
        return 1
    loaded = [load(f) for f in files]
    aggs = [aggregate(o) for o in loaded]
    for p, o in zip(periods, loaded):
        print(f"{p}: 事業所 {len(o):,} / 住居 {sum(x['h'] for x in o.values()):,}")

    cur, prev = loaded[-1], loaded[-2]
    A, B = aggs[-2], aggs[-1]

    series = dict(
        labels=[label(p) for p in periods],
        offices=[len(o) for o in loaded],
        houses=[sum(x["h"] for x in o.values()) for o in loaded],
    )
    pref = [[PREFS[i], B["pref"][i], A["pref"][i], B["pref_h"][i],
             [g["pref"][i] for g in aggs]] for i in range(47)]
    muni_pref = {**A["muni_pref"], **B["muni_pref"]}
    muni = [[m, muni_pref[m], B["muni"].get(m, 0), A["muni"].get(m, 0)]
            for m in set(A["muni"]) | set(B["muni"]) if m]
    names = {**A["corp_name"], **B["corp_name"]}
    corp = sorted(([names[c], B["corp"].get(c, 0), A["corp"].get(c, 0)]
                   for c in set(A["corp"]) | set(B["corp"])),
                  key=lambda x: -max(x[1], x[2]))
    corp = [c for c in corp if c[1] >= 5 or c[2] >= 5 or abs(c[1] - c[2]) >= 2]
    new = [[o["name"], o["cn"], o["m"], o["p"], o["h"]] for k, o in cur.items() if k not in prev]
    gone = [[o["name"], o["cn"], o["m"], o["p"], o["h"]] for k, o in prev.items() if k not in cur]

    data = dict(
        cur=label(periods[-1]), prev=label(periods[-2]),
        tot=dict(o=len(cur), o0=len(prev),
                 h=series["houses"][-1], h0=series["houses"][-2],
                 c=len(B["corp"]), new=len(new), gone=len(gone)),
        series=series, pref=pref, muni=muni, corp=corp, new=new, gone=gone, P=PREFS,
    )
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    blob = blob.replace("</", "<\\/")  # script タグ内に埋め込むため
    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("__DATA__", blob)
    html = re.sub(r"__CUR__", data["cur"], html)
    html = re.sub(r"__PREV__", data["prev"], html)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"書き出し: {OUT} ({len(html):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
