"""data/slim/ を集計して、静的サイト一式を public/ に書き出す。

  /                         トップ（全サービスの概要、タイル地図、都道府県一覧）
  /s/<サービス>/            サービス別ランキング
  /p/<都道府県>/            都道府県ページ
  /p/<都道府県>/<市区町村>/ 市区町村ページ（事業所の一覧つき）
  /p/<都道府県>/<市区町村>/<サービス>/  市区町村 × サービスの事業所一覧
  /f/<事業所番号>/          事業所ページ（全サービス）
  /report/<時点>/           レポート（データの時点ごとに1本）
  /data/                    お仕事で使う方へ（地域のくらべ表・半年の動き・CSV ダウンロード）

環境変数 SITE_URL に公開先の URL を入れると、sitemap.xml と canonical に使う。
"""
import collections
import csv
import gzip
import html
import io
import json
import multiprocessing
import os
import pathlib
import re
import shutil
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from services import GROUPS, PREF_SLUGS, PREFS, SERVICES  # noqa: E402
import ogp  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SLIM = ROOT / "data" / "slim"
ASSETS = ROOT / "site" / "assets"
OUT = ROOT / "public"
SITE_URL = os.environ.get("SITE_URL", "https://kaigoapp.github.io/gh-ranking").rstrip("/")
SITE_NAME = "ジモふく"
TAGLINE = "地元の障害福祉が見つかる"
WATCH_GAPS = ["33", "46", "65", "63", "22", "52"]  # レポートで「ない市区町村」を数えるサービス
GH = "33"  # 住居数を出すのはグループホームだけ
GA_ID = "G-1DLDV43DSJ"  # Google アナリティクスの測定 ID（G-…）。空ならアクセス解析のタグを入れない

DESC = {
    "11": "自宅で入浴・排せつ・食事の介護や、調理・洗濯などの家事を手伝うサービス。",
    "12": "重い障害があり常に介護が必要な人に、自宅や外出先で長時間の介護を行うサービス。",
    "15": "視覚障害のある人の外出に同行し、移動の支援や情報の提供を行うサービス。",
    "13": "知的障害や精神障害により行動に著しい困難がある人の外出時に、危険を避ける支援を行うサービス。",
    "14": "常に介護が必要で意思疎通が難しい人に、複数のサービスを組み合わせて提供するサービス。",
    "21": "医療と常時の介護が必要な人に、病院で機能訓練や看護、介護を行うサービス。",
    "22": "常に介護が必要な人に、日中の介護や創作的活動・生産活動の機会を提供するサービス。",
    "24": "家族の病気などのときに、施設に短期間泊まって介護を受けられるサービス（ショートステイ）。",
    "32": "施設に入所する人に、主に夜間の介護や生活の支援を行うサービス。",
    "33": "地域の住居で共同生活を送る人に、主に夜間の相談や日常生活の援助を行うサービス（グループホーム）。",
    "61": "一人暮らしを始めた人などを定期的に訪問し、生活の相談や助言を行うサービス。",
    "41": "身体機能の維持・向上のために、一定期間リハビリなどの訓練を行うサービス。",
    "42": "生活能力の維持・向上のために、一定期間、日常生活の訓練を行うサービス。",
    "34": "住まいを提供しながら、地域生活に向けた生活能力の訓練を行うサービス。",
    "60": "一般企業への就職を目指す人に、訓練や職場探し、就職後の定着までを支援するサービス。",
    "45": "雇用契約を結んで働きながら、知識や能力を高める訓練を受けられるサービス。",
    "46": "雇用契約を結ばずに、自分のペースで生産活動などの働く機会を得られるサービス。",
    "62": "就職した人が働き続けられるよう、企業や家族との連絡調整や相談を行うサービス。",
    "63": "主に未就学の障害のある子どもに、日常生活の基本動作や集団生活への適応の支援を行うサービス。",
    "64": "肢体不自由のある子どもに、医療的な管理のもとで発達支援を行うサービス（2024年度に児童発達支援へ一元化）。",
    "65": "学校に通う障害のある子どもに、放課後や休日に生活能力の向上や交流の機会を提供するサービス。",
    "66": "外出が難しい重い障害のある子どもの自宅を訪ねて、発達支援を行うサービス。",
    "67": "保育所や学校などを訪問し、障害のある子どもが集団生活になじめるよう専門的な支援を行うサービス。",
    "68": "障害のある子どもが施設に入所し、保護や日常生活の指導を受けるサービス。",
    "69": "障害のある子どもが医療機関などに入所し、保護や日常生活の指導、治療を受けるサービス。",
    "53": "施設や病院から地域での生活に移る人に、住まい探しなどの相談や支援を行うサービス。",
    "54": "地域で一人暮らしをする人に、常時の連絡体制と緊急時の支援を行うサービス。",
    "52": "障害福祉サービスを利用する人の「サービス等利用計画」を作り、利用状況を確認するサービス。",
    "70": "障害のある子どもの通所支援の「障害児支援利用計画」を作り、利用状況を確認するサービス。",
}

PREF_ABBR = [(p[:-1] if p[-1] in "都府県" else p)[:2] for p in PREFS]
NEW_CHIP = '<span class="chip hot">新規掲載</span>'
CORP_CHIP = '<span class="chip hot">新規参入</span>'

# トップページの「目的からさがす」。行政の分類（訪問系など）ではなく、探す人の言葉で分ける。
# (見出し, 説明, アイコンの SVG パス, サービスコード)
PURPOSES = [
    ("暮らす場所", "グループホームや入所施設、ショートステイ", "M4 11.5 12 5l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5h-5v5H5a1 1 0 0 1-1-1z", ["33", "32", "24", "61", "34"]),
    ("働く・仕事の練習", "就労継続支援A型・B型、就労移行支援", "M8 7V5.5A1.5 1.5 0 0 1 9.5 4h5A1.5 1.5 0 0 1 16 5.5V7M4 8.5A1.5 1.5 0 0 1 5.5 7h13A1.5 1.5 0 0 1 20 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5zM4 12.5h16", ["46", "45", "60", "62"]),
    ("日中の通い先", "生活介護、自立訓練など", "M12 4v2M12 18v2M4 12h2M18 12h2M6.3 6.3l1.4 1.4M16.3 16.3l1.4 1.4M6.3 17.7l1.4-1.4M16.3 7.7l1.4-1.4M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7z", ["22", "42", "41", "21"]),
    ("子どもの支援", "放課後等デイサービス、児童発達支援", "M12 4.5a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5zM7 20v-5.5a5 5 0 0 1 10 0V20M5 11.5l3 2M19 11.5l-3 2", ["65", "63", "67", "66", "64", "68", "69"]),
    ("自宅での支援・外出", "ホームヘルプ、同行援護、移動の支援", "M7 20c0-3 1-5 1-7l-2-3 3-4 3 2 3-2M14 20l1-5 3-2M17 6.5a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3z", ["11", "12", "15", "13", "14"]),
    ("相談する", "サービス利用計画、地域生活の相談", "M5 6.5A1.5 1.5 0 0 1 6.5 5h11A1.5 1.5 0 0 1 19 6.5v8a1.5 1.5 0 0 1-1.5 1.5H11l-4 3.5V16h-.5A1.5 1.5 0 0 1 5 14.5zM9 10h6M9 12.5h4", ["52", "70", "53", "54"]),
]

# 12列のタイル地図での (行, 列)
TILE = {
    0: (0, 11), 1: (1, 11), 4: (2, 10), 2: (2, 11), 5: (3, 10), 3: (3, 11),
    16: (4, 7), 15: (4, 8), 14: (4, 9), 6: (4, 10),
    31: (5, 2), 30: (5, 3), 27: (5, 4), 25: (5, 5), 17: (5, 6), 20: (5, 7), 19: (5, 8),
    9: (5, 9), 8: (5, 10), 7: (5, 11),
    34: (6, 1), 33: (6, 2), 32: (6, 3), 26: (6, 4), 28: (6, 5), 24: (6, 6), 22: (6, 7),
    18: (6, 8), 10: (6, 9), 12: (6, 10), 11: (6, 11),
    40: (7, 0), 39: (7, 1), 37: (7, 2), 36: (7, 3), 29: (7, 4), 23: (7, 5), 21: (7, 7), 13: (7, 10),
    41: (8, 0), 43: (8, 1), 38: (8, 2), 35: (8, 3),
    42: (9, 0), 44: (9, 1), 45: (10, 0), 46: (2, 0),
}
assert len(TILE) == 47

REGIONS = [("北海道・東北", range(0, 7)), ("関東", range(7, 14)), ("中部", range(14, 23)), ("近畿", range(23, 30)),
           ("中国", range(30, 35)), ("四国", range(35, 39)), ("九州・沖縄", range(39, 47))]

MUNI_RE = re.compile(r"[^\s/\\?#%]{1,20}?[市区町村]")


# ---------------------------------------------------------------- 集計

def label(p: str, short=False) -> str:
    y, m = int(p[:4]), int(p[4:])
    return f"{y % 100}.{m}" if short else f"{y}年{m}月末"


def split_muni(addr_muni: str, code_pref: int):
    """「東京都八王子市」→ (12, "八王子市")。市区町村として扱えないものは名前を None にする。"""
    for i, p in enumerate(PREFS):
        if addr_muni.startswith(p):
            rest = addr_muni[len(p):]
            return i, (rest if MUNI_RE.fullmatch(rest) else None)
    return code_pref, None


def read_slim(path: pathlib.Path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        yield from csv.DictReader(f)


CORP_FORMS = ["株式会社", "社会福祉法人", "合同会社", "特定非営利活動法人", "一般社団法人", "医療法人", "有限会社"]


def corp_form(name: str) -> str:
    """運営法人の種類を法人名に含まれる言葉で分ける（おおよそ）。"""
    return next((k for k in CORP_FORMS if k in name), "その他")


def summarize(path: pathlib.Path, keep: bool):
    """1サービス・1時点のファイルを読み、事業所単位にまとめる。"""
    offices = {}
    for r in read_slim(path):
        pi, mname = split_muni(r["muni"], int(r["pref"]))
        o = offices.get(r["no"])
        if o is None:
            o = offices[r["no"]] = dict(p=pi, corp_key=r["corp_key"], corp=r["corp"], name=r["name"],
                                        rows=0, munis={}, houses=[], tel="", url="", cap="", hours="", closed="")
        o["rows"] += 1
        if mname:
            mk = (pi, mname)
            if mk not in o["munis"]:
                o["munis"][mk] = (r["name"], r["addr"])
        if keep:
            o["houses"].append((r["name"], r["muni"], r["addr"], r.get("lat", ""), r.get("lng", "")))
            o["tel"] = o["tel"] or r.get("tel", "")
            o["url"] = o["url"] or r.get("url", "")
            for k in ("cap", "hours", "closed"):
                o[k] = o[k] or (r.get(k) or "")
    pref = collections.Counter(o["p"] for o in offices.values())
    muni = collections.Counter(mk for o in offices.values() for mk in o["munis"])
    rows = sum(o["rows"] for o in offices.values())
    ctype = collections.Counter(corp_form(o["corp"]) for o in offices.values())
    return dict(n=len(offices), rows=rows, pref=pref, muni=muni, ctype=ctype, offices=offices if keep else None,
                nos={no: o["rows"] for no, o in offices.items()})


POP_PREF: dict = {}
POP_MUNI: dict = collections.defaultdict(dict)
POP_ASOF = [""]
POP_SOURCE = "人口：総務省「住民基本台帳に基づく人口、人口動態及び世帯数」"


def load_pop():
    """data/pop.csv（scripts/fetch_pop.py が作る）を読む。無ければ人口あたりの数は出さない。"""
    f = ROOT / "data" / "pop.csv"
    if not f.exists():
        return
    lines = f.read_text(encoding="utf-8").splitlines()
    if lines and lines[0].startswith("#"):
        POP_ASOF[0] = lines[0].split()[-1]
        lines = lines[1:]
    for r in csv.DictReader(lines):
        if r["pref"] not in PREFS:
            continue
        pi = PREFS.index(r["pref"])
        if r["muni"]:
            POP_MUNI[pi][r["muni"]] = int(r["pop"])
        else:
            POP_PREF[pi] = int(r["pop"])
    if not POP_PREF:  # 都道府県の行がない古い形式。政令市の区を除いて足す
        for pi, ms in POP_MUNI.items():
            POP_PREF[pi] = sum(n for m, n in ms.items() if not re.search(r"市.+区$", m))
    print(f"人口データ: {sum(len(v) for v in POP_MUNI.values()):,}市区町村（{POP_ASOF[0]}）")


def pop_of(pi, mname):
    """市区町村の人口。名前の書き方が少し違う（「八丈島八丈町」と「八丈町」など）ときは、末尾が一致する1つを使う。"""
    ms = POP_MUNI.get(pi, {})
    if mname in ms:
        return ms[mname]
    cand = [k for k in ms if mname.endswith(k) or k.endswith(mname)]
    return ms[cand[0]] if len(cand) == 1 else None


def per10k(n, pop):
    return f"{n / pop * 10000:.1f}" if pop and n is not None else ""


def load():
    files = {c: sorted((SLIM / c).glob("*.csv.gz")) for c in SERVICES if (SLIM / c).is_dir()}
    periods = sorted({f.name[:6] for fs in files.values() for f in fs})
    if len(periods) < 2:
        raise SystemExit("2時点以上のデータが必要です")
    latest, prev = periods[-1], periods[-2]
    year_ago = periods[-3] if len(periods) >= 3 else periods[0]
    S = {}
    for code, fs in files.items():
        have = {f.name[:6]: f for f in fs}
        if latest not in have:
            print(f"{code} {SERVICES[code][0]}: 最新時点のデータがないので除外")
            continue
        per = {}
        for p in periods:
            if p in have:
                per[p] = summarize(have[p], keep=p in (latest, prev))
        S[code] = per
        print(f"{code} {SERVICES[code][0]}: " + " ".join(f"{p}={per[p]['n']}" for p in per))
    return periods, latest, prev, year_ago, S


# ---------------------------------------------------------------- 部品

E = html.escape


def fmt(n) -> str:
    return f"{n:,}"


def delta(d, cls="") -> str:
    k = "up" if d > 0 else "down" if d < 0 else "zero"
    s = f"+{d:,}" if d > 0 else f"−{-d:,}" if d < 0 else "±0"
    return f'<span class="d {k} {cls}">{s}</span>'


def pct(cur, base):
    return None if not base else (cur - base) / base * 100


def spark(vals, w=72, h=26) -> str:
    v = [x for x in vals if x is not None]
    if len(v) < 3:
        return ""
    lo, hi = min(v), max(v)
    pts = []
    for i, x in enumerate(v):
        px = 2 + i / (len(v) - 1) * (w - 4)
        py = h - 3 - (x - lo) / ((hi - lo) or 1) * (h - 7)
        pts.append((px, py))
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"2,{h - 1} " + line + f" {w - 2},{h - 1}"
    lx, ly = pts[-1]
    return (f'<svg viewBox="0 0 {w} {h}" aria-hidden="true"><polygon class="spark-area" points="{area}"/>'
            f'<polyline class="spark-line" points="{line}"/><circle class="spark-dot" cx="{lx:.1f}" cy="{ly:.1f}" r="2.4"/></svg>')


def trend_chart(periods, vals, unit="件") -> str:
    pairs = [(p, v) for p, v in zip(periods, vals) if v is not None]
    if len(pairs) < 2:
        return ""
    W, H, pt, pb = 480, 210, 26, 28
    mx = max(v for _, v in pairs)
    slot = W / len(pairs)
    bw = min(30, slot * 0.62)
    out = []
    for i, (p, v) in enumerate(pairs):
        x = slot * i + (slot - bw) / 2
        bh = (H - pt - pb) * (v / mx if mx else 0)
        y = H - pb - bh
        last = i == len(pairs) - 1
        out.append(f'<rect class="b{" last" if last else ""}" x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="2"/>')
        if i in (0, len(pairs) - 1):
            out.append(f'<text class="v" x="{x + bw / 2:.1f}" y="{y - 6:.1f}" text-anchor="middle">{v:,}</text>')
        if len(pairs) <= 8 or i % 2 == (len(pairs) - 1) % 2:
            out.append(f'<text class="ax" x="{x + bw / 2:.1f}" y="{H - 9}" text-anchor="middle">{label(p, True)}</text>')
    out.append(f'<line class="base" x1="0" x2="{W}" y1="{H - pb}" y2="{H - pb}"/>')
    first, lastv = pairs[0][1], pairs[-1][1]
    alt = f"{label(pairs[0][0])}の{first:,}{unit}から{label(pairs[-1][0])}の{lastv:,}{unit}への推移"
    return f'<div class="chart"><svg viewBox="0 0 {W} {H}" role="img" aria-label="{E(alt)}">{"".join(out)}</svg></div>'


def tilemap(values: dict, links: dict, titles: dict, caption: str, legend_lo: str, legend_hi: str) -> str:
    vs = [v for v in values.values() if v is not None]
    lo, hi = (min(vs), max(vs)) if vs else (0, 1)
    cells = []
    for pi in range(47):
        r, c = TILE[pi]
        v = values.get(pi)
        t = 8 if v is None else 8 + 92 * ((v - lo) / ((hi - lo) or 1))
        cls = ' class="hi"' if t > 58 else ""
        cells.append(f'<a href="{links[pi]}"{cls} style="grid-area:{r + 1}/{c + 1};--t:{t:.0f}%" '
                     f'title="{E(titles[pi])}" aria-label="{E(titles[pi])}">{PREF_ABBR[pi]}</a>')
    return (f'<figure class="tilemap" style="margin:0"><div class="grid">{"".join(cells)}</div>'
            f'<div class="legend"><span>{E(legend_lo)}</span><span class="ramp"></span><span>{E(legend_hi)}</span></div>'
            f'<figcaption class="cap">{E(caption)}</figcaption></figure>')


SEARCH_FORM = """<form class="search" id="search" role="search">
  <label for="q">施設名・法人名・市区町村名でさがす</label>
  <div class="field"><svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8.5" cy="8.5" r="6" fill="none" stroke="currentColor" stroke-width="2"/><path d="M13 13l5 5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
  <input id="q" type="search" placeholder="例：フレンド、江別市、社会福祉法人〇〇" autocomplete="off" enterkeyhint="search"></div>
  <p class="status" aria-live="polite"></p>
  <ul class="results"></ul>
</form>"""


LOGO = ('<svg viewBox="0 0 32 32" aria-hidden="true"><path d="M16 2C9.4 2 4 7.3 4 13.9 4 22.6 16 30 16 30s12-7.4 12-16.1C28 7.3 22.6 2 16 2z" fill="var(--brand)"/>'
        '<path d="M10 15.2 16 10l6 5.2V21a1 1 0 0 1-1 1h-3.2v-4h-3.6v4H11a1 1 0 0 1-1-1z" fill="var(--on-brand)"/>'
        '<circle cx="23.5" cy="6.5" r="3.5" fill="var(--sun)"/></svg>')


def page(path: str, title: str, desc: str, hero: str, body: str, scripts: str = "", split=False, og: str = "og/top.png") -> None:
    """path は public/ からの相対パス（"s/group-home/" など、末尾スラッシュ）。og はシェア用画像の public/ からのパス。"""
    depth = path.count("/")
    rel = "../" * depth
    canonical = SITE_URL + "/" + urllib.parse.quote(path)
    full_title = title if title.startswith(SITE_NAME) else f"{title}｜{SITE_NAME}"
    doc = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{E(full_title)}</title>
<meta name="description" content="{E(desc)}">
<link rel="canonical" href="{E(canonical)}">
<meta property="og:title" content="{E(full_title)}">
<meta property="og:description" content="{E(desc)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:url" content="{E(canonical)}">{OG_META.format(url=E(SITE_URL + "/" + urllib.parse.quote(og))) if ogp.ENABLED and og else ""}
<meta name="theme-color" content="#1f4d3a">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=BIZ+UDPGothic:wght@400;700&family=Barlow+Semi+Condensed:wght@600;700&family=Zen+Maru+Gothic:wght@700;900&display=swap">
<link rel="stylesheet" href="{rel}assets/site.css">{GA_TAG.format(id=GA_ID) if GA_ID else ""}
</head>
<body>
<header class="site-head"><div class="container">
  <a class="brand" href="{rel or './'}">{LOGO}<span class="wm">{SITE_NAME}</span><span class="tag">{TAGLINE}</span></a>
  <nav class="site-nav" aria-label="サイト内"><a href="{rel}#purpose">目的からさがす</a><a href="{rel}#prefs">地域からさがす</a><a href="{rel}data/">データ</a><a href="{rel}report/">レポート</a><a class="find" href="{rel or './'}#search"><svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8.5" cy="8.5" r="6" fill="none" stroke="currentColor" stroke-width="2.2"/><path d="M13 13l5 5" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>さがす</a></nav>
</div></header>
<div class="band"><div class="hero{' split' if split else ''}"><div class="container">{hero}</div></div></div>
<main><div class="container">{body}</div></main>
<footer class="site-foot"><div class="container">
  <div class="foot-brand">{LOGO}<div><b>{SITE_NAME}</b><span>{TAGLINE}。全国の障害福祉サービスの事業所を、国の公表データからまとめています。</span></div></div>
  <div>出典：独立行政法人福祉医療機構 <a href="https://www.wam.go.jp/content/wamnet/pcpub/top/sfkopendata/">WAM NET「障害福祉サービス等情報公表システムデータのオープンデータ」</a>を加工して作成。最新データ：{E(LATEST_LABEL)}時点。</div>
  <div><a href="{rel}report/">レポート</a>　<a href="{rel}privacy/">プライバシーとアクセス解析について</a></div>
  <div>事業所数は事業所番号の数です。1つの事業所が複数のサービスを行う場合は、サービスごとに数えています（延べ数）。新規掲載・掲載終了は公表システムへの掲載の有無で判定しており、実際の開設・廃止日とは一致しない場合があります。</div>
</div></footer>
{scripts}
</body>
</html>
"""
    dest = OUT / path / "index.html" if path else OUT / "index.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(doc, encoding="utf-8")
    PAGES.append(path)


def crumbs(rel: str, items) -> str:
    parts = [f'<a href="{rel or "./"}">全国</a>']
    for name, href in items:
        parts.append('<span aria-hidden="true">›</span>')
        parts.append(f'<a href="{href}">{E(name)}</a>' if href else f"<span>{E(name)}</span>")
    return f'<nav class="crumbs" aria-label="パンくず">{"".join(parts)}</nav>'


def muni_path(pi, name):
    return f"p/{PREF_SLUGS[pi]}/{name}/"


FAC_PAGES: set = set()    # 事業所ページを作る事業所番号（最新時点に載っている全サービスの事業所）


def fac_path(no):
    """事業所ページ。事業所番号は英数字だけのはずだが、念のため確かめる。"""
    return f"f/{no}/" if no in FAC_PAGES else None


def ms_path(pi, name, code):
    """市区町村 × サービスのページ。"""
    return f"{muni_path(pi, name)}{SERVICES[code][2]}/"


def display_name(o):
    """グループホームは住居ごとに名前が違うことがあるので、いちばん多い名前を事業所名にする。"""
    names = [h[0] for h in o["houses"]] or [o["name"]]
    return collections.Counter(names).most_common(1)[0][0]


def ps_path(pi, code):
    """都道府県 × サービスのページ。市区町村名は「市区町村」で終わるので s とは重ならない。"""
    return f"p/{PREF_SLUGS[pi]}/s/{SERVICES[code][2]}/"


def href(rel, path):
    return rel + urllib.parse.quote(path)


# ---------------------------------------------------------------- ページ

PAGES: list[str] = []
OG_JOBS: dict = {}        # シェア用画像のパス → 画像に書く内容
GA_TAG = """
<script async src="https://www.googletagmanager.com/gtag/js?id={id}"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}gtag('js',new Date());gtag('config','{id}');</script>"""
OG_META = """
<meta property="og:image" content="{url}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">"""


def og_image(key: str, eyebrow: str, title: str, number=None, unit="", lines=()) -> str:
    """シェア用画像を予約して、そのパスを返す。画像はページを全部作ったあとにまとめて描く。"""
    path = f"og/{key}.png"
    OG_JOBS[path] = (eyebrow, title, number, unit, tuple(lines), f"jimofuku.jp　{LATEST_LABEL}時点　国の公表データから")
    return path


def muni_og(pi, name):
    return f"og/m/{PREF_SLUGS[pi]}/{name}.png"


def _draw_og(job):
    path, (eyebrow, title, number, unit, lines, foot) = job
    ogp.make(OUT / path, eyebrow, title, number, unit, lines, foot)


def draw_og_images():
    if not ogp.ENABLED:
        print("シェア用画像：Pillow かフォントがないので作りません")
        return
    with multiprocessing.Pool() as pool:
        pool.map(_draw_og, sorted(OG_JOBS.items()), chunksize=32)
    print(f"シェア用画像: {len(OG_JOBS):,}")
CORP_PAGES: set = set()   # 法人ページを作る法人番号
CORP_MIN = 3              # 最新時点でこの数以上の事業所を運営する法人だけページを作る


def corp_path(k):
    return f"c/{k}/" if k in CORP_PAGES else None


def corp_link(rel, k, name):
    cp = corp_path(k)
    return f'<a href="{href(rel, cp)}">{E(name)}</a>' if cp else E(name)


def matome_path(pi, name):
    """市区町村の「まとめ1枚」（お仕事で使う方へ）。"""
    return f"data/{PREF_SLUGS[pi]}/{name}/"
FRESH: dict = {}          # グループホームの事業所番号 → 初掲載の時点（直近1年のもの）
FRESH_LATEST = [""]


def fresh_chip(no):
    p = FRESH.get(no)
    if not p:
        return ""
    if p == FRESH_LATEST[0]:
        return '<span class="chip hot">半年以内に新規掲載</span>'
    return '<span class="chip">1年以内に新規掲載</span>'


LATEST_LABEL = ""


def build():
    global LATEST_LABEL
    periods, latest, prev, year_ago, S = load()
    load_pop()
    LATEST_LABEL = label(latest)
    codes = [c for _, cs in GROUPS for c in cs if c in S]

    def n_at(c, p):
        return S[c][p]["n"] if p in S[c] else None

    def pref_at(c, p, pi):
        return S[c][p]["pref"].get(pi, 0) if p in S[c] else None

    def muni_at(c, p, mk):
        return S[c][p]["muni"].get(mk, 0) if p in S[c] else None

    def sum_known(vals):
        v = [x for x in vals if x is not None]
        return sum(v) if v else None

    total_series = [sum_known(n_at(c, p) for c in codes) for p in periods]
    pref_total = {p: collections.Counter() for p in periods}
    muni_total = {p: collections.Counter() for p in periods}
    for c in codes:
        for p, s in S[c].items():
            pref_total[p].update(s["pref"])
            muni_total[p].update(s["muni"])
    all_munis = sorted({mk for c in codes for mk in S[c][latest]["muni"]})
    print(f"市区町村ページ: {len(all_munis)}")

    # 新規掲載の事業所（サービスごと）
    new_nos = {c: set(S[c][latest]["offices"]) - set(S[c][prev]["offices"]) if prev in S[c] else set() for c in codes}
    # グループホームが初めて公表データに載った時点。直近1年（2時点）に初掲載なら「最近できた」とみなす。
    # 開設したばかりの事業所は入居者を募集していることが多いので、探す人の目に入る場所に出す。
    first_seen = {}
    if GH in S:
        for p in periods:
            for no in (S[GH][p].get("nos") or ()) if p in S[GH] else ():
                first_seen.setdefault(no, p)
    recent = {latest, prev} - {periods[0]}
    FRESH.clear()
    FRESH.update({no: p for no, p in first_seen.items() if p in recent and GH in S and no in S[GH][latest]["offices"]})
    FRESH_LATEST[0] = latest
    corp_nos = collections.defaultdict(set)
    for c in codes:
        for no, o in S[c][latest]["offices"].items():
            if re.fullmatch(r"[1-9]\d{12}", o["corp_key"]):  # 法人番号の先頭はチェック用の数字で 1〜9
                corp_nos[o["corp_key"]].add(no)
    CORP_PAGES.clear()
    CORP_PAGES.update(k for k, v in corp_nos.items() if len(v) >= CORP_MIN)
    FAC_PAGES.clear()
    FAC_PAGES.update(no for c in codes for no in S[c][latest]["offices"] if re.fullmatch(r"[0-9A-Za-z]{1,20}", no))
    print(f"事業所ページ: {len(FAC_PAGES):,}")
    print(f"法人ページを作る法人: {len(CORP_PAGES):,}")
    print(f"グループホーム 1年以内に初掲載: {len(FRESH):,}")

    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ASSETS, OUT / "assets", dirs_exist_ok=True)

    # ---------- トップ
    tot, tot0 = total_series[-1], total_series[-2]
    first_i = next(i for i, v in enumerate(total_series) if v)
    ratio = tot / total_series[first_i]
    rates = {pi: pct(pref_total[latest][pi], pref_total[year_ago][pi]) for pi in range(47)}
    titles = {pi: f"{PREFS[pi]} {pref_total[latest][pi]:,}件（1年で{rates[pi]:+.1f}%）" if rates[pi] is not None else PREFS[pi] for pi in range(47)}
    links = {pi: href("", f"p/{PREF_SLUGS[pi]}/") for pi in range(47)}
    rv = [v for v in rates.values() if v is not None]
    hero = f"""<div class="hero-copy">
  <div class="eyebrow">全国 {len(all_munis):,} の市区町村　{len(codes)} 種類のサービス</div>
  <h1>地元の障害福祉を、<br>ちゃんと見つける。</h1>
  <p class="lede">グループホーム、就労支援、放課後等デイサービスまで。国の公表データをもとに、全国{tot:,}か所の事業所を地域とサービスからさがせます。</p>
  {SEARCH_FORM}
</div>
<div class="hero-map">
  <div class="map-h"><b>地図からさがす</b><span>色が濃いほど、この1年で事業所が増えた都道府県</span></div>
  {tilemap(rates, links, titles, "都道府県を押すと、その地域のページへ移ります。", f"{min(rv):+.1f}%", f"{max(rv):+.1f}%")}
</div>"""

    def icon(d):
        return f'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="{d}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>'

    purpose_html = []
    for title, sub, ic, cs in PURPOSES:
        cs = [c for c in cs if c in S]
        if not cs:
            continue
        n = sum(S[c][latest]["n"] for c in cs)
        links_ = "".join(f'<a href="{href("", "s/" + SERVICES[c][2] + "/")}#area"><span>{E(SERVICES[c][1])}</span><span class="c">{S[c][latest]["n"]:,}</span></a>' for c in cs)
        purpose_html.append(f'<div class="purpose"><div class="ph">{icon(ic)}<div><h3>{E(title)}<span class="pn">{n:,}<small>か所</small></span></h3><p>{E(sub)}</p></div></div><div class="plinks">{links_}</div></div>')

    stats = f"""<div class="stats">
  <div><span class="v">{tot:,}</span><span class="k">掲載している事業所（延べ）</span></div>
  <div><span class="v">{delta(tot - tot0)}</span><span class="k">この半年の増減</span></div>
  <div><span class="v">{len(FRESH):,}</span><span class="k">この1年でできたグループホーム</span></div>
  <div><span class="v">{latest[:4]}.{int(latest[4:])}</span><span class="k">最新データ（{E(LATEST_LABEL)}時点）</span></div>
</div>"""

    fresh_top = ""
    if GH in S and FRESH:
        fc = collections.Counter(S[GH][latest]["offices"][no]["p"] for no in FRESH)
        grid = "".join(f'<a href="{href("", ps_path(pi, GH))}#fresh"><span>{PREFS[pi]}</span><span class="c">{fc.get(pi, 0):,}</span></a>' for pi in range(47))
        fresh_top = (f'<section id="fresh" class="feature"><div class="feature-h"><span class="badge">NEW</span><h2>最近できたグループホーム</h2></div>'
                     f'<p class="sec-sub">この1年で新しく公表データに載ったグループホームは全国に<b>{len(FRESH):,}件</b>。できたばかりの事業所は入居者を募集していることが多く、入居先を探す手がかりになります。都道府県を選ぶと一覧が見られます。</p>'
                     f'<div class="pref-grid">{grid}</div></section>')
    inc = sorted(all_munis, key=lambda mk: (-(muni_total[latest][mk] - muni_total[prev][mk]), -muni_total[latest][mk]))[:15]
    inc_rows = "".join(
        f'<tr><td class="r tnum">{i + 1}</td><td class="name"><a href="{href("", muni_path(*mk))}">{E(PREFS[mk[0]] + " " + mk[1])}</a></td>'
        f'<td class="n">{muni_total[latest][mk]:,}</td><td class="r">{delta(muni_total[latest][mk] - muni_total[prev][mk])}</td></tr>'
        for i, mk in enumerate(inc))
    regions = REGIONS
    prefs_html = "".join(
        f'<div class="region"><h3>{r}</h3><div class="pref-grid">'
        + "".join(f'<a href="{links[pi]}"><span>{PREFS[pi]}</span><span class="c">{pref_total[latest][pi]:,}</span></a>' for pi in rng)
        + '</div></div>' for r, rng in regions)
    body = f"""
{stats}
<section id="purpose"><span id="services"></span><h2>目的からさがす</h2><p class="sec-sub">どんな支援を探しているかを選ぶと、次に地域を選べます。数字は全国の事業所数です。</p>
<div class="purposes">{"".join(purpose_html)}</div></section>
<a class="pro-link" href="data/"><span class="eyebrow">お仕事で使う方へ</span><b>地域ごとのくらべ表と、ダウンロードできるデータ</b><span>事業所の運営・相談支援・自治体の計画づくりに。市区町村ごとの事業所数と増え方、半年間に新しく載った事業所の一覧を、Excel で開けるファイルでも配っています。</span><i aria-hidden="true">→</i></a>
<a class="pro-link" href="report/{latest}/"><span class="eyebrow">レポート</span><b>障害福祉の事業所はどう変わったか（{E(LATEST_LABEL)}）</b><span>増え方、地域の差、入れ替わり、運営法人の変化を、国の公表データからまとめました。データが新しくなるたびに更新します。</span><i aria-hidden="true">→</i></a>
{fresh_top}
<section id="prefs"><h2>地域からさがす</h2><p class="sec-sub">都道府県を選ぶと、市区町村ごとの事業所の一覧へ進めます。数字は全サービスの延べ事業所数です。</p><div class="regions">{prefs_html}</div></section>
<section class="data-zone"><div class="data-h"><span class="eyebrow">データで見る</span><h2>障害福祉の事業所は、いまも増えています</h2>
<p class="sec-sub">全サービスの延べ事業所数の推移。{E(label(periods[first_i]))}から<b>{ratio:.2f}倍</b>になりました。初期の数字は、公表システムへの登録が行き渡る前のため少なめに出ている可能性があります。</p></div>
{trend_chart(periods, total_series)}
<div><h3 class="sec-h">この半年で事業所が増えた市区町村</h3><p class="sec-sub">全サービスの延べ事業所数の増加数です。増減は{E(label(prev))}との比較です。</p>
<div class="table-wrap"><table><thead><tr><th class="r">順位</th><th>市区町村</th><th class="r">事業所数</th><th class="r">半年の増減</th></tr></thead><tbody>{inc_rows}</tbody></table></div></div>
</section>"""
    page("", f"{SITE_NAME}｜{TAGLINE}", f"全国の障害福祉サービス事業所（{LATEST_LABEL}時点 {tot:,}件）を、グループホーム・就労支援・放課後等デイなどサービス別、都道府県・市区町村別にさがせます。国の公表データから自動更新。",
         hero, body, '<script src="assets/search.js" defer></script>', split=True,
         og=og_image("top", "全国の障害福祉サービス", "地元の障害福祉を、ちゃんと見つける。", tot, "か所",
                     [f"{len(codes)}種類のサービス", f"{len(all_munis):,}の市区町村", "地域と目的からさがせます"]))

    # ---------- サービス別
    for c in codes:
        name, short, slug = SERVICES[c]
        path = f"s/{slug}/"
        rel = "../../"
        ser = [n_at(c, p) for p in periods]
        cur, pv = ser[-1], ser[-2]
        ya = n_at(c, year_ago)
        fi = next(i for i, v in enumerate(ser) if v)
        L, P_ = S[c][latest], S[c].get(prev)
        rates = {pi: pct(L["pref"].get(pi, 0), pref_at(c, year_ago, pi)) for pi in range(47)}
        titles = {pi: f"{PREFS[pi]} {L['pref'].get(pi, 0):,}件" + (f"（1年で{rates[pi]:+.1f}%）" if rates[pi] is not None else "") for pi in range(47)}
        links = {pi: href(rel, ps_path(pi, c)) for pi in range(47)}
        rv = [v for v in rates.values() if v is not None] or [0]
        group = next(g for g, cs in GROUPS if c in cs)
        hero = f"""<div>
  {crumbs(rel, [(short, None)])}
  <div class="eyebrow" style="margin-top:12px">{E(group)}　{E(LATEST_LABEL)}時点</div>
  <h1>{E(name)}の事業所数ランキング</h1>
  <p class="lede">{E(DESC.get(c, ""))}</p>
  <div class="big"><div><span class="n">{cur:,}</span><span class="u">事業所</span></div>
    <div class="meta">{f'<span>半年で <b>{delta(cur - pv)}</b></span>' if pv is not None else ''}{f'<span>1年で <b>{delta(cur - ya)}</b></span>' if ya is not None and year_ago != prev else ''}{f'<span>{label(periods[fi])}の <b>{cur / ser[fi]:.2f}倍</b></span>' if fi < len(ser) - 1 else ''}</div></div>
</div>
{tilemap(rates, links, titles, f"都道府県別、1年間の事業所数の増加率。タップでその都道府県の{short}のページへ。", f"{min(rv):+.1f}%", f"{max(rv):+.1f}%")}"""
        corp_cur = collections.Counter(o["corp_key"] for o in L["offices"].values())
        corp_prev = collections.Counter(o["corp_key"] for o in P_["offices"].values()) if P_ else collections.Counter()
        cnames = {}
        for src in (P_, L):
            if src:
                for o in src["offices"].values():
                    cnames[o["corp_key"]] = o["corp"]
        corp = sorted(([cnames[k], corp_cur.get(k, 0), corp_prev.get(k, 0), href(rel, corp_path(k)) if corp_path(k) else ""] for k in set(corp_cur) | set(corp_prev)),
                      key=lambda x: -max(x[1], x[2]))
        corp = [x for x in corp if x[1] >= 5 or x[2] >= 5 or abs(x[1] - x[2]) >= 2][:300]

        def office_row(o, no=None):
            mk = next(iter(o["munis"]), None)
            fac = href(rel, fac_path(no)) if no and fac_path(no) else ""
            return [display_name(o) if o["houses"] else o["name"], o["corp"], mk[1] if mk else "",
                    o["p"], href(rel, muni_path(*mk)) if mk else "", fac]

        newl = [office_row(L["offices"][k], k) for k in sorted(new_nos[c])]
        gone = [office_row(P_["offices"][k]) for k in sorted(set(P_["offices"]) - set(L["offices"]))] if P_ else []
        munis = [[mk[1], mk[0], L["muni"].get(mk, 0), P_["muni"].get(mk, 0) if P_ else 0, href(rel, muni_path(*mk))]
                 for mk in set(L["muni"]) | (set(P_["muni"]) if P_ else set())]
        data = dict(cur=LATEST_LABEL, prev=label(prev), P=PREFS,
                    pref=[[PREFS[pi], L["pref"].get(pi, 0), P_["pref"].get(pi, 0) if P_ else 0, links[pi]] for pi in range(47)],
                    muni=munis, corp=corp, new=newl, gone=gone)
        blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        kpis = [("運営法人", len(corp_cur), "法人"), ("半年の新規掲載", len(newl), "件"), ("半年の掲載終了", len(gone), "件")]
        kpi_html = "".join(f'<div class="kpi"><span class="k">{k}</span><span class="v">{v:,}<small>{u}</small></span></div>' for k, v, u in kpis)
        siblings = [x for g, cs in GROUPS if c in cs for x in cs if x in S and x != c]
        sib = "".join(f'<a href="{href(rel, "s/" + SERVICES[x][2] + "/")}"><span>{E(SERVICES[x][1])}</span><span class="c">{S[x][latest]["n"]:,}</span></a>' for x in siblings)
        by_pref_m = collections.defaultdict(list)
        for mk, n in L["muni"].items():
            by_pref_m[mk[0]].append((mk, n))
        area = "".join(
            f'<h3 class="area-h">{E(rname)}</h3>' + "".join(
                f'<details><summary>{PREFS[pi]}<span class="c">{L["pref"].get(pi, 0):,}件</span></summary><div class="pref-grid">'
                + "".join(f'<a href="{href(rel, ms_path(*mk, c))}"><span>{E(mk[1])}</span><span class="c">{n:,}</span></a>'
                          for mk, n in sorted(by_pref_m[pi], key=lambda x: (-x[1], x[0][1])))
                + '</div></details>'
                for pi in rng if by_pref_m[pi])
            for rname, rng in REGIONS)
        body = f"""
<section id="area"><h2>{E(short)}を地域からさがす</h2><p class="sec-sub">都道府県を開いて、市区町村を選んでください。数字は事業所の数です。<a href="{href(rel, "data/near/")}?s={c}">いまいる場所や住所から、近い順にさがす</a></p>
<div class="dir area">{area}</div></section>
<section><div class="kpis">{kpi_html}</div></section>
<section><h2>ランキング</h2>
  <div class="tabs" id="rk-tabs" role="tablist" aria-label="ランキングの種類">
    <button role="tab" data-tab="pref" aria-selected="true">都道府県</button>
    <button role="tab" data-tab="muni" aria-selected="false">市区町村</button>
    <button role="tab" data-tab="corp" aria-selected="false">運営法人</button>
    <button role="tab" data-tab="new" aria-selected="false">新規掲載</button>
    <button role="tab" data-tab="gone" aria-selected="false">掲載終了</button>
  </div>
  <div class="controls">
    <div class="seg" id="rk-sort" role="group" aria-label="並び順">
      <button data-sort="count" aria-pressed="true">事業所数順</button>
      <button data-sort="inc" aria-pressed="false">増加数順</button>
      <button data-sort="dec" aria-pressed="false">減少数順</button>
    </div>
    <select id="rk-pref" aria-label="都道府県で絞り込む"><option value="-1">全国</option></select>
  </div>
  <p class="note" id="rk-note"></p>
  <ol class="rank" id="rk-list"></ol>
  <button class="more" id="rk-more" hidden>さらに表示</button>
</section>
<section><h2>全国の事業所数の推移</h2>{trend_chart(periods, ser)}</section>
{f'<section><h2>同じ分類のサービス</h2><div class="pref-grid">{sib}</div></section>' if sib else ''}"""
        scripts = f'<script id="rank-data" type="application/json">{blob}</script>\n<script src="{rel}assets/rank.js" defer></script>'
        page(path, f"{name}の事業所数ランキング（都道府県・市区町村・法人別）",
             f"{name}の事業所は全国に{cur:,}件（{LATEST_LABEL}時点）。都道府県・市区町村・運営法人別の事業所数と半年間の増減、新規掲載の事業所を毎回更新。",
             hero, body, scripts, split=True,
             og=og_image(f"s/{slug}", group, f"{name}の事業所", cur, "か所",
                         [f"半年で{cur - pv:+,}" if pv is not None else "", f"いちばん多いのは{PREFS[max(range(47), key=lambda i: L['pref'].get(i, 0))]}", "都道府県・市区町村・法人別"]))

    # ---------- 都道府県
    for pi in range(47):
        pref = PREFS[pi]
        path = f"p/{PREF_SLUGS[pi]}/"
        rel = "../../"
        ptot = [pref_total[p][pi] if any(p in S[c] for c in codes) else None for p in periods]
        cur, pv = ptot[-1], ptot[-2]
        rank = sorted(range(47), key=lambda i: -pref_total[latest][i]).index(pi) + 1
        hero = f"""<div>
  {crumbs(rel, [(pref, None)])}
  <div class="eyebrow" style="margin-top:12px">{E(LATEST_LABEL)}時点</div>
  <h1>{E(pref)}の障害福祉事業所</h1>
  <div class="big"><div><span class="n">{cur:,}</span><span class="u">事業所</span></div>
    <div class="meta"><span>半年で <b>{delta(cur - pv)}</b></span><span>全国 <b>{rank}位</b> / 47</span></div></div>
</div>"""
        rows = []
        for gname, cs in GROUPS:
            gr = []
            for c in cs:
                if c not in S:
                    continue
                ser = [pref_at(c, p, pi) for p in periods]
                v, v0 = ser[-1], ser[-2]
                nat_rank = sorted(range(47), key=lambda i: -S[c][latest]["pref"].get(i, 0)).index(pi) + 1
                gr.append(f'<tr><td class="name"><a href="{href(rel, ps_path(pi, c))}">{E(SERVICES[c][1])}</a></td>'
                          f'<td class="n">{v:,}</td><td class="r">{delta(v - v0) if v0 is not None else ""}</td>'
                          f'<td class="r tnum">{nat_rank}位</td><td>{spark(ser, 64, 22)}</td></tr>')
            if gr:
                rows.append(f'<tr class="grp"><td colspan="5">{E(gname)}</td></tr>' + "".join(gr))
        pm = sorted((mk for mk in all_munis if mk[0] == pi), key=lambda mk: -muni_total[latest][mk])
        mmax = max([muni_total[latest][mk] for mk in pm] or [1])
        top3 = ["33", "46", "65"]
        mrows = "".join(
            f'<tr><td class="name"><a href="{href(rel, muni_path(*mk))}">{E(mk[1])}</a></td><td class="n">{muni_total[latest][mk]:,}</td>'
            f'<td class="r">{delta(muni_total[latest][mk] - muni_total[prev][mk])}</td>'
            + "".join(f'<td class="r tnum">{S[c][latest]["muni"].get(mk, 0) if c in S else 0:,}</td>' for c in top3)
            + f'<td class="bar-cell"><div class="bar"><i style="width:{muni_total[latest][mk] / mmax * 100:.1f}%"></i></div></td></tr>'
            for mk in pm)
        body = f"""
<section><h2>サービス別の事業所数</h2><p class="sec-sub">サービス名を押すと、{E(pref)}内の市区町村別・法人別の内訳が見られます。増減は{E(label(prev))}との比較、順位は全国47都道府県の中での順位です。</p>
<div class="table-wrap"><table><thead><tr><th>サービス</th><th class="r">事業所数</th><th class="r">半年の増減</th><th class="r">全国順位</th><th class="r">推移</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>
<section><h2>市区町村別の事業所数</h2><p class="sec-sub">全サービスの延べ事業所数の多い順です。市区町村名から、事業所の一覧が見られます。</p>
<div class="table-wrap"><table><thead><tr><th>市区町村</th><th class="r">延べ</th><th class="r">半年</th><th class="r">GH</th><th class="r">就労B</th><th class="r">放デイ</th><th></th></tr></thead><tbody>{mrows}</tbody></table></div>
<p class="note">GH＝グループホーム（共同生活援助）、就労B＝就労継続支援B型、放デイ＝放課後等デイサービス。</p></section>
<section><h2>{E(pref)}の事業所数の推移</h2>{trend_chart(periods, ptot)}</section>"""
        ptop = sorted(((S[c][latest]["pref"].get(pi, 0), c) for c in codes), reverse=True)[:3]
        pref_og = og_image(f"p/{PREF_SLUGS[pi]}", "都道府県", f"{pref}の障害福祉", cur, "か所",
                           [f"{SERVICES[c][1]} {n:,}か所" for n, c in ptop])
        page(path, f"{pref}の障害福祉事業所数（サービス別・市区町村別）",
             f"{pref}の障害福祉サービス事業所は{cur:,}件（{LATEST_LABEL}時点、延べ）。グループホーム・就労支援・放課後等デイなどサービス別、市区町村別の事業所数と増減。",
             hero, body, og=pref_og)

    # ---------- 都道府県 × サービス
    for c in codes:
        name, short, slug = SERVICES[c]
        L, P_ = S[c][latest], S[c].get(prev)
        nat_order = sorted(range(47), key=lambda i: -L["pref"].get(i, 0))
        by_pref_cur = collections.defaultdict(list)
        for no, o in L["offices"].items():
            by_pref_cur[o["p"]].append((no, o))
        by_pref_prev = collections.defaultdict(list)
        if P_:
            for no, o in P_["offices"].items():
                by_pref_prev[o["p"]].append((no, o))
        for pi in range(47):
            pref = PREFS[pi]
            path = ps_path(pi, c)
            rel = "../../../../"
            ser = [pref_at(c, p, pi) for p in periods]
            cur, pv = ser[-1], ser[-2]
            ya = pref_at(c, year_ago, pi)
            nat_rank = nat_order.index(pi) + 1
            hero = f"""<div>
  {crumbs(rel, [(pref, href(rel, f"p/{PREF_SLUGS[pi]}/")), (short, None)])}
  <div class="eyebrow" style="margin-top:12px">{E(pref)}　{E(LATEST_LABEL)}時点</div>
  <h1>{E(pref)}の{E(name)}</h1>
  <p class="lede">{E(DESC.get(c, ""))}</p>
  <div class="big"><div><span class="n">{cur:,}</span><span class="u">事業所</span></div>
    <div class="meta">{f'<span>半年で <b>{delta(cur - pv)}</b></span>' if pv is not None else ''}{f'<span>1年で <b>{delta(cur - ya)}</b></span>' if ya is not None and year_ago != prev else ''}<span>全国 <b>{nat_rank}位</b> / 47</span></div></div>
</div>"""
            cur_offs, prev_offs = by_pref_cur[pi], by_pref_prev[pi]
            cur_nos = {no for no, _ in cur_offs}
            prev_nos = {no for no, _ in prev_offs}
            new_here = sorted(((no, o) for no, o in cur_offs if no in new_nos[c]), key=lambda x: x[1]["name"])
            gone_here = sorted(((no, o) for no, o in prev_offs if no not in cur_nos), key=lambda x: x[1]["name"])
            corp_c = collections.Counter(o["corp_key"] for _, o in cur_offs)
            corp_p = collections.Counter(o["corp_key"] for _, o in prev_offs)
            cname = {o["corp_key"]: o["corp"] for _, o in prev_offs + cur_offs}
            kpis = [("運営法人", len(corp_c), "法人"), ("半年の新規掲載", len(new_here), "件"), ("半年の掲載終了", len(gone_here), "件")]
            kpi_html = "".join(f'<div class="kpi"><span class="k">{k}</span><span class="v">{v:,}<small>{u}</small></span></div>' for k, v, u in kpis)

            mks = sorted({mk for mk in L["muni"] if mk[0] == pi} | ({mk for mk in P_["muni"] if mk[0] == pi} if P_ else set()),
                         key=lambda mk: (-L["muni"].get(mk, 0), mk[1]))
            mmax = max([L["muni"].get(mk, 0) for mk in mks] or [1]) or 1
            mrows = "".join(
                f'<tr><td class="r tnum">{i + 1}</td><td class="name"><a href="{href(rel, ms_path(*mk, c) if L["muni"].get(mk) else muni_path(*mk))}">{E(mk[1])}</a></td>'
                f'<td class="n">{L["muni"].get(mk, 0):,}</td><td class="r">{delta(L["muni"].get(mk, 0) - (P_["muni"].get(mk, 0) if P_ else 0))}</td>'
                f'<td class="bar-cell"><div class="bar"><i style="width:{L["muni"].get(mk, 0) / mmax * 100:.1f}%"></i></div></td></tr>'
                for i, mk in enumerate(mks))
            corps = sorted(set(corp_c) | set(corp_p), key=lambda k: (-corp_c.get(k, 0), cname[k]))[:20]
            crows = "".join(
                f'<tr><td class="r tnum">{i + 1}</td><td class="name">{corp_link(rel, k, cname[k])}{CORP_CHIP if not corp_p.get(k) and P_ else ""}</td>'
                f'<td class="n">{corp_c.get(k, 0):,}</td><td class="r">{delta(corp_c.get(k, 0) - corp_p.get(k, 0))}</td></tr>'
                for i, k in enumerate(corps) if corp_c.get(k, 0) >= 2 or corp_p.get(k, 0) >= 2)

            def office_li(o, fac=None):
                mk = next(iter(o["munis"]), None)
                place = f'<a href="{href(rel, muni_path(*mk))}">{E(mk[1])}</a>' if mk else ""
                nm = f'<a href="{fac}">{E(display_name(o))}</a>' if fac else E(o["name"])
                return f'<li><b>{nm}</b><span>{E(o["corp"])}{"｜" + place if place else ""}</span></li>'

            new_html = "".join(office_li(o, href(rel, fac_path(no)) if fac_path(no) else None) for no, o in new_here)
            fresh_sec = ""
            if c == GH:
                fr = sorted(((no, o) for no, o in cur_offs if no in FRESH),
                            key=lambda x: (FRESH[x[0]] != latest, next(iter(x[1]["munis"]), (0, ""))[1], display_name(x[1])))
                def fresh_li(no, o):
                    mk = next(iter(o["munis"]), None)
                    place = f'<a href="{href(rel, muni_path(*mk))}">{E(mk[1])}</a>' if mk else ""
                    fp = fac_path(no)
                    nm = f'<a href="{href(rel, fp)}">{E(display_name(o))}</a>' if fp else E(display_name(o))
                    return f'<li><b>{nm}{fresh_chip(no)}</b><span>{E(o["corp"])}{"｜" + place if place else ""}</span></li>'
                items = "".join(fresh_li(no, o) for no, o in fr)
                fresh_sec = (f'<section id="fresh"><h2>最近できた{E(pref)}のグループホーム</h2>'
                             f'<p class="sec-sub">この1年で新しく公表データに載ったグループホームです（{len(fr):,}件）。できたばかりの事業所は入居者を募集していることが多いため、入居先を探す手がかりになります。空き状況は各事業所にお問い合わせください。</p>'
                             + (f'<div class="dir"><details open><summary>1年以内に新規掲載<span class="c">{len(fr):,}件</span></summary><ul>{items}</ul></details></div>' if fr else '<p class="note">この1年で新しく載ったグループホームはありません。</p>')
                             + '</section>')
            gone_html = "".join(office_li(o) for _, o in gone_here)
            others = "".join(
                f'<a href="{href(rel, ps_path(i, c))}"><span>{PREFS[i]}</span><span class="c">{L["pref"].get(i, 0):,}</span></a>'
                for i in range(47) if i != pi)
            siblings = "".join(
                f'<a href="{href(rel, ps_path(pi, x))}"><span>{E(SERVICES[x][1])}</span><span class="c">{S[x][latest]["pref"].get(pi, 0):,}</span></a>'
                for x in codes if x != c)
            body = f"""
<section><div class="kpis">{kpi_html}</div></section>
{fresh_sec}
<section><h2>市区町村別の{E(short)}</h2><p class="sec-sub">{E(pref)}内の事業所数の多い順です。増減は{E(label(prev))}との比較です。市区町村名から、その地域の{E(short)}の一覧が見られます。</p>
{f'<div class="table-wrap"><table><thead><tr><th class="r">順位</th><th>市区町村</th><th class="r">事業所数</th><th class="r">半年の増減</th><th></th></tr></thead><tbody>{mrows}</tbody></table></div>' if mrows else '<p class="note">この都道府県には、市区町村が特定できる事業所の掲載がありません。</p>'}</section>
{f'<section><h2>{E(pref)}で{E(short)}を多く運営する法人</h2><p class="sec-sub">2事業所以上を運営する法人の上位です。</p><div class="table-wrap"><table><thead><tr><th class="r">順位</th><th>法人</th><th class="r">事業所数</th><th class="r">半年の増減</th></tr></thead><tbody>{crows}</tbody></table></div></section>' if crows else ''}
<section><h2>{"この半年の掲載終了" if c == GH else "この半年の新規掲載と掲載終了"}</h2><p class="sec-sub">{E(label(prev))}から{E(LATEST_LABEL)}までの変化です。掲載の有無で判定しているため、実際の開設・廃止日とは一致しない場合があります。</p>
<div class="dir">
  {"" if c == GH else f'<details{" open" if 0 < len(new_here) <= 30 else ""}><summary>新規掲載<span class="c">{len(new_here):,}件</span></summary>{f"<ul>{new_html}</ul>" if new_html else ""}</details>'}
  <details><summary>掲載終了<span class="c">{len(gone_here):,}件</span></summary>{f"<ul>{gone_html}</ul>" if gone_html else ""}</details>
</div></section>
<section><h2>{E(pref)}の{E(short)}の推移</h2>{trend_chart(periods, ser)}</section>
<section><h2>ほかの都道府県の{E(short)}</h2><p class="sec-sub"><a href="{href(rel, f"s/{slug}/")}">全国のランキングを見る</a></p><div class="pref-grid">{others}</div></section>
<section><h2>{E(pref)}のほかのサービス</h2><div class="pref-grid">{siblings}</div></section>"""
            page(path, f"{pref}の{name}の事業所数（市区町村別・法人別）",
                 f"{pref}の{name}の事業所は{cur:,}件（{LATEST_LABEL}時点、全国{nat_rank}位）。市区町村別・運営法人別の事業所数と、半年間の新規掲載・掲載終了の事業所。",
                 hero, body, og=f"og/p/{PREF_SLUGS[pi]}.png")

    # ---------- 市区町村
    order = {c: i for i, c in enumerate(codes)}
    pref_munis = collections.defaultdict(list)
    for mk in all_munis:
        pref_munis[mk[0]].append(mk)
    for mk in all_munis:
        pi, mname = mk
        pref = PREFS[pi]
        path = muni_path(pi, mname)
        rel = "../../../"
        cur, pv = muni_total[latest][mk], muni_total[prev][mk]
        siblings = pref_munis[pi]
        prank = sorted(siblings, key=lambda m: -muni_total[latest][m]).index(mk) + 1
        hero = f"""<div>
  {crumbs(rel, [(pref, href(rel, f"p/{PREF_SLUGS[pi]}/")), (mname, None)])}
  <div class="eyebrow" style="margin-top:12px">{E(pref)}　{E(LATEST_LABEL)}時点</div>
  <h1>{E(mname)}の障害福祉事業所</h1>
  <div class="big"><div><span class="n">{cur:,}</span><span class="u">事業所</span></div>
    <div class="meta"><span>半年で <b>{delta(cur - pv)}</b></span><span>{E(pref)}内 <b>{prank}位</b> / {len(siblings)}</span></div></div>
  <p class="since"><a href="{href(rel, matome_path(pi, mname))}">お仕事で使う方へ：{E(mname)}のまとめ（印刷できます）</a></p>
</div>"""
        rows, dirs = [], []
        for c in sorted((c for c in codes if S[c][latest]["muni"].get(mk) or (prev in S[c] and S[c][prev]["muni"].get(mk))), key=order.get):
            ser = [muni_at(c, p, mk) for p in periods]
            v, v0 = ser[-1], ser[-2]
            within = sorted(siblings, key=lambda m: -S[c][latest]["muni"].get(m, 0))
            r_in = within.index(mk) + 1 if v else None
            rows.append(f'<tr><td class="name"><a href="{href(rel, ms_path(pi, mname, c) if v else ps_path(pi, c))}">{E(SERVICES[c][1])}</a></td>'
                        f'<td class="n">{v:,}</td><td class="r">{delta(v - v0) if v0 is not None else ""}</td>'
                        f'<td class="r tnum">{f"{r_in}位" if r_in else "－"}</td><td>{spark(ser, 64, 22)}</td></tr>')
            offs = [(no, o) for no, o in S[c][latest]["offices"].items() if mk in o["munis"]]
            if offs:
                offs.sort(key=lambda x: (0 if c == GH and x[0] in FRESH else 1, x[1]["munis"][mk][0]))
                def nm(no, o):
                    fp = fac_path(no)
                    t = E(o["munis"][mk][0])
                    return f'<a href="{href(rel, fp)}">{t}</a>' if fp else t
                lis = "".join(
                    f'<li><b>{nm(no, o)}{fresh_chip(no) if c == GH else (NEW_CHIP if no in new_nos[c] else "")}</b>'
                    f'<span>{E(o["corp"])}</span><span>{E(join_addr(mname, o["munis"][mk][1]))}</span></li>'
                    for no, o in offs)
                lis += f'<li class="more-link"><a href="{href(rel, ms_path(pi, mname, c))}">{E(mname)}の{E(SERVICES[c][1])}のページへ</a></li>'
                if c == GH:
                    dirs.insert(0, f'<details id="group-home" open><summary>{E(SERVICES[c][1])}<span class="c">{len(offs):,}件</span></summary><ul>{lis}</ul></details>')
                else:
                    dirs.append(f'<details><summary>{E(SERVICES[c][1])}<span class="c">{len(offs):,}件</span></summary><ul>{lis}</ul></details>')
        near = "".join(f'<a href="{href(rel, muni_path(*m))}"><span>{E(m[1])}</span><span class="c">{muni_total[latest][m]:,}</span></a>'
                       for m in sorted(siblings, key=lambda m: -muni_total[latest][m]) if m != mk)
        body = f"""
<section><h2>{E(mname)}の事業所一覧</h2><p class="sec-sub">事業所名を押すと、電話番号や地図のある詳しいページへ移れます。「新規掲載」の印があるグループホームは最近できた事業所で、入居者を募集している可能性があるため先頭に並べています。ほかのサービスは、サービス名を押すと一覧が開きます。</p>
<div class="dir">{"".join(dirs)}</div></section>
<section><h2>{E(mname)}のサービス別の事業所数</h2><p class="sec-sub">サービス名を押すと、{E(mname)}のそのサービスの事業所が見られます。増減は{E(label(prev))}との比較、順位は{E(pref)}内の市区町村の中での順位です。</p>
<div class="table-wrap"><table><thead><tr><th>サービス</th><th class="r">事業所数</th><th class="r">半年の増減</th><th class="r">県内順位</th><th class="r">推移</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>
<section><h2>{E(pref)}のほかの市区町村</h2><div class="pref-grid">{near}</div></section>"""
        top = sorted(((S[c][latest]["muni"].get(mk, 0), c) for c in codes), reverse=True)[:3]
        tops = "・".join(f"{SERVICES[c][1]}{n}件" for n, c in top if n)
        page(path, f"{mname}（{pref}）の障害福祉事業所一覧と数の推移",
             f"{pref}{mname}の障害福祉サービス事業所は{cur:,}件（{LATEST_LABEL}時点、延べ）。{tops}など。サービス別の事業所数・増減と事業所の一覧。",
             hero, body, og=og_image(f"m/{PREF_SLUGS[pi]}/{mname}", pref, f"{mname}の障害福祉", cur, "か所",
                                     [f"{SERVICES[c][1]} {n:,}か所" for n, c in top if n]))

    # ---------- 市区町村 × サービス
    build_muni_services(S, codes, periods, latest, prev, new_nos, pref_munis)

    # ---------- お仕事で使う方へ（データ）
    build_data(S, codes, periods, latest, prev, year_ago, new_nos)
    build_matome(S, codes, periods, latest, prev, year_ago, new_nos)
    build_corps(S, codes, periods, latest, prev, new_nos)
    build_near(S, codes, latest)
    build_privacy()
    build_reports(S, codes, periods)

    # ---------- 事業所ページ
    build_facilities(S, codes, periods, latest, new_nos)

    draw_og_images()

    # ---------- 404・sitemap・robots
    nf = (OUT / "index.html").read_text(encoding="utf-8")
    base_path = urllib.parse.urlparse(SITE_URL).path.rstrip("/") + "/"
    nf = nf.replace("<head>", f'<head>\n<base href="{base_path}">', 1)
    (OUT / "404.html").write_text(nf, encoding="utf-8")
    # sitemap は1ファイル5万件までなので、分けて書き、sitemap.xml をその目次にする
    chunk = 40000
    parts = []
    for i in range(0, len(PAGES), chunk):
        urls = "".join(f"<url><loc>{E(SITE_URL + '/' + urllib.parse.quote(p))}</loc></url>\n" for p in PAGES[i:i + chunk])
        name = f"sitemap-{i // chunk + 1}.xml"
        (OUT / name).write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}</urlset>\n', encoding="utf-8")
        parts.append(name)
    maps = "".join(f"<sitemap><loc>{SITE_URL}/{n}</loc></sitemap>\n" for n in parts)
    (OUT / "sitemap.xml").write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{maps}</sitemapindex>\n', encoding="utf-8")
    (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n", encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    print(f"ページ数: {len(PAGES):,}")


def join_addr(mname, addr):
    """市区町村名と番地をつなぐ。番地の側に市区町村名が入っているデータもあるので、重ねない。"""
    return addr if addr.startswith(mname) else mname + addr


def svc_label(c):
    return "グループホーム" if c == GH else SERVICES[c][0]


def build_muni_services(S, codes, periods, latest, prev, new_nos, pref_munis):
    """市区町村 × サービスのページ。「札幌市 就労継続支援B型」のように探す人が多い組み合わせ。"""
    rel = "../../../../"
    count = 0
    for c in codes:
        name, short, slug = SERVICES[c]
        L, P_ = S[c][latest], S[c].get(prev)
        by_muni = collections.defaultdict(list)
        for no, o in L["offices"].items():
            for mk in o["munis"]:
                by_muni[mk].append((no, o))
        for mk, offs in by_muni.items():
            pi, mname = mk
            pref = PREFS[pi]
            cur = L["muni"].get(mk, 0)
            pv = P_["muni"].get(mk, 0) if P_ else None
            ser = [S[c][p]["muni"].get(mk, 0) if p in S[c] else None for p in periods]
            within = sorted((m for m in pref_munis[pi] if L["muni"].get(m)), key=lambda m: -L["muni"].get(m, 0))
            r_in = within.index(mk) + 1
            offs.sort(key=lambda x: (0 if c == GH and x[0] in FRESH else 1 if x[0] in new_nos[c] else 2, x[1]["munis"][mk][0]))

            def li(no, o):
                fp = fac_path(no)
                t = E(o["munis"][mk][0])
                chip = fresh_chip(no) if c == GH else (NEW_CHIP if no in new_nos[c] else "")
                return (f'<li><b>{f"""<a href="{href(rel, fp)}">{t}</a>""" if fp else t}{chip}</b>'
                        f'<span>{corp_link(rel, o["corp_key"], o["corp"])}</span><span>{E(join_addr(mname, o["munis"][mk][1]))}</span></li>')
            items = "".join(li(no, o) for no, o in offs)
            n_new = sum(1 for no, _ in offs if no in new_nos[c])
            hero = f"""<div>
  {crumbs(rel, [(pref, href(rel, f"p/{PREF_SLUGS[pi]}/")), (mname, href(rel, muni_path(pi, mname))), (short, None)])}
  <div class="eyebrow" style="margin-top:12px">{E(pref)}{E(mname)}　{E(LATEST_LABEL)}時点</div>
  <h1>{E(mname)}の{E(name)}</h1>
  <p class="lede">{E(DESC.get(c, ""))}</p>
  <div class="big"><div><span class="n">{cur:,}</span><span class="u">事業所</span></div>
    <div class="meta">{f'<span>半年で <b>{delta(cur - pv)}</b></span>' if pv is not None else ''}<span>{E(pref)}内 <b>{r_in}位</b> / {len(within)}</span></div></div>
</div>"""
            near = "".join(f'<a href="{href(rel, ms_path(*m, c))}"><span>{E(m[1])}</span><span class="c">{L["muni"][m]:,}</span></a>'
                           for m in within if m != mk)
            others = "".join(f'<a href="{href(rel, ms_path(pi, mname, x))}"><span>{E(SERVICES[x][1])}</span><span class="c">{S[x][latest]["muni"][mk]:,}</span></a>'
                             for x in codes if x != c and S[x][latest]["muni"].get(mk))
            fresh_note = "「新規掲載」の印がある事業所は最近できた事業所で、入居者を募集している可能性があるため先頭に並べています。" if c == GH else "「新規掲載」の印は、この半年で新しく公表データに載った事業所です。"
            near_url = f'{href(rel, "data/near/")}?s={c}&amp;p={pi}&amp;m={urllib.parse.quote(mname)}'
            tools = (f'<div class="list-tools">'
                     + (f'<input id="flt" class="filter" type="search" placeholder="名前・住所・法人でしぼりこむ" aria-label="事業所をしぼりこむ" data-target="#flt-list"><span class="note" id="flt-note" aria-live="polite"></span>' if len(offs) > 10 else '')
                     + f'<a class="near-link" href="{near_url}">住所やいまいる場所から近い順に見る</a></div>')
            body = f"""
<section><h2>{E(mname)}の{E(short)}の一覧</h2><p class="sec-sub">{cur:,}か所の事業所があります。事業所名を押すと、電話番号や地図のある詳しいページへ移れます。{fresh_note}空き状況や費用などは、各事業所にお問い合わせください。</p>
{tools}
<div class="dir"><details open><summary>{E(short)}<span class="c">{len(offs):,}件</span></summary><ul id="flt-list">{items}</ul></details></div></section>
{area_box(rel, pi, mname, c)}
<section><h2>{E(mname)}の{E(short)}の数の推移</h2>{trend_chart(periods, ser)}</section>
{f'<section><h2>{E(pref)}のほかの市区町村の{E(short)}</h2><p class="sec-sub"><a href="{href(rel, ps_path(pi, c))}">{E(pref)}の{E(short)}のまとめを見る</a></p><div class="pref-grid">{near}</div></section>' if near else ''}
{f'<section><h2>{E(mname)}のほかのサービス</h2><p class="sec-sub"><a href="{href(rel, muni_path(pi, mname))}">{E(mname)}の障害福祉事業所をすべて見る</a></p><div class="pref-grid">{others}</div></section>' if others else ''}"""
            new_txt = f"この半年で{n_new}か所が新しく載りました。" if n_new else ""
            page(ms_path(pi, mname, c), f"{mname}（{pref}）の{name}一覧（{cur:,}か所）",
                 f"{pref}{mname}の{name}の事業所{cur:,}か所の一覧（{LATEST_LABEL}時点）。{new_txt}事業所名・運営法人・所在地と、電話番号や地図のある事業所ページ。",
                 hero, body, f'<script src="{rel}assets/filter.js" defer></script>' if len(offs) > 10 else "", og=muni_og(pi, mname))
            count += 1
    print(f"市区町村×サービスのページ: {count:,}")


def build_facilities(S, codes, periods, latest, new_nos):
    """事業所ページ。1つの事業所番号で複数のサービスを行う事業所は、1ページにまとめる。"""
    first_seen = {}
    for p in periods:
        for c in codes:
            for no in (S[c][p].get("nos") or ()) if p in S[c] else ():
                first_seen.setdefault(no, p)
    offs = collections.defaultdict(dict)   # 事業所番号 → サービス → o（サービスは codes の順）
    for c in codes:
        for no, o in S[c][latest]["offices"].items():
            if no in FAC_PAGES:
                offs[no][c] = o
    main = {no: GH if GH in cs else next(iter(cs)) for no, cs in offs.items()}
    names = {no: display_name(cs[main[no]]) for no, cs in offs.items()}
    by_corp = collections.defaultdict(list)
    by_muni = collections.defaultdict(list)   # (市区町村, サービス) → 事業所番号
    for no, cs in offs.items():
        o = cs[main[no]]
        by_corp[o["corp_key"]].append(no)
        for c, oc in cs.items():
            for mk in oc["munis"]:
                by_muni[(mk, c)].append(no)
    rel = "../../"

    def place_of(no):
        o = offs[no][main[no]]
        return office_place(o)

    for no, cs in offs.items():
        path = fac_path(no)
        m = main[no]
        o = cs[m]
        pi = o["p"]
        pref = PREFS[pi]
        mk = next(iter(o["munis"]), None)
        name = names[no]
        place = pref + (mk[1] if mk else "")
        tel = next((x["tel"] for x in cs.values() if x["tel"]), "")
        url = next((x["url"] for x in cs.values() if x["url"].startswith(("http://", "https://"))), "")
        items = [(pref, href(rel, f"p/{PREF_SLUGS[pi]}/"))]
        if mk:
            items.append((mk[1], href(rel, muni_path(*mk))))
        items.append((name, None))
        fs = first_seen.get(no)
        since = (f"{label(periods[0])}以前から" if fs == periods[0] else f"{label(fs)}から") if fs else ""
        h0 = o["houses"][0] if o["houses"] else (name, "", "", "", "")
        acts = []
        if tel:
            acts.append(f'<a class="act main" href="tel:{E(re.sub(r"[^0-9+]", "", tel))}"><span>電話する</span><b class="tnum">{E(tel)}</b></a>')
        if h0[3] and h0[4]:
            acts.append(f'<a class="act" href="https://www.google.com/maps/search/?api=1&amp;query={urllib.parse.quote(h0[3] + "," + h0[4])}" rel="noopener" target="_blank"><span>地図で見る</span><b>Google マップ</b></a>')
        if url:
            acts.append(f'<a class="act" href="{E(url)}" rel="nofollow noopener" target="_blank"><span>事業所のホームページ</span><b>開く</b></a>')
        actions = f'<div class="actions">{"".join(acts)}</div>' if acts else ""
        labels = "・".join(svc_label(c) for c in cs)
        if list(cs) == [GH]:
            eyebrow = "グループホーム（共同生活援助）"
            lede = f"{o['corp']}が運営する、{place}のグループホームです。"
        else:
            eyebrow = "・".join("グループホーム（共同生活援助）" if c == GH else SERVICES[c][0] for c in cs)
            lede = f"{o['corp']}が運営する、{place}の事業所です。{labels}を行っています。"
        if no in FRESH:
            lede += "この1年で新しく公表データに載ったグループホームで、入居者を募集している可能性があります。"
        hero = f"""<div>
  {crumbs(rel, items)}
  <div class="eyebrow" style="margin-top:12px">{E(eyebrow)}{fresh_chip(no) if GH in cs else (NEW_CHIP if any(no in new_nos[c] for c in cs) else "")}</div>
  <h1>{E(name)}</h1>
  <p class="lede">{E(lede)}</p>
  {actions}
  <p class="since">公表データへの掲載：{E(since)}</p>
</div>"""
        def maplink(lat, lng):
            if lat and lng:
                q = urllib.parse.quote(f"{lat},{lng}")
                return f' <a href="https://www.google.com/maps/search/?api=1&amp;query={q}" rel="noopener" target="_blank">地図</a>'
            return ""

        def svc_link(c):
            mkc = next(iter(cs[c]["munis"]), None)
            t = "共同生活援助（グループホーム）" if c == GH else SERVICES[c][0]
            return f'<a href="{href(rel, ms_path(*mkc, c) if mkc else ps_path(pi, c))}">{E(t)}</a>'
        info = [
            ("事業所名", E(name)),
            ("サービス", "<br>".join(svc_link(c) for c in cs)),
            ("運営法人", corp_link(rel, o["corp_key"], o["corp"]) + (f'<br><span class="note">法人番号 {E(o["corp_key"])}</span>' if o["corp_key"].isdigit() else "")),
            ("所在地", E(h0[1] + h0[2]) + maplink(h0[3], h0[4])),
        ]
        if tel:
            info.append(("電話番号", f'<span class="tnum">{E(tel)}</span>'))
        if url:
            info.append(("ホームページ", f'<a href="{E(url)}" rel="nofollow noopener" target="_blank">{E(url)}</a>'))
        if o["cap"]:
            info.append(("定員", E(o["cap"]) + ("人" if o["cap"].isdigit() else "")))
        if o["hours"]:
            info.append(("利用できる時間（平日）", E(o["hours"])))
        if o["closed"]:
            info.append(("定休日", E(o["closed"])))
        info.append(("事業所番号", f'<span class="tnum">{E(no)}</span>'))
        info_html = "".join(f'<tr><th scope="row">{k}</th><td class="name">{v}</td></tr>' for k, v in info)
        houses = "".join(
            f'<li><b>{E(h[0])}</b><span>{E(h[1] + h[2])}{maplink(h[3], h[4])}</span></li>' for h in o["houses"])
        svcs = "".join(
            f'<li><b>{svc_link(c)}{"" if display_name(cs[c]) == name else f"<span class=note>（{E(display_name(cs[c]))}）</span>"}</b><span>{E(DESC.get(c, ""))}</span></li>'
            for c in cs)

        def fac_links(nos, limit=30):
            nos = sorted((x for x in nos if x != no), key=lambda x: names[x])
            out = "".join(
                f'<li><b><a href="{href(rel, fac_path(x))}">{E(names[x])}</a></b><span>{E("・".join(SERVICES[c][1] for c in offs[x]))}｜{E(offs[x][main[x]]["corp"])}｜{E(place_of(x))}</span></li>'
                for x in nos[:limit])
            return out, len(nos), len(nos) - limit
        corp_html, corp_n, corp_more = fac_links(by_corp[o["corp_key"]])
        muni_html, muni_n, muni_more = fac_links(by_muni[(mk, m)]) if mk else ("", 0, 0)
        short = SERVICES[m][1]
        corp_more_note = f'<p class="note">ほか{corp_more}件は<a href="{href(rel, corp_path(o["corp_key"]))}">法人のページ</a>へ。</p>' if corp_more > 0 and corp_path(o["corp_key"]) else (f'<p class="note">ほか{corp_more}件。</p>' if corp_more > 0 else "")
        body = f"""
<section><h2>基本情報</h2>
<div class="table-wrap"><table class="info"><tbody>{info_html}</tbody></table></div>
<p class="note">{E(LATEST_LABEL)}時点の国の公表データです。空き状況や費用などは、事業所に直接お問い合わせください。</p></section>
<section><h2>行っているサービス</h2><p class="sec-sub">同じ事業所番号で公表されているサービスです。サービス名を押すと、{E(mk[1] if mk else pref)}の同じサービスの事業所が見られます。</p><div class="dir"><details open><summary>サービス<span class="c">{len(cs)}種類</span></summary><ul>{svcs}</ul></details></div></section>
{f'<section><h2>公表データに載っている住居</h2><p class="sec-sub">グループホームは複数の住居を持つことがありますが、公表データにすべての住居が載っているとは限りません。</p><div class="dir"><details open><summary>住居<span class="c">{len(o["houses"])}か所</span></summary><ul>{houses}</ul></details></div></section>' if m == GH and len(o["houses"]) > 1 else ''}
{f'<section><h2>{E(o["corp"])}のほかの事業所</h2><div class="dir"><details open><summary>同じ法人<span class="c">{corp_n}件</span></summary><ul>{corp_html}</ul></details></div>{corp_more_note}</section>' if corp_html else ''}
{f'<section><h2>{E(mk[1])}のほかの{E(short)}</h2><div class="dir"><details><summary>同じ市区町村<span class="c">{muni_n}件</span></summary><ul>{muni_html}</ul></details></div><p class="note"><a href="{href(rel, ms_path(*mk, m))}">{E(mk[1])}の{E(short)}をすべて見る</a></p></section>' if muni_html else ''}"""
        if list(cs) == [GH]:
            title = f"{name}（{place}）のグループホーム情報"
        else:
            title = f"{name}（{place}）の{svc_label(m)}{'など' if len(cs) > 1 else ''}"
        page(path, title,
             f"{name}は{o['corp']}が運営する{place}の{labels}の事業所。所在地、電話番号、同じ法人や同じ地域の事業所。",
             hero, body, og=muni_og(*mk) if mk else "og/top.png")
    print(f"施設ページ: {len(offs):,}")
    muni_list = sorted({mk for c in codes for mk in S[c][latest]["muni"]})
    # 検索用の索引。事業所が多いので8つに分け、検索欄を触ったときに並行して読み込む。
    # 法人名は表にして番号で引き、ファイルを小さくする。
    muni_ix = {mk: i for i, mk in enumerate(muni_list)}
    order = sorted(offs, key=lambda x: (offs[x][main[x]]["p"], names[x]))
    n_parts = 8
    size = -(-len(order) // n_parts)
    parts = []
    for k in range(n_parts):
        corp_ix, corps, fl = {}, [], []
        for no in order[k * size:(k + 1) * size]:
            o = offs[no][main[no]]
            ci = corp_ix.setdefault(o["corp"], len(corps))
            if ci == len(corps):
                corps.append(o["corp"])
            mk = next(iter(o["munis"]), None)
            alt = " ".join(sorted({display_name(x) for x in offs[no].values()} - {names[no]}))
            fl.append([no, names[no], ci, o["p"], muni_ix.get(mk, -1), [codes.index(c) for c in offs[no]]] + ([alt] if alt else []))
        name = f"search/f{k + 1}.json"
        (OUT / "assets" / name).parent.mkdir(parents=True, exist_ok=True)
        (OUT / "assets" / name).write_text(json.dumps(dict(C=corps, f=fl), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        parts.append(name)
    index = dict(P=PREFS, S=PREF_SLUGS, N=[SERVICES[c][1] for c in codes],
                 m=[[mk[1], mk[0]] for mk in muni_list], F=parts)
    (OUT / "assets" / "search.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


CSV_SOURCE = "出典：独立行政法人福祉医療機構 WAM NET「障害福祉サービス等情報公表システムデータのオープンデータ」をもとに ジモふく（https://jimofuku.jp/）が集計"


def write_csv(path: pathlib.Path, note: str, header, rows) -> int:
    """Excel でそのまま開けるよう、BOM つき UTF-8・CRLF で書く。先頭2行は出典と注記。"""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow([CSV_SOURCE])
    w.writerow([note])
    w.writerow(header)
    w.writerows(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8"))
    return path.stat().st_size


def build_data(S, codes, periods, latest, prev, year_ago, new_nos):
    """お仕事で使う方へのページ。くらべ表のもとデータ、半年の動き、CSV を書き出す。"""
    path = "data/"
    rel = "../"
    dd = OUT / "data"
    ya = year_ago if year_ago != prev else None

    def counts(c, p, key, k):
        return S[c][p][key].get(k, 0) if p and p in S[c] else None

    # 運営法人の数（最新時点だけ。事業所の一覧を持っているのは最新と前回だけなので）
    corp_pref = {c: collections.defaultdict(set) for c in codes}
    corp_muni = {c: collections.defaultdict(set) for c in codes}
    for c in codes:
        for o in S[c][latest]["offices"].values():
            corp_pref[c][o["p"]].add(o["corp_key"])
            for mk in o["munis"]:
                corp_muni[c][mk].add(o["corp_key"])

    def all_corps(table, k):
        return len(set().union(*(table[c].get(k, set()) for c in codes)))

    def sum4(vals):
        out = []
        for i in range(3):
            v = [x[i] for x in vals if x[i] is not None]
            out.append(sum(v) if v else None)
        return out

    # 都道府県のくらべ表：サービスごとに [最新, 半年前, 1年前, 運営法人数]
    T = {}
    for c in codes:
        T[c] = [[counts(c, latest, "pref", pi), counts(c, prev, "pref", pi), counts(c, ya, "pref", pi), len(corp_pref[c].get(pi, ()))]
                for pi in range(47)]
    T["all"] = [sum4([T[c][pi] for c in codes]) + [all_corps(corp_pref, pi)] for pi in range(47)]

    # 市区町村のくらべ表は都道府県ごとのファイルにして、選んだときだけ読み込む
    munis_by_pref = collections.defaultdict(set)
    for c in codes:
        for p in (latest, prev):
            if p in S[c]:
                for mk in S[c][p]["muni"]:
                    munis_by_pref[mk[0]].add(mk)
    latest_munis = {mk for c in codes for mk in S[c][latest]["muni"]}
    for pi in range(47):
        rows = []
        for mk in sorted(munis_by_pref[pi], key=lambda m: m[1]):
            sv = {}
            for c in codes:
                v = [counts(c, latest, "muni", mk), counts(c, prev, "muni", mk), counts(c, ya, "muni", mk), len(corp_muni[c].get(mk, ()))]
                if v[0] or v[1] or v[2]:
                    sv[c] = v
            allv = sum4(list(sv.values())) + [all_corps(corp_muni, mk)]
            rows.append([mk[1], href(rel, matome_path(*mk)) if mk in latest_munis else "", allv, sv, pop_of(*mk)])
        f = dd / "m" / f"{PREF_SLUGS[pi]}.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # 半年の動き：新しく載った事業所・載らなくなった事業所。都道府県ごとのファイルにする。
    corps_prev = {c: {o["corp_key"] for o in S[c][prev]["offices"].values()} if prev in S[c] else None for c in codes}
    corps_cur = {c: {o["corp_key"] for o in S[c][latest]["offices"].values()} for c in codes}
    moves = collections.defaultdict(dict)
    move_rows = []
    for c in codes:
        if prev not in S[c]:
            continue
        L, P_ = S[c][latest]["offices"], S[c][prev]["offices"]
        for kind, nos, src in ((1, sorted(new_nos[c]), L), (0, sorted(set(P_) - set(L)), P_)):
            for no in nos:
                o = src[no]
                mk = next(iter(o["munis"]), None)
                name = display_name(o) if o["houses"] else o["name"]
                # 新規：そのサービスを全国でまだしていなかった法人 / 終了：そのサービスを全国でしなくなった法人
                first = int(o["corp_key"] not in (corps_prev[c] if kind else corps_cur[c]))
                fac = href(rel, fac_path(no)) if kind and fac_path(no) else ""
                # 1つの事業所が複数のサービスを始めたときは、画面では1行にまとめる
                r = moves[o["p"]].setdefault((kind, no), [kind, [], mk[1] if mk else "", name, o["corp"], [], "", href(rel, corp_path(o["corp_key"])) if corp_path(o["corp_key"]) else ""])
                r[1].append(c)
                if first:
                    r[5].append(c)
                r[6] = r[6] or fac
                move_rows.append([("新規掲載" if kind else "掲載終了"), SERVICES[c][0], PREFS[o["p"]], mk[1] if mk else "", no, name, o["corp"],
                                  o["corp_key"] if o["corp_key"].isdigit() else "",
                                  ("この法人が初めて行うサービス" if kind else "この法人がこのサービスをすべてやめた") if first else ""])
    for pi in range(47):
        rows = sorted(moves[pi].values(), key=lambda r: (-r[0], r[2], r[3]))
        (dd / "mv" / f"{PREF_SLUGS[pi]}.json").parent.mkdir(parents=True, exist_ok=True)
        (dd / "mv" / f"{PREF_SLUGS[pi]}.json").write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    move_rows.sort(key=lambda r: (r[0] != "新規掲載", PREFS.index(r[2]), r[3], r[1], r[5]))

    # CSV
    plabels = [label(p) for p in periods]
    cnote = "事業所数は事業所番号の数（1つの事業所が複数のサービスを行う場合はサービスごとに数える）。空欄はその時点のデータがないもの。" + (POP_SOURCE + "。" if POP_PREF else "")
    pref_rows = [[PREFS[pi], POP_PREF.get(pi, ""), SERVICES[c][0]] + [("" if p not in S[c] else S[c][p]["pref"].get(pi, 0)) for p in periods]
                 for pi in range(47) for c in codes]
    muni_keys = sorted({mk for c in codes for p in S[c] for mk in S[c][p]["muni"]})
    muni_rows = []
    for mk in muni_keys:
        for c in codes:
            vals = [("" if p not in S[c] else S[c][p]["muni"].get(mk, 0)) for p in periods]
            if any(vals):
                muni_rows.append([PREFS[mk[0]], mk[1], pop_of(*mk) or "", SERVICES[c][0]] + vals)
    gap_rows = [[SERVICES[c][0], PREFS[mk[0]], mk[1]] for c in codes for mk in sorted(latest_munis) if not S[c][latest]["muni"].get(mk)]
    span = f"{prev}-{latest}"
    files = [
        ("todofuken.csv", f"都道府県別・サービス別の事業所数（{plabels[0]}〜{plabels[-1]}）", "都道府県ごと・サービスごとに、各時点の事業所数を並べた表です。",
         write_csv(dd / "todofuken.csv", cnote, ["都道府県", f"人口（{POP_ASOF[0]}）", "サービス"] + plabels, pref_rows), len(pref_rows)),
        ("shikuchoson.csv", f"市区町村別・サービス別の事業所数（{plabels[0]}〜{plabels[-1]}）", "市区町村ごと・サービスごとに、各時点の事業所数を並べた表です。事業所が1か所もない組み合わせは省いています。",
         write_csv(dd / "shikuchoson.csv", cnote + "市区町村は事業所の所在地。", ["都道府県", "市区町村", f"人口（{POP_ASOF[0]}）", "サービス"] + plabels, muni_rows), len(muni_rows)),
        (f"ugoki-{span}.csv", f"半年間に新しく載った事業所・載らなくなった事業所（{label(prev)}→{label(latest)}）", "事業所名・運営法人・所在地の一覧です。その法人が初めて行うサービスかどうかの印もつけています。",
         write_csv(dd / f"ugoki-{span}.csv", f"{label(prev)}と{label(latest)}の公表データをくらべ、掲載の有無で判定。実際の開設・廃止日とは一致しない場合がある。掲載終了には休止や番号の変更も含む。",
                   ["区分", "サービス", "都道府県", "市区町村", "事業所番号", "事業所名", "運営法人", "法人番号", "法人のようす"], move_rows), len(move_rows)),
        ("nai.csv", f"そのサービスが1か所もない市区町村（{LATEST_LABEL}時点）", "サービスごとに、事業所が1か所もない市区町村を並べた表です。",
         write_csv(dd / "nai.csv", f"{LATEST_LABEL}時点で、なんらかの障害福祉サービスの事業所がある市区町村のうち、そのサービスの事業所が所在地にない市区町村。近くの市区町村の事業所を使える場合もある。",
                   ["サービス", "都道府県", "市区町村"], gap_rows), len(gap_rows)),
    ]
    dl_names = {"todofuken.csv": f"ジモふく_都道府県別事業所数_{latest}.csv", "shikuchoson.csv": f"ジモふく_市区町村別事業所数_{latest}.csv",
                f"ugoki-{span}.csv": f"ジモふく_新規掲載と掲載終了_{span}.csv", "nai.csv": f"ジモふく_サービスがない市区町村_{latest}.csv"}
    def size_label(b):
        return f"{b / 1024 / 1024:.1f}MB" if b >= 1024 * 1024 else f"{max(1, b // 1024):,}KB"
    dl_html = "".join(
        f'<li><a class="dl" href="{E(fn)}" download="{E(dl_names[fn])}"><b>{E(t)}</b><span>{E(d)}</span>'
        f'<span class="meta">CSV（Excel で開けます）・{n:,}行・{size_label(size)}</span></a></li>'
        for fn, t, d, size, n in files)

    svc_opts = "".join(f'<optgroup label="{E(g)}">' + "".join(f'<option value="{c}">{E(SERVICES[c][1])}</option>' for c in cs if c in S) + "</optgroup>"
                       for g, cs in GROUPS)
    pref_opts = "".join(f'<option value="{pi}">{PREFS[pi]}</option>' for pi in range(47))
    blob = json.dumps(dict(P=PREFS, S=PREF_SLUGS, T=T, N={c: SERVICES[c][1] for c in codes},
                           pl=[href(rel, f"p/{PREF_SLUGS[pi]}/") for pi in range(47)], pp=[POP_PREF.get(pi) for pi in range(47)],
                           cur=LATEST_LABEL, prev=label(prev), ya=label(ya) if ya else ""),
                      ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    n_new = sum(1 for r in move_rows if r[0] == "新規掲載")
    n_first = sum(1 for r in move_rows if r[0] == "新規掲載" and r[8])
    hero = f"""<div>
  {crumbs(rel, [("お仕事で使う方へ", None)])}
  <div class="eyebrow" style="margin-top:12px">データ　{E(LATEST_LABEL)}時点</div>
  <h1>お仕事で使う方へ</h1>
  <p class="lede">事業所の運営、相談支援、自治体の計画づくりなどに使えるよう、地域ごとの事業所の数と増え方、半年間の動きをまとめました。表はそのままダウンロードできます。</p>
  <div class="big"><div><span class="n">{n_new:,}</span><span class="u">件</span></div>
    <div class="meta"><span>この半年に新しく載った事業所</span><span>うち <b>{n_first:,}件</b> は、その法人が初めて行うサービス</span></div></div>
</div>"""
    body = f"""
<section id="compare"><h2>地域のくらべ表</h2>
<p class="sec-sub">地域とサービスを選ぶと、事業所の数と、半年前・1年前からの増え方を一覧にします。表の見出しを押すと並べ替えられます。</p>
<div class="controls">
  <select id="cp-area" aria-label="地域"><option value="-1">全国（都道府県ごと）</option>{pref_opts}</select>
  <select id="cp-svc" aria-label="サービス"><option value="all">全サービス（延べ）</option>{svc_opts}</select>
</div>
<p class="note" id="cp-note"></p>
<div class="table-wrap"><table class="sortable" id="cp-table"><thead><tr>
  <th><button data-k="name">地域</button></th>
  <th class="r"><button data-k="n">事業所数</button></th>
  <th class="r"><button data-k="d6">半年の増減</button></th>
  <th class="r"><button data-k="d12">1年の増減</button></th>
  <th class="r"><button data-k="r12">1年の増え方</button></th>
  <th class="r"><button data-k="corp">運営法人</button></th>{'<th class="r"><button data-k="per">人口1万人あたり</button></th>' if POP_PREF else ''}
</tr></thead><tbody id="cp-body"></tbody></table></div>
<p class="note">運営法人は、その地域でそのサービスを行う法人の数です（{E(LATEST_LABEL)}時点）。市区町村は事業所の所在地で数えています。{f"人口1万人あたりは、{E(POP_ASOF[0])}の人口で割った数です（{POP_SOURCE}）。" if POP_PREF else ""}</p>
</section>
<section id="moves"><h2>半年の動き</h2>
<p class="sec-sub">{E(label(prev))}から{E(LATEST_LABEL)}までに、新しく公表データに載った事業所と、載らなくなった事業所です。「初めて」の印は、その法人がそのサービスを全国でまだ行っていなかった（新しく始めた）ことを示します。</p>
<div class="controls">
  <select id="mv-pref" aria-label="都道府県"><option value="">都道府県を選んでください</option>{pref_opts}</select>
  <select id="mv-svc" aria-label="サービス"><option value="all">全サービス</option>{svc_opts}</select>
  <div class="seg" id="mv-kind" role="group" aria-label="区分">
    <button data-kind="1" aria-pressed="true">新しく載った</button>
    <button data-kind="0" aria-pressed="false">載らなくなった</button>
  </div>
  <label class="check"><input type="checkbox" id="mv-first"> 初めての法人だけ</label>
</div>
<p class="note" id="mv-note">都道府県を選ぶと一覧が出ます。</p>
<div class="dir"><ul id="mv-list" class="mv-list"></ul></div>
<button class="more" id="mv-more" hidden>さらに表示</button>
<p class="note">掲載の有無で判定しているため、実際の開設・廃止日とは一致しない場合があります。「載らなくなった」には、廃止のほか休止や事業所番号の変更も含みます。</p>
</section>
<section id="gaps"><h2>そのサービスが1か所もない市区町村</h2>
<p class="sec-sub">サービスを選ぶと、その事業所が1か所もない市区町村を一覧にします。開業する場所を考えるときや、地域で足りないサービスを確かめるときに使えます。</p>
<div class="controls">
  <select id="gp-svc" aria-label="サービス"><option value="">サービスを選んでください</option>{svc_opts}</select>
  <select id="gp-pref" aria-label="地域"><option value="-1">全国</option>{pref_opts}</select>
</div>
<p class="note" id="gp-note">サービスを選ぶと一覧が出ます。</p>
<div id="gp-list" class="gp-list"></div>
<p class="note">ここに出るのは、なんらかの障害福祉サービスの事業所がある市区町村です。事業所の所在地で数えているため、近くの市区町村の事業所を使える場合もあります。</p>
</section>
<section id="near-link"><a class="pro-link" href="near/"><span class="eyebrow">地図の場所から</span><b>近くの事業所を、近い順に出す</b><span>住所・市区町村・いまいる場所から、近い事業所を距離つきで並べます。相談支援で利用先の候補を探すときに。</span><i aria-hidden="true">→</i></a></section>
<section id="download"><h2>データのダウンロード</h2>
<p class="sec-sub">どれも CSV という形式で、Excel でそのまま開けます。データが新しくなるたびに作り直しています（年2回、3月末と9月末の時点）。</p>
<ul class="dl-list">{dl_html}</ul>
<div class="howto"><h3>使うときのお願い</h3>
<p>資料や会議などで使うときは、出典として次のように書いてください。ファイルの1行目にも同じ文が入っています。</p>
<p class="cite">{E(CSV_SOURCE)}</p>
<p>事業所ごとの住所・電話番号などすべての項目が必要な場合は、国のもとのデータ（<a href="https://www.wam.go.jp/content/wamnet/pcpub/top/sfkopendata/">WAM NET のオープンデータ</a>）を使ってください。</p></div>
</section>"""
    scripts = f'<script id="data-data" type="application/json">{blob}</script>\n<script src="{rel}assets/data.js" defer></script>'
    page(path, "お仕事で使う方へ｜障害福祉の事業所データ（地域のくらべ表・ダウンロード）",
         f"障害福祉サービス事業所の数を、都道府県・市区町村・サービスごとにくらべられる表と、半年間に新しく載った事業所の一覧。CSV でダウンロードできます（{LATEST_LABEL}時点）。",
         hero, body, scripts,
         og=og_image("data", "お仕事で使う方へ", "障害福祉の事業所データ", n_new, "件",
                     ["この半年に新しく載った事業所", "地域のくらべ表", "CSV でダウンロード"]))
    print(f"データページ: 動き {len(move_rows):,}件、市区町村CSV {len(muni_rows):,}行")


def office_place(o):
    mk = next(iter(o["munis"]), None)
    return PREFS[o["p"]] + (mk[1] if mk else "")


def build_matome(S, codes, periods, latest, prev, year_ago, new_nos):
    """市区町村ごとの「まとめ1枚」。そのまま印刷して会議などで使えるようにする。"""
    ya = year_ago if year_ago != prev else None
    L = {c: S[c][latest] for c in codes}
    munis = sorted({mk for c in codes for mk in L[c]["muni"]})
    by_pref = collections.defaultdict(list)
    for mk in munis:
        by_pref[mk[0]].append(mk)
    # 市区町村ごとの事業所（最新）と、半年で載らなくなった事業所
    here = collections.defaultdict(list)
    gone = collections.defaultdict(list)
    for c in codes:
        for no, o in L[c]["offices"].items():
            for mk in o["munis"]:
                here[mk].append((c, no, o))
        if prev in S[c]:
            for no, o in S[c][prev]["offices"].items():
                if no not in L[c]["offices"]:
                    for mk in o["munis"]:
                        gone[mk].append((c, no, o))
    rank = {}
    for c in codes:
        for pi, ms in by_pref.items():
            order = sorted((m for m in ms if L[c]["muni"].get(m)), key=lambda m: -L[c]["muni"][m])
            for i, m in enumerate(order):
                rank[(c, m)] = (i + 1, len(order))

    def cnt(c, p, mk):
        return S[c][p]["muni"].get(mk, 0) if p and p in S[c] else None

    rel = "../../../"
    for mk in munis:
        pi, mname = mk
        pref = PREFS[pi]
        offs = here[mk]
        tot = sum(L[c]["muni"].get(mk, 0) for c in codes)
        tot0 = sum(cnt(c, prev, mk) or 0 for c in codes)
        tot1 = sum(cnt(c, ya, mk) or 0 for c in codes) if ya else None
        corps = collections.Counter(o["corp_key"] for _, _, o in offs)
        cname = {o["corp_key"]: o["corp"] for _, _, o in offs}
        new_here = [(c, no, o) for c, no, o in offs if no in new_nos[c]]
        mpop = pop_of(pi, mname)
        rows = []
        missing = []
        for gname, cs in GROUPS:
            gr = []
            for c in cs:
                if c not in S:
                    continue
                v = L[c]["muni"].get(mk, 0)
                v0, v1 = cnt(c, prev, mk), cnt(c, ya, mk)
                if not v and not v0:
                    missing.append(SERVICES[c][1])
                    continue
                ncorp = len({o["corp_key"] for cc, _, o in offs if cc == c})
                r = rank.get((c, mk))
                per = (f'<td class="r tnum">{per10k(v, mpop)}<span class="note">（県 {per10k(L[c]["pref"].get(pi, 0), POP_PREF.get(pi))}）</span></td>'
                       if mpop else "")
                gr.append(f'<tr><td class="name">{E(SERVICES[c][1])}</td><td class="n">{v:,}</td>'
                          f'<td class="r">{delta(v - v0) if v0 is not None else ""}</td><td class="r">{delta(v - v1) if v1 is not None else ""}</td>'
                          f'<td class="r tnum">{ncorp:,}</td><td class="r tnum">{f"{r[0]}位 / {r[1]}" if r else "－"}</td>{per}</tr>')
            if gr:
                rows.append(f'<tr class="grp"><td colspan="{7 if mpop else 6}">{E(gname)}</td></tr>' + "".join(gr))
        top = corps.most_common(10)
        top_rows = "".join(f'<tr><td class="r tnum">{i + 1}</td><td class="name">{corp_link(rel, k, cname[k])}</td><td class="n">{n:,}</td></tr>'
                           for i, (k, n) in enumerate(top))

        def li(c, no, o):
            fp = fac_path(no) if no in L[c]["offices"] else None
            nm = o["munis"].get(mk, (o["name"],))[0]
            nm = f'<a href="{href(rel, fp)}">{E(nm)}</a>' if fp else E(nm)
            return f'<li><b>{nm}</b><span>{E(SERVICES[c][1])}｜{corp_link(rel, o["corp_key"], o["corp"])}</span></li>'
        new_html = "".join(li(*x) for x in sorted(new_here, key=lambda x: (codes.index(x[0]), x[2]["name"])))
        gone_html = "".join(li(*x) for x in sorted(gone[mk], key=lambda x: (codes.index(x[0]), x[2]["name"])))
        hero = f"""<div>
  {crumbs(rel, [("お仕事で使う方へ", href(rel, "data/")), (pref, None), (mname, None)])}
  <div class="eyebrow" style="margin-top:12px">{E(pref)}　{E(LATEST_LABEL)}時点のまとめ</div>
  <h1>{E(mname)}の障害福祉のまとめ</h1>
  <div class="big"><div><span class="n">{tot:,}</span><span class="u">事業所（延べ）</span></div>
    <div class="meta"><span>半年で <b>{delta(tot - tot0)}</b></span>{f'<span>1年で <b>{delta(tot - tot1)}</b></span>' if tot1 is not None else ''}<span>運営法人 <b>{len(corps):,}</b></span><span>半年の新規掲載 <b>{len(new_here):,}件</b></span>{f'<span>人口 <b>{mpop:,}人</b>（{E(POP_ASOF[0])}）</span>' if mpop else ''}</div></div>
  <p class="since noprint"><button class="print" onclick="window.print()">このページを印刷する</button>　<a href="{href(rel, muni_path(pi, mname))}">{E(mname)}の事業所の一覧へ</a></p>
</div>"""
        body = f"""
<section><h2>サービス別の事業所数</h2><p class="sec-sub">増減は{E(label(prev))}{f"・{E(label(ya))}" if ya else ""}との比較、順位は{E(pref)}の市区町村の中での順位です。運営法人は、{E(mname)}でそのサービスを行う法人の数です。</p>
<div class="table-wrap"><table><thead><tr><th>サービス</th><th class="r">事業所数</th><th class="r">半年</th><th class="r">1年</th><th class="r">法人</th><th class="r">県内順位</th>{'<th class="r">人口1万人あたり</th>' if mpop else ''}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>
{f'<p class="note"><b>{E(mname)}に事業所がないサービス：</b>{E("、".join(missing))}</p>' if missing else ''}</section>
<section><h2>事業所の多い運営法人</h2><p class="sec-sub">{E(mname)}で運営している事業所の数（サービスごとに数えた延べ数）の多い順です。</p>
<div class="table-wrap"><table><thead><tr><th class="r">順位</th><th>法人</th><th class="r">事業所</th></tr></thead><tbody>{top_rows}</tbody></table></div></section>
<section><h2>この半年の動き</h2><p class="sec-sub">{E(label(prev))}から{E(LATEST_LABEL)}までに、新しく公表データに載った事業所と、載らなくなった事業所です。掲載の有無で判定しているため、実際の開設・廃止日とは一致しない場合があります。</p>
<div class="dir"><details open><summary>新しく載った<span class="c">{len(new_here):,}件</span></summary>{f"<ul>{new_html}</ul>" if new_html else ""}</details>
<details{" open" if len(gone[mk]) <= 20 else ""}><summary>載らなくなった<span class="c">{len(gone[mk]):,}件</span></summary>{f"<ul>{gone_html}</ul>" if gone_html else ""}</details></div></section>
<p class="note">{E(CSV_SOURCE)}（{E(LATEST_LABEL)}時点）。{E(POP_SOURCE) + "（" + E(POP_ASOF[0]) + "）。" if mpop else ""}</p>"""
        page(matome_path(pi, mname), f"{pref}{mname}の障害福祉のまとめ（事業所数・増減・運営法人）",
             f"{pref}{mname}の障害福祉サービス事業所は延べ{tot:,}件（{LATEST_LABEL}時点）。サービス別の事業所数と増減、県内順位、事業所の多い運営法人、半年間に新しく載った事業所。",
             hero, body, og=muni_og(pi, mname))
    print(f"まとめページ: {len(munis):,}")


def build_corps(S, codes, periods, latest, prev, new_nos):
    """法人ごとのページ。どの地域で何を運営しているかを一覧にする。"""
    cur = collections.defaultdict(dict)    # 法人番号 → 事業所番号 → [サービス, ...], o
    for c in codes:
        for no, o in S[c][latest]["offices"].items():
            if o["corp_key"] in CORP_PAGES:
                cur[o["corp_key"]].setdefault(no, [[], o])[0].append(c)
    prevc = collections.defaultdict(collections.Counter)
    gone = collections.defaultdict(list)
    for c in codes:
        if prev not in S[c]:
            continue
        L = S[c][latest]["offices"]
        for no, o in S[c][prev]["offices"].items():
            if o["corp_key"] in CORP_PAGES:
                prevc[o["corp_key"]][c] += 1
                if no not in L:
                    gone[o["corp_key"]].append((c, no, o))
    rel = "../../"
    for k, offs in cur.items():
        first = next(iter(offs.values()))[1]
        name = first["corp"]
        svc = collections.Counter(c for cs, _ in offs.values() for c in cs)
        prefs = collections.Counter(o["p"] for _, o in offs.values())
        n_new = sum(1 for no, (cs, _) in offs.items() for c in cs if no in new_nos[c])
        total, total0 = sum(svc.values()), sum(prevc[k].values())
        srows = "".join(
            f'<tr><td class="name"><a href="{href(rel, "s/" + SERVICES[c][2] + "/")}">{E(SERVICES[c][1])}</a></td><td class="n">{svc[c]:,}</td>'
            f'<td class="r">{delta(svc[c] - prevc[k][c]) if prev in S[c] else ""}</td></tr>'
            for c in codes if svc[c] or prevc[k][c])
        pref_links = "".join(f'<a href="{href(rel, f"p/{PREF_SLUGS[pi]}/")}"><span>{PREFS[pi]}</span><span class="c">{n:,}</span></a>'
                             for pi, n in sorted(prefs.items(), key=lambda x: (-x[1], x[0])))
        groups = collections.defaultdict(list)
        for no, (cs, o) in offs.items():
            mk = next(iter(o["munis"]), None)
            groups[(o["p"], mk[1] if mk else "")].append((no, cs, o))
        dirs = []
        for (pi, mname), items in sorted(groups.items()):
            lis = "".join(
                f'<li><b>{f"""<a href="{href(rel, fac_path(no))}">{E(display_name(o))}</a>""" if fac_path(no) else E(display_name(o) if o["houses"] else o["name"])}'
                f'{NEW_CHIP if any(no in new_nos[c] for c in cs) else ""}</b><span>{E("・".join(SERVICES[c][1] for c in cs))}</span></li>'
                for no, cs, o in sorted(items, key=lambda x: x[2]["name"]))
            dirs.append(f'<details{" open" if len(groups) <= 6 else ""}><summary>{PREFS[pi]}{E(mname)}<span class="c">{len(items):,}か所</span></summary><ul>{lis}</ul></details>')
        gone_html = "".join(f'<li><b>{E(o["name"])}</b><span>{E(SERVICES[c][1])}｜{E(office_place(o))}</span></li>'
                            for c, no, o in sorted(gone[k], key=lambda x: x[2]["name"]))
        hero = f"""<div>
  {crumbs(rel, [("運営法人", None), (name, None)])}
  <div class="eyebrow" style="margin-top:12px">運営法人　{E(LATEST_LABEL)}時点</div>
  <h1>{E(name)}</h1>
  <div class="big"><div><span class="n">{len(offs):,}</span><span class="u">事業所</span></div>
    <div class="meta"><span>サービス <b>{len(svc)}種類</b></span><span><b>{len(prefs)}</b>都道府県</span><span>半年で（延べ） <b>{delta(total - total0)}</b></span></div></div>
  <p class="since">法人番号 {E(k)}　<a href="https://www.houjin-bangou.nta.go.jp/henkorireki-johoto.html?selHouzinNo={E(k)}" rel="noopener" target="_blank">国税庁の法人番号公表サイトで見る</a></p>
</div>"""
        body = f"""
<section><h2>行っているサービス</h2><p class="sec-sub">サービスごとの事業所数です。増減は{E(label(prev))}との比較です。</p>
<div class="table-wrap"><table><thead><tr><th>サービス</th><th class="r">事業所数</th><th class="r">半年の増減</th></tr></thead><tbody>{srows}</tbody></table></div></section>
<section><h2>事業所のある都道府県</h2><div class="pref-grid">{pref_links}</div></section>
<section><h2>事業所の一覧</h2><p class="sec-sub">所在地ごとにまとめています。「新規掲載」の印は、この半年で新しく公表データに載った事業所です。</p><div class="dir">{"".join(dirs)}</div></section>
{f'<section><h2>この半年で載らなくなった事業所</h2><p class="sec-sub">廃止のほか、休止や事業所番号の変更も含みます。</p><div class="dir"><details><summary>載らなくなった<span class="c">{len(gone[k]):,}件</span></summary><ul>{gone_html}</ul></details></div></section>' if gone_html else ''}"""
        page(corp_path(k), f"{name}が運営する障害福祉事業所（{len(offs):,}か所）",
             f"{name}（法人番号{k}）が運営する障害福祉サービスの事業所{len(offs):,}か所の一覧。サービス別の事業所数、事業所のある都道府県、半年間の増減（{LATEST_LABEL}時点）。",
             hero, body)
    print(f"法人ページ: {len(cur):,}")


def build_privacy():
    """アクセス解析で外部に送る情報を知らせるページ。"""
    rel = "../"
    hero = f"""<div>
  {crumbs(rel, [("プライバシーとアクセス解析について", None)])}
  <h1>プライバシーとアクセス解析について</h1>
</div>"""
    ga = f"""<section><h2>アクセス解析（Google アナリティクス）</h2>
<p>{SITE_NAME}では、どのページがよく見られているかを知り、サイトをよくするために、Google LLC の「Google アナリティクス」を使っています。</p>
<div class="table-wrap"><table class="info"><tbody>
<tr><th scope="row">送る先</th><td>Google LLC</td></tr>
<tr><th scope="row">送る情報</th><td>見たページの住所、前に見ていたページ、日時、お使いのブラウザや端末の種類、おおよその地域、Cookie などで作られる識別番号</td></tr>
<tr><th scope="row">使いみち</th><td>アクセス数や、よく見られるページを数えるため。名前や連絡先など、個人を特定する情報は集めていません。</td></tr>
</tbody></table></div>
<p>送られた情報は Google のプライバシーポリシーに沿って扱われます：<a href="https://policies.google.com/technologies/partner-sites?hl=ja" rel="noopener" target="_blank">Google のサービスを使用するサイトやアプリから収集した情報の Google による使用</a></p>
<p>送りたくない場合は、<a href="https://tools.google.com/dlpage/gaoptout?hl=ja" rel="noopener" target="_blank">Google アナリティクス オプトアウト アドオン</a>を使うか、ブラウザの設定で Cookie を止めてください。</p></section>""" if GA_ID else ""
    body = f"""{ga}
<section><h2>「近くの事業所」での位置情報</h2>
<p>「いまいる場所から」を押したときの位置情報は、お使いの端末の中で距離を計算するためだけに使い、どこにも送りません。「住所で」さがすときは、入力した住所を緯度・経度に変えるために、国土地理院の住所検索に送ります。</p></section>
<section><h2>掲載している情報</h2>
<p>事業所の情報は、独立行政法人福祉医療機構 <a href="https://www.wam.go.jp/content/wamnet/pcpub/top/sfkopendata/">WAM NET のオープンデータ</a>をもとにしています。内容は国の公表データのとおりで、{SITE_NAME}が事業所に確認したものではありません。</p></section>"""
    page("privacy/", "プライバシーとアクセス解析について",
         f"{SITE_NAME}のアクセス解析（Google アナリティクス）で送る情報と、位置情報の扱いについて。", hero, body)


SHOP = json.loads((ROOT / "data" / "report" / "shop.json").read_text(encoding="utf-8"))
AREA_PATH = "report/area/"


def area_report_for(pi, mname):
    """市区町村（政令市の区もふくむ）に地域レポートがあれば、その市の設定を返す。"""
    for x in SHOP["cities"]:
        if PREFS[pi] == x["pref"] and (mname == x["city"] or mname.startswith(x["city"]) and mname.endswith("区")):
            return x
    return None


def area_box(rel, pi, mname, c):
    """市区町村 × サービスのページに出す、地域レポートの案内枠。"""
    x = area_report_for(pi, mname)
    ed = next((e for e in SHOP["editions"] if c in e["svcs"]), None)
    if not x or not ed:
        return ""
    return (f'<aside class="area-box"><p class="eyebrow">{E(x["city"])}で{E(svc_label(c))}を開く方へ</p>'
            f'<h2>{E(x["city"])}の地域レポート（{E(ed["name"])}）</h2>'
            f'<p>開くならどの町か、市の手続きと総量規制、収支と要る手元資金までを1冊にまとめた PDF です（{E(SHOP["period"])}のデータ）。</p>'
            f'<a class="area-btn" href="{href(rel, AREA_PATH)}#{urllib.parse.quote(x["city"])}">中身と値段を見る</a></aside>')


def build_area_reports():
    """地域別レポート（有料、BOOTH で販売）の売り場。"""
    rel = "../../"
    one, st = SHOP["price_one"], SHOP["price_set"]

    def buy(x, label_):
        if x["booth"]:
            return f'<a class="area-btn" href="{E(x["booth"])}" rel="noopener" target="_blank">{label_}</a>'
        return '<span class="area-btn off">準備中</span>'
    eds = "".join(
        f'<div><h3>{E(e["name"])}</h3><p class="note">{E(e["for"])}</p><ul>' + "".join(f"<li>{E(s)}</li>" for s in e["points"]) + "</ul></div>"
        for e in SHOP["editions"])
    rows = "".join(
        f'<tr id="{E(x["city"])}"><th>{E(x["city"])}<small>{E(x["pref"])}</small></th>'
        '<td>放デイ・児発／グループホーム／就労B型</td>'
        f'<td>{buy(x, "BOOTH で買う")}</td></tr>'
        for x in SHOP["cities"])
    sample = SHOP.get("sample_pdf")
    hero = f"""<div>
  {crumbs(rel, [("レポート", href(rel, "report/")), ("地域レポート", None)])}
  <div class="eyebrow" style="margin-top:12px">数字ならジモふく　{E(SHOP["period"])}のデータ</div>
  <h1>開くならどこか、がわかる地域レポート</h1>
  <p class="lede">障害福祉の事業所を開こうとしている方に向けて、人口の多い20市区と船橋市について、国の公表データと市の資料から「どの町で開くか」「いくら要るか」「市の決まりはどうか」をまとめた PDF です。サービスごとに3つの版があります。</p>
</div>"""
    body = f"""
<section><h2>値段</h2>
<div class="price-row"><div><span class="n">{one:,}</span><span class="u">円</span><p>1つの市・1つの版</p></div>
<div><span class="n">{st:,}</span><span class="u">円</span><p>1つの市・3つの版のセット</p></div></div>
<p class="note">BOOTH（ピクシブのお店のサービス）でお支払いのあと、すぐに PDF と Excel をダウンロードできます。税こみの値段です。</p></section>
<section><h2>まずは無料の見本を</h2><p class="sec-sub">船橋市の放課後等デイ・児童発達支援版の前半（まとめ・結論・開業までにやること など）を、そのまま読めます。</p>
{f'<a class="area-btn" href="{href(rel, sample)}">見本の PDF を開く（船橋市・前半）</a>' if sample else ''}</section>
<section><h2>3つの版の中身</h2><div class="report-cols area-eds">{eds}</div>
<h3>どの版にも入っているもの</h3><ul>{"".join(f"<li>{E(s)}</li>" for s in SHOP["common"])}</ul></section>
<section><h2>市区の一覧</h2><p class="sec-sub">ボタンを押すと BOOTH の商品ページに移ります。版はそこで選べます。</p>
<div class="table-wrap"><table class="area-table"><thead><tr><th>市区</th><th>版</th><th></th></tr></thead><tbody>{rows}</tbody></table></div></section>
<section><h2>このレポートについて</h2><ul>
<li>事業所の数と場所は、福祉医療機構 WAM NET のオープンデータ（2021年11月から半年ごと）をもとにしています。</li>
<li>手続き・総量規制は、各市の手引きと募集要項を2026年10月に確かめたものです。動き出す前に、必ず市の最新の案内を確かめてください。</li>
<li>収支の試算は、全国の平均的な事業所の数字を市の単価・給料・家賃に合わせた、判断のたたき台です。</li>
<li>レポートの数字は、出典（ジモふく）を書いていただければ引用できます。PDF そのものの再配布はご遠慮ください。</li></ul></section>"""
    page(AREA_PATH, "地域レポート｜開くならどこか、がわかる市区ごとのレポート",
         f"障害福祉の事業所を開く方へ。人口の多い20市区と船橋市について、開くならどの町か・市の手続きと総量規制・収支と要る手元資金をまとめた PDF（{SHOP['period']}のデータ）。",
         hero, body)


def report_path(p):
    return f"report/{p}/"


def build_reports(S, codes, periods):
    """データが新しくなるたびに1本ずつ増える「レポート」。各時点を前の時点・最初の時点とくらべる。"""
    start = periods[0]
    made = []
    for i in range(1, len(periods)):
        p, q = periods[i], periods[i - 1]
        cs = [c for c in codes if p in S[c]]
        made.append((p, build_report(S, cs, periods[: i + 1], p, q, start)))
    made.reverse()
    rel = "../"
    items = "".join(f'<li><a href="{href("", f"{p}/")}"><b>{E(t)}</b><span>{E(label(p))}時点のデータ</span></a></li>'
                    for p, t in made)
    hero = f"""<div>
  {crumbs(rel, [("レポート", None)])}
  <h1>レポート</h1>
  <p class="lede">国の公表データから、障害福祉の事業所がどう変わっているかをまとめています。データが新しくなるたびに1本ずつ増えます。引用・転載は出典（{SITE_NAME}、WAM NET）を書いていただければ自由です。</p>
</div>"""
    page("report/", "レポート｜障害福祉の事業所はどう変わったか",
         "国の公表データから、障害福祉の事業所の増え方・地域の差・入れ替わり・運営法人の変化をまとめたレポート。",
         hero, f'<section><a class="area-promo" href="{href(rel, AREA_PATH)}"><b>地域レポート（有料）</b><span>開くならどの町か・市の手続き・収支を、市区ごとに1冊に。人口の多い20市区と船橋市。無料の見本あり。</span></a></section>'
         f'<section><ul class="report-list">{items}</ul></section>')
    build_area_reports()


def build_report(S, codes, ps, p, q, start):
    path = report_path(p)
    rel = "../../"
    lp, lq, ls = label(p), label(q), label(start)

    def tot(per, cs=codes):
        v = [S[c][per]["n"] for c in cs if per in S[c]]
        return sum(v) if v else None

    series = [tot(x) for x in ps]
    n_p, n_q = series[-1], series[-2]
    first_i = next(i for i, v in enumerate(series) if v)
    n_s = series[first_i]

    # サービス別：最初の時点からの増え方（最初の時点にないサービスは前回から）
    svc = []
    for c in codes:
        a = S[c][p]["n"]
        b = S[c][q]["n"] if q in S[c] else None
        f = S[c][start]["n"] if start in S[c] else None
        svc.append((c, a, b, f))
    grow = sorted([x for x in svc if x[3]], key=lambda x: -(x[1] - x[3]))
    svc_rows = "".join(
        f'<tr><th scope="row"><a href="{href(rel, "s/" + SERVICES[c][2] + "/")}">{E(SERVICES[c][1])}</a></th><td class="num">{a:,}</td>'
        f'<td class="num">{delta(a - b) if b is not None else "—"}</td>'
        f'<td class="num">{delta(a - f) if f is not None else "—"}</td>'
        f'<td class="num">{f"{a / f:.2f}倍" if f else "—"}</td></tr>'
        for c, a, b, f in sorted(svc, key=lambda x: -x[1]))

    # 地域：人口1万人あたり
    pref = collections.Counter()
    pref_s = collections.Counter()
    for c in codes:
        pref.update(S[c][p]["pref"])
        if start in S[c]:
            pref_s.update(S[c][start]["pref"])
    dens = sorted(((pref[pi] / POP_PREF[pi] * 10000, pi) for pi in range(47) if POP_PREF.get(pi)), reverse=True)
    rate = sorted(((pref[pi] / pref_s[pi], pi) for pi in range(47) if pref_s[pi]), reverse=True)

    def plist(items, fmt_):
        return "".join(f'<li><a href="{href(rel, f"p/{PREF_SLUGS[pi]}/")}">{PREFS[pi]}</a> <span class="num">{fmt_(v)}</span></li>' for v, pi in items)

    region_html = ""
    if dens:
        region_html = f"""<section><h2>地域の差</h2>
<p>人口1万人あたりの事業所数（全サービスの延べ）で、多い県と少ない県を並べました。人口は{E(POP_ASOF[0] or "最新")}時点の住民基本台帳人口です。</p>
<div class="report-cols"><div><h3>多い</h3><ol>{plist(dens[:5], lambda v: f"{v:.1f}")}</ol></div>
<div><h3>少ない</h3><ol>{plist(dens[-5:][::-1], lambda v: f"{v:.1f}")}</ol></div>
<div><h3>{E(ls)}から増えた割合が大きい</h3><ol>{plist(rate[:5], lambda v: f"{v:.2f}倍")}</ol></div></div>
<p class="note">いちばん多い{PREFS[dens[0][1]]}と少ない{PREFS[dens[-1][1]]}では、約{dens[0][0] / dens[-1][0]:.1f}倍の差があります。</p></section>"""

    # 入れ替わり：事業所番号で重ねずに数える
    def nos(per):
        out = set()
        for c in codes:
            if per in S[c]:
                out.update(S[c][per]["nos"] or ())
        return out

    now, before = nos(p), nos(q)
    n_new, n_gone = len(now - before), len(before - now)

    # 運営法人の種類
    ct_p, ct_s = collections.Counter(), collections.Counter()
    for c in codes:
        ct_p.update(S[c][p].get("ctype") or {})
        if start in S[c]:
            ct_s.update(S[c][start].get("ctype") or {})
    ct_rows = "".join(
        f'<tr><th scope="row">{E(k.replace("特定非営利活動法人", "NPO法人"))}</th><td class="num">{ct_s[k]:,}</td><td class="num">{ct_p[k]:,}</td><td class="num">{delta(ct_p[k] - ct_s[k])}</td></tr>'
        for k in CORP_FORMS + ["その他"] if ct_p[k] or ct_s[k])
    kk = sum(ct_p.values()) or 1
    top_form = max(CORP_FORMS, key=lambda k: ct_p[k])

    # サービスがない市区町村（その時点のデータに出てくる市区町村のうち）
    munis = set()
    for c in codes:
        munis.update(S[c][p]["muni"])
    gaps = sorted(((sum(1 for mk in munis if not S[c][p]["muni"].get(mk)), c) for c in codes if c in WATCH_GAPS), reverse=True)
    gap_rows = "".join(f'<tr><th scope="row">{E(SERVICES[c][1])}</th><td class="num">{n:,}</td><td class="num">{n / len(munis) * 100:.0f}%</td></tr>' for n, c in gaps)

    g1 = grow[0] if grow else None
    points = [f"全国の事業所（全サービスの延べ）は<b>{n_p:,}か所</b>。前回（{E(lq)}）から{delta(n_p - n_q)}、{E(label(ps[first_i]))}の{n_s:,}か所から<b>{n_p / n_s:.2f}倍</b>になりました。"]
    if g1:
        points.append(f"いちばん増えたのは<b>{E(SERVICES[g1[0]][1])}</b>で、{E(ls)}から{g1[1] - g1[3]:+,}か所（{g1[1] / g1[3]:.2f}倍）。")
    points.append(f"前回から新しく載った事業所は<b>約{n_new:,}か所</b>、載らなくなった事業所は約{n_gone:,}か所（事業所番号で重ねずに数えた数）。")
    points.append(f"運営法人の種類では<b>{E(top_form)}</b>がいちばん多く、全体の{ct_p[top_form] / kk * 100:.0f}%です。")
    if gaps:
        n, c = gaps[0]
        points.append(f"{E(SERVICES[c][1])}は、{len(munis):,}の市区町村のうち<b>{n:,}</b>（{n / len(munis) * 100:.0f}%）で事業所が1か所もありません。")
    title = f"障害福祉の事業所はどう変わったか（{lp}）"
    hero = f"""<div>
  {crumbs(rel, [("レポート", href(rel, "report/")), (lp, None)])}
  <div class="eyebrow" style="margin-top:12px">レポート　{E(lp)}時点のデータ</div>
  <h1>{E(title)}</h1>
  <p class="lede">国の公表データ（WAM NET）から、全国の障害福祉サービスの事業所の増え方、地域の差、入れ替わり、運営法人の変化をまとめました。</p>
</div>"""
    body = f"""
<section><h2>要点</h2><ul class="points">{"".join(f"<li>{x}</li>" for x in points)}</ul></section>
<section><h2>全国の推移</h2><p>全サービスの延べ事業所数です。初期の数字は、公表システムへの登録が行き渡る前のため少なめに出ている可能性があります。</p>
{trend_chart(ps, series, "か所")}</section>
<section><h2>サービス別</h2><p>事業所の多い順です。サービス名を押すと、そのサービスの事業所をさがせます。</p>
<div class="table-wrap"><table><thead><tr><th>サービス</th><th class="num">{E(lp)}</th><th class="num">前回から</th><th class="num">{E(ls)}から</th><th class="num">何倍</th></tr></thead><tbody>{svc_rows}</tbody></table></div></section>
{region_html}
<section><h2>入れ替わり</h2><p>前回（{E(lq)}）から今回までに、新しく載った事業所は<b>約{n_new:,}か所</b>、載らなくなった事業所は<b>約{n_gone:,}か所</b>でした。差し引き{delta(n_new - n_gone)}です。新しく載った事業所の一覧は<a href="{href(rel, "data/")}#moves">お仕事で使う方へ</a>で見られます（最新の時点だけ）。</p></section>
<section><h2>運営法人の種類</h2><p>法人名に含まれる言葉（「株式会社」など）で分けた、おおよその数です（延べ）。</p>
<div class="table-wrap"><table><thead><tr><th>種類</th><th class="num">{E(ls)}</th><th class="num">{E(lp)}</th><th class="num">増減</th></tr></thead><tbody>{ct_rows}</tbody></table></div></section>
<section><h2>サービスがない市区町村</h2><p>この時点のデータに出てくる{len(munis):,}の市区町村のうち、そのサービスの事業所が1か所もない市区町村の数です。データに出てこない市区町村は数えていないので、実際はもう少し多い可能性があります。</p>
<div class="table-wrap"><table><thead><tr><th>サービス</th><th class="num">ない市区町村</th><th class="num">割合</th></tr></thead><tbody>{gap_rows}</tbody></table></div></section>
<section><h2>注意点・出典</h2><ul>
<li>事業所数はサービスごとの延べ数です。1つの事業所が複数のサービスを行うと、それぞれで数えています。</li>
<li>「新しく載った」「載らなくなった」は公表データに載ったかどうかで判断しています。実際の開所日・廃止日とはずれることがあります。</li>
<li>運営法人の種類は法人名の言葉で分けたおおよその分類です。</li>
<li>出典：独立行政法人福祉医療機構 <a href="https://www.wam.go.jp/content/wamnet/pcpub/top/sfkopendata/">WAM NET「障害福祉サービス等情報公表システムデータのオープンデータ」</a>、{E(POP_SOURCE)}。集計は{SITE_NAME}。引用するときは「{SITE_NAME}（jimofuku.jp）調べ」と書いてください。</li>
</ul></section>"""
    page(path, title, f"{lp}時点の国の公表データから。全国{n_p:,}か所の障害福祉の事業所の増え方、地域の差、入れ替わり、運営法人の変化。",
         hero, body, og=og_image(f"report-{p}", "レポート", "障害福祉の事業所はどう変わったか", n_p, "か所",
                                 (f"{label(ps[first_i])}から{n_p / n_s:.2f}倍", f"前回から新しく約{n_new:,}か所")))
    return title


def build_near(S, codes, latest):
    """近くの事業所を近い順に出すページ。事業所の位置を 0.5 度ごとの区画に分けて書き出す。"""
    rel = "../../"
    dd = OUT / "data" / "near"
    offices = {}
    for c in codes:
        for no, o in S[c][latest]["offices"].items():
            h = o["houses"][0] if o["houses"] else None
            try:
                lat, lng = float(h[3]), float(h[4])
            except (TypeError, ValueError, IndexError):
                continue
            if not (20 <= lat <= 46 and 122 <= lng <= 154):
                continue
            r = offices.get(no)
            if r is None:
                fp = fac_path(no)
                cp = corp_path(o["corp_key"])
                r = offices[no] = [round(lat, 5), round(lng, 5), display_name(o) if o["houses"] else o["name"], [], o["corp"],
                                   office_place(o), href(rel, fp) if fp else "", href(rel, cp) if cp else "", o["tel"]]
            r[3].append(c)
    tiles = collections.defaultdict(list)
    centers = collections.defaultdict(list)
    for no, r in offices.items():
        tiles[(int(r[0] * 2), int(r[1] * 2))].append(r)
    for c in codes:
        for o in S[c][latest]["offices"].values():
            for mk, (_, _) in o["munis"].items():
                h = o["houses"][0] if o["houses"] else None
                try:
                    centers[mk].append((float(h[3]), float(h[4])))
                except (TypeError, ValueError, IndexError):
                    pass
    (dd / "t").mkdir(parents=True, exist_ok=True)
    for (a, b), rows in tiles.items():
        (dd / "t" / f"{a}_{b}.json").write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    munis = []
    for mk, pts in sorted(centers.items()):
        pts = [x for x in pts if 20 <= x[0] <= 46 and 122 <= x[1] <= 154]
        if pts:
            # 中央値を使う（住所が間違った事業所に引っぱられないように）
            la = sorted(x[0] for x in pts)[len(pts) // 2]
            ln = sorted(x[1] for x in pts)[len(pts) // 2]
            munis.append([mk[0], mk[1], round(la, 4), round(ln, 4)])
    blob = json.dumps(dict(P=PREFS, N={c: SERVICES[c][1] for c in codes}, M=munis, T=sorted(f"{a}_{b}" for a, b in tiles)), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    svc_opts = "".join(f'<optgroup label="{E(g)}">' + "".join(f'<option value="{c}">{E(SERVICES[c][1])}</option>' for c in cs if c in S) + "</optgroup>"
                       for g, cs in GROUPS)
    pref_opts = "".join(f'<option value="{pi}">{PREFS[pi]}</option>' for pi in range(47))
    hero = f"""<div>
  {crumbs(rel, [("お仕事で使う方へ", href(rel, "data/")), ("近くの事業所", None)])}
  <div class="eyebrow" style="margin-top:12px">{E(LATEST_LABEL)}時点</div>
  <h1>近くの事業所を、近い順に</h1>
  <p class="lede">住所や市区町村、いまいる場所から、近い事業所を距離つきで並べます。距離はまっすぐ測ったもので、道のりとは違います。</p>
</div>"""
    body = f"""
<section><h2>場所を決める</h2>
<div class="near-how">
  <form id="nr-addr" class="near-row"><label for="nr-q">住所で</label><input id="nr-q" type="search" placeholder="例：札幌市中央区北1条西2丁目" autocomplete="street-address"><button>この住所から</button></form>
  <div class="near-row"><label for="nr-pref">市区町村で</label><select id="nr-pref"><option value="">都道府県</option>{pref_opts}</select><select id="nr-muni" disabled><option value="">市区町村</option></select></div>
  <div class="near-row"><button id="nr-geo" type="button">いまいる場所から</button><span class="note">位置情報の利用を許可してください。場所はこの端末の中だけで使い、どこにも送りません。</span></div>
</div>
<p class="note" id="nr-where" aria-live="polite"></p></section>
<section><h2>近い事業所</h2>
<div class="controls"><select id="nr-svc" aria-label="サービス"><option value="all">全サービス</option>{svc_opts}</select></div>
<p class="note" id="nr-note">場所を決めると一覧が出ます。</p>
<div class="dir"><ul id="nr-list" class="mv-list"></ul></div>
<button class="more" id="nr-more" hidden>さらに表示</button>
<p class="note">位置は国の公表データにある緯度・経度です。まれに位置がずれている事業所があります。おおよそ半径40km以内の事業所を出しています。</p></section>"""
    page("data/near/", "近くの障害福祉事業所を近い順にさがす（住所・市区町村・現在地から）",
         "住所や市区町村、現在地から、近くの障害福祉サービス事業所を距離の近い順に一覧にします。グループホーム、就労支援、放課後等デイサービスなどサービスで絞り込めます。",
         hero, body, f'<script id="near-data" type="application/json">{blob}</script>\n<script src="{rel}assets/near.js" defer></script>',
         og=og_image("near", "住所・市区町村・いまいる場所から", "近くの事業所を、近い順に", None, "",
                     ["グループホーム、就労支援、放課後等デイサービスなど", "距離つきで一覧にします"]))
    print(f"近くの事業所: {len(offices):,}か所、区画 {len(tiles):,}")


if __name__ == "__main__":
    if OUT.exists():
        shutil.rmtree(OUT)
    build()
