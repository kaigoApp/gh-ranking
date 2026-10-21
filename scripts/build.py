"""data/slim/ を集計して、静的サイト一式を public/ に書き出す。

  /                         トップ（全サービスの概要、タイル地図、都道府県一覧）
  /s/<サービス>/            サービス別ランキング
  /p/<都道府県>/            都道府県ページ
  /p/<都道府県>/<市区町村>/ 市区町村ページ（事業所の一覧つき）

環境変数 SITE_URL に公開先の URL を入れると、sitemap.xml と canonical に使う。
"""
import collections
import csv
import gzip
import html
import json
import os
import pathlib
import re
import shutil
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from services import GROUPS, PREF_SLUGS, PREFS, SERVICES  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SLIM = ROOT / "data" / "slim"
ASSETS = ROOT / "site" / "assets"
OUT = ROOT / "public"
SITE_URL = os.environ.get("SITE_URL", "https://kaigoapp.github.io/gh-ranking").rstrip("/")
SITE_NAME = "障害福祉事業所ランキング"
GH = "33"  # 住居数を出すのはグループホームだけ

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


def summarize(path: pathlib.Path, keep: bool):
    """1サービス・1時点のファイルを読み、事業所単位にまとめる。"""
    offices = {}
    for r in read_slim(path):
        pi, mname = split_muni(r["muni"], int(r["pref"]))
        o = offices.get(r["no"])
        if o is None:
            o = offices[r["no"]] = dict(p=pi, corp_key=r["corp_key"], corp=r["corp"], name=r["name"],
                                        rows=0, munis={}, houses=[], tel="", url="")
        o["rows"] += 1
        if mname:
            mk = (pi, mname)
            if mk not in o["munis"]:
                o["munis"][mk] = (r["name"], r["addr"])
        if keep:
            o["houses"].append((r["name"], r["muni"], r["addr"], r.get("lat", ""), r.get("lng", "")))
            o["tel"] = o["tel"] or r.get("tel", "")
            o["url"] = o["url"] or r.get("url", "")
    pref = collections.Counter(o["p"] for o in offices.values())
    muni = collections.Counter(mk for o in offices.values() for mk in o["munis"])
    rows = sum(o["rows"] for o in offices.values())
    return dict(n=len(offices), rows=rows, pref=pref, muni=muni, offices=offices if keep else None,
                nos=set(offices) if path.parent.name == GH else None)


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


LOGO = ('<svg viewBox="0 0 26 26" aria-hidden="true"><rect x="1" y="14" width="7" height="11" rx="1.5" fill="var(--accent-on-ink)" opacity=".45"/>'
        '<rect x="9.5" y="8" width="7" height="17" rx="1.5" fill="var(--accent-on-ink)" opacity=".7"/>'
        '<rect x="18" y="1" width="7" height="24" rx="1.5" fill="var(--accent-on-ink)"/></svg>')


def page(path: str, title: str, desc: str, hero: str, body: str, scripts: str = "", split=False) -> None:
    """path は public/ からの相対パス（"s/group-home/" など、末尾スラッシュ）。"""
    depth = path.count("/")
    rel = "../" * depth
    canonical = SITE_URL + "/" + urllib.parse.quote(path)
    full_title = title if title == SITE_NAME else f"{title}｜{SITE_NAME}"
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
<meta name="theme-color" content="#0c2b30">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=BIZ+UDPGothic:wght@400;700&family=Barlow+Semi+Condensed:wght@600;700&family=Zen+Kaku+Gothic+New:wght@700;900&display=swap">
<link rel="stylesheet" href="{rel}assets/site.css">
</head>
<body>
<div class="band">
  <header class="site-head"><div class="container">
    <a class="brand" href="{rel or './'}">{LOGO}<span>{SITE_NAME}</span></a>
    <nav class="site-nav" aria-label="サイト内"><a href="{rel}#services">サービス別</a><a href="{rel}#prefs">都道府県別</a></nav>
  </div></header>
  <div class="hero{' split' if split else ''}"><div class="container">{hero}</div></div>
</div>
<main><div class="container">{body}</div></main>
<footer class="site-foot"><div class="container">
  <div><b>{SITE_NAME}</b>　全国の障害福祉サービス事業所の数と推移を、国の公表データから集計しています。</div>
  <div>出典：独立行政法人福祉医療機構 <a href="https://www.wam.go.jp/content/wamnet/pcpub/top/sfkopendata/">WAM NET「障害福祉サービス等情報公表システムデータのオープンデータ」</a>を加工して作成。最新データ：{E(LATEST_LABEL)}時点。</div>
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


def fac_path(no):
    """グループホームの施設ページ。事業所番号は英数字だけのはずだが、念のため確かめる。"""
    return f"f/{no}/" if re.fullmatch(r"[0-9A-Za-z]{1,20}", no) else None


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
LATEST_LABEL = ""


def build():
    global LATEST_LABEL
    periods, latest, prev, year_ago, S = load()
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
    hero = f"""<div>
  <div class="eyebrow">最新データ　{E(LATEST_LABEL)}時点</div>
  <h1>全国の障害福祉事業所を、地域とサービスで比べる</h1>
  <p class="lede">グループホーム、就労支援、放課後等デイサービスなど{len(codes)}種類のサービスについて、国の公表データから事業所の数と増減を集計しています。データは半年ごとに自動で更新されます。</p>
  <div class="big"><div><span class="n">{tot:,}</span><span class="u">事業所</span></div>
    <div class="meta"><span>半年で <b>{delta(tot - tot0)}</b></span><span>{label(periods[first_i])}の <b>{ratio:.2f}倍</b></span></div></div>
</div>
{tilemap(rates, links, titles, "都道府県別、1年間の事業所数（延べ）の増加率。タップでその都道府県のページへ。", f"{min(rv):+.1f}%", f"{max(rv):+.1f}%")}"""

    cards = []
    for gname, cs in GROUPS:
        items = []
        for c in cs:
            if c not in S:
                continue
            ser = [n_at(c, p) for p in periods]
            cur, pv = ser[-1], ser[-2]
            items.append(f'<a class="card" href="{href("", "s/" + SERVICES[c][2] + "/")}"><span class="t">{E(SERVICES[c][1])}</span>'
                         f'<span class="n">{cur:,}<small>件</small></span>{spark(ser)}'
                         f'<span>{delta(cur - pv) if pv is not None else ""} <span class="d zero" style="font-weight:400;font-family:var(--f-body);font-size:12px">半年</span></span></a>')
        if items:
            cards.append(f'<div class="group"><h3>{E(gname)}</h3><div class="cards">{"".join(items)}</div></div>')

    inc = sorted(all_munis, key=lambda mk: (-(muni_total[latest][mk] - muni_total[prev][mk]), -muni_total[latest][mk]))[:15]
    inc_rows = "".join(
        f'<tr><td class="r tnum">{i + 1}</td><td class="name"><a href="{href("", muni_path(*mk))}">{E(PREFS[mk[0]] + " " + mk[1])}</a></td>'
        f'<td class="n">{muni_total[latest][mk]:,}</td><td class="r">{delta(muni_total[latest][mk] - muni_total[prev][mk])}</td></tr>'
        for i, mk in enumerate(inc))
    prefs_html = "".join(f'<a href="{links[pi]}"><span>{PREFS[pi]}</span><span class="c">{pref_total[latest][pi]:,}</span></a>' for pi in range(47))
    body = f"""
<section id="services"><h2>サービス別の事業所数</h2><p class="sec-sub">カードを開くと、都道府県・市区町村・運営法人のランキングが見られます。数字は{E(LATEST_LABEL)}時点、増減は{E(label(prev))}との比較です。</p>
<div class="groups">{"".join(cards)}</div></section>
<section><h2>この半年で事業所が増えた市区町村</h2><p class="sec-sub">全サービスの延べ事業所数の増加数です。</p>
<div class="table-wrap"><table><thead><tr><th class="r">順位</th><th>市区町村</th><th class="r">事業所数</th><th class="r">半年の増減</th></tr></thead><tbody>{inc_rows}</tbody></table></div></section>
<section id="prefs"><h2>都道府県から探す</h2><p class="sec-sub">数字は全サービスの延べ事業所数です。</p><div class="pref-grid">{prefs_html}</div></section>
<section><h2>全国の事業所数の推移</h2><p class="sec-sub">全サービスの延べ事業所数。初期の数字は、公表システムへの登録が行き渡る前のため少なめに出ている可能性があります。</p>{trend_chart(periods, total_series)}</section>"""
    page("", SITE_NAME, f"全国の障害福祉サービス事業所数（{LATEST_LABEL}時点 {tot:,}件）を、グループホーム・就労支援・放課後等デイなどサービス別、都道府県・市区町村別にランキング。国の公表データから自動更新。",
         hero, body, split=True)

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
        corp = sorted(([cnames[k], corp_cur.get(k, 0), corp_prev.get(k, 0)] for k in set(corp_cur) | set(corp_prev)),
                      key=lambda x: -max(x[1], x[2]))
        corp = [x for x in corp if x[1] >= 5 or x[2] >= 5 or abs(x[1] - x[2]) >= 2][:300]

        def office_row(o, no=None):
            mk = next(iter(o["munis"]), None)
            fac = href(rel, fac_path(no)) if no and c == GH and fac_path(no) else ""
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
        if c == GH:
            kpis.insert(0, ("住居数", L["rows"], "件"))
        kpi_html = "".join(f'<div class="kpi"><span class="k">{k}</span><span class="v">{v:,}<small>{u}</small></span></div>' for k, v, u in kpis)
        siblings = [x for g, cs in GROUPS if c in cs for x in cs if x in S and x != c]
        sib = "".join(f'<a href="{href(rel, "s/" + SERVICES[x][2] + "/")}"><span>{E(SERVICES[x][1])}</span><span class="c">{S[x][latest]["n"]:,}</span></a>' for x in siblings)
        body = f"""
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
             hero, body, scripts, split=True)

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
        page(path, f"{pref}の障害福祉事業所数（サービス別・市区町村別）",
             f"{pref}の障害福祉サービス事業所は{cur:,}件（{LATEST_LABEL}時点、延べ）。グループホーム・就労支援・放課後等デイなどサービス別、市区町村別の事業所数と増減。",
             hero, body)

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
            if c == GH:
                kpis.insert(0, ("住居数", sum(o["rows"] for _, o in cur_offs), "件"))
            kpi_html = "".join(f'<div class="kpi"><span class="k">{k}</span><span class="v">{v:,}<small>{u}</small></span></div>' for k, v, u in kpis)

            mks = sorted({mk for mk in L["muni"] if mk[0] == pi} | ({mk for mk in P_["muni"] if mk[0] == pi} if P_ else set()),
                         key=lambda mk: (-L["muni"].get(mk, 0), mk[1]))
            mmax = max([L["muni"].get(mk, 0) for mk in mks] or [1]) or 1
            mrows = "".join(
                f'<tr><td class="r tnum">{i + 1}</td><td class="name"><a href="{href(rel, muni_path(*mk))}">{E(mk[1])}</a></td>'
                f'<td class="n">{L["muni"].get(mk, 0):,}</td><td class="r">{delta(L["muni"].get(mk, 0) - (P_["muni"].get(mk, 0) if P_ else 0))}</td>'
                f'<td class="bar-cell"><div class="bar"><i style="width:{L["muni"].get(mk, 0) / mmax * 100:.1f}%"></i></div></td></tr>'
                for i, mk in enumerate(mks))
            corps = sorted(set(corp_c) | set(corp_p), key=lambda k: (-corp_c.get(k, 0), cname[k]))[:20]
            crows = "".join(
                f'<tr><td class="r tnum">{i + 1}</td><td class="name">{E(cname[k])}{CORP_CHIP if not corp_p.get(k) and P_ else ""}</td>'
                f'<td class="n">{corp_c.get(k, 0):,}</td><td class="r">{delta(corp_c.get(k, 0) - corp_p.get(k, 0))}</td></tr>'
                for i, k in enumerate(corps) if corp_c.get(k, 0) >= 2 or corp_p.get(k, 0) >= 2)

            def office_li(o, fac=None):
                mk = next(iter(o["munis"]), None)
                place = f'<a href="{href(rel, muni_path(*mk))}">{E(mk[1])}</a>' if mk else ""
                nm = f'<a href="{fac}">{E(display_name(o))}</a>' if fac else E(o["name"])
                return f'<li><b>{nm}</b><span>{E(o["corp"])}{"｜" + place if place else ""}</span></li>'

            new_html = "".join(office_li(o, href(rel, fac_path(no)) if c == GH and fac_path(no) else None) for no, o in new_here)
            gone_html = "".join(office_li(o) for _, o in gone_here)
            others = "".join(
                f'<a href="{href(rel, ps_path(i, c))}"><span>{PREFS[i]}</span><span class="c">{L["pref"].get(i, 0):,}</span></a>'
                for i in range(47) if i != pi)
            siblings = "".join(
                f'<a href="{href(rel, ps_path(pi, x))}"><span>{E(SERVICES[x][1])}</span><span class="c">{S[x][latest]["pref"].get(pi, 0):,}</span></a>'
                for x in codes if x != c)
            body = f"""
<section><div class="kpis">{kpi_html}</div></section>
<section><h2>市区町村別の{E(short)}</h2><p class="sec-sub">{E(pref)}内の事業所数の多い順です。増減は{E(label(prev))}との比較です。市区町村名から、その地域の事業所の一覧が見られます。</p>
{f'<div class="table-wrap"><table><thead><tr><th class="r">順位</th><th>市区町村</th><th class="r">事業所数</th><th class="r">半年の増減</th><th></th></tr></thead><tbody>{mrows}</tbody></table></div>' if mrows else '<p class="note">この都道府県には、市区町村が特定できる事業所の掲載がありません。</p>'}</section>
{f'<section><h2>{E(pref)}で{E(short)}を多く運営する法人</h2><p class="sec-sub">2事業所以上を運営する法人の上位です。</p><div class="table-wrap"><table><thead><tr><th class="r">順位</th><th>法人</th><th class="r">事業所数</th><th class="r">半年の増減</th></tr></thead><tbody>{crows}</tbody></table></div></section>' if crows else ''}
<section><h2>この半年の新規掲載と掲載終了</h2><p class="sec-sub">{E(label(prev))}から{E(LATEST_LABEL)}までの変化です。掲載の有無で判定しているため、実際の開設・廃止日とは一致しない場合があります。</p>
<div class="dir">
  <details{" open" if 0 < len(new_here) <= 30 else ""}><summary>新規掲載<span class="c">{len(new_here):,}件</span></summary>{f"<ul>{new_html}</ul>" if new_html else ""}</details>
  <details><summary>掲載終了<span class="c">{len(gone_here):,}件</span></summary>{f"<ul>{gone_html}</ul>" if gone_html else ""}</details>
</div></section>
<section><h2>{E(pref)}の{E(short)}の推移</h2>{trend_chart(periods, ser)}</section>
<section><h2>ほかの都道府県の{E(short)}</h2><p class="sec-sub"><a href="{href(rel, f"s/{slug}/")}">全国のランキングを見る</a></p><div class="pref-grid">{others}</div></section>
<section><h2>{E(pref)}のほかのサービス</h2><div class="pref-grid">{siblings}</div></section>"""
            page(path, f"{pref}の{name}の事業所数（市区町村別・法人別）",
                 f"{pref}の{name}の事業所は{cur:,}件（{LATEST_LABEL}時点、全国{nat_rank}位）。市区町村別・運営法人別の事業所数と、半年間の新規掲載・掲載終了の事業所。",
                 hero, body)

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
</div>"""
        rows, dirs = [], []
        for c in sorted((c for c in codes if S[c][latest]["muni"].get(mk) or (prev in S[c] and S[c][prev]["muni"].get(mk))), key=order.get):
            ser = [muni_at(c, p, mk) for p in periods]
            v, v0 = ser[-1], ser[-2]
            within = sorted(siblings, key=lambda m: -S[c][latest]["muni"].get(m, 0))
            r_in = within.index(mk) + 1 if v else None
            rows.append(f'<tr><td class="name"><a href="{href(rel, ps_path(pi, c))}">{E(SERVICES[c][1])}</a></td>'
                        f'<td class="n">{v:,}</td><td class="r">{delta(v - v0) if v0 is not None else ""}</td>'
                        f'<td class="r tnum">{f"{r_in}位" if r_in else "－"}</td><td>{spark(ser, 64, 22)}</td></tr>')
            offs = [(no, o) for no, o in S[c][latest]["offices"].items() if mk in o["munis"]]
            if offs:
                offs.sort(key=lambda x: x[1]["munis"][mk][0])
                def nm(no, o):
                    fp = fac_path(no) if c == GH else None
                    t = E(o["munis"][mk][0])
                    return f'<a href="{href(rel, fp)}">{t}</a>' if fp else t
                lis = "".join(
                    f'<li><b>{nm(no, o)}{NEW_CHIP if no in new_nos[c] else ""}</b>'
                    f'<span>{E(o["corp"])}</span><span>{E(mname + o["munis"][mk][1])}</span></li>'
                    for no, o in offs)
                dirs.append(f'<details><summary>{E(SERVICES[c][1])}<span class="c">{len(offs):,}件</span></summary><ul>{lis}</ul></details>')
        near = "".join(f'<a href="{href(rel, muni_path(*m))}"><span>{E(m[1])}</span><span class="c">{muni_total[latest][m]:,}</span></a>'
                       for m in sorted(siblings, key=lambda m: -muni_total[latest][m]) if m != mk)
        body = f"""
<section><h2>サービス別の事業所数</h2><p class="sec-sub">サービス名を押すと、{E(pref)}全体の内訳が見られます。増減は{E(label(prev))}との比較、順位は{E(pref)}内の市区町村の中での順位です。</p>
<div class="table-wrap"><table><thead><tr><th>サービス</th><th class="r">事業所数</th><th class="r">半年の増減</th><th class="r">県内順位</th><th class="r">推移</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>
<section><h2>{E(mname)}の事業所一覧</h2><p class="sec-sub">サービス名を押すと、その事業所の一覧が開きます。グループホームは住居ごとではなく事業所ごとに載せており、名前から施設の詳しいページへ移れます。</p>
<div class="dir">{"".join(dirs)}</div></section>
<section><h2>{E(pref)}のほかの市区町村</h2><div class="pref-grid">{near}</div></section>"""
        top = sorted(((S[c][latest]["muni"].get(mk, 0), c) for c in codes), reverse=True)[:3]
        tops = "・".join(f"{SERVICES[c][1]}{n}件" for n, c in top if n)
        page(path, f"{mname}（{pref}）の障害福祉事業所一覧と数の推移",
             f"{pref}{mname}の障害福祉サービス事業所は{cur:,}件（{LATEST_LABEL}時点、延べ）。{tops}など。サービス別の事業所数・増減と事業所の一覧。",
             hero, body)

    # ---------- グループホームの施設ページ
    if GH in S:
        build_facilities(S, codes, periods, latest, new_nos)

    # ---------- 404・sitemap・robots
    nf = (OUT / "index.html").read_text(encoding="utf-8")
    base_path = urllib.parse.urlparse(SITE_URL).path.rstrip("/") + "/"
    nf = nf.replace("<head>", f'<head>\n<base href="{base_path}">', 1)
    (OUT / "404.html").write_text(nf, encoding="utf-8")
    urls = "".join(f"<url><loc>{E(SITE_URL + '/' + urllib.parse.quote(p))}</loc></url>\n" for p in PAGES)
    (OUT / "sitemap.xml").write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}</urlset>\n', encoding="utf-8")
    (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n", encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    print(f"ページ数: {len(PAGES):,}")


def build_facilities(S, codes, periods, latest, new_nos):
    L = S[GH][latest]
    first_seen = {}
    for p in periods:
        nos = S[GH][p].get("nos") if p in S[GH] else None
        for no in nos or ():
            first_seen.setdefault(no, p)
    by_corp = collections.defaultdict(list)
    by_muni = collections.defaultdict(list)
    for no, o in L["offices"].items():
        by_corp[o["corp_key"]].append(no)
        for mk in o["munis"]:
            by_muni[mk].append(no)
    names = {no: display_name(o) for no, o in L["offices"].items()}
    rel = "../../"
    count = 0
    for no, o in L["offices"].items():
        path = fac_path(no)
        if not path:
            continue
        pi = o["p"]
        pref = PREFS[pi]
        mk = next(iter(o["munis"]), None)
        name = names[no]
        place = pref + (mk[1] if mk else "")
        items = [(pref, href(rel, f"p/{PREF_SLUGS[pi]}/"))]
        if mk:
            items.append((mk[1], href(rel, muni_path(*mk))))
        items.append((name, None))
        fs = first_seen.get(no)
        since = (f"{label(periods[0])}以前から" if fs == periods[0] else f"{label(fs)}から") if fs else ""
        hero = f"""<div>
  {crumbs(rel, items)}
  <div class="eyebrow" style="margin-top:12px">グループホーム（共同生活援助）{NEW_CHIP if no in new_nos[GH] else ""}</div>
  <h1>{E(name)}</h1>
  <p class="lede">{E(o["corp"])}が運営する、{E(place)}のグループホームです。</p>
  <div class="big"><div><span class="n">{len(o["houses"])}</span><span class="u">住居</span></div>
    <div class="meta"><span>公表データへの掲載 <b>{E(since)}</b></span></div></div>
</div>"""
        h0 = o["houses"][0] if o["houses"] else (name, "", "", "", "")
        def maplink(lat, lng):
            if lat and lng:
                q = urllib.parse.quote(f"{lat},{lng}")
                return f' <a href="https://www.google.com/maps/search/?api=1&amp;query={q}" rel="noopener" target="_blank">地図</a>'
            return ""
        info = [
            ("事業所名", E(name)),
            ("サービス", f'<a href="{href(rel, ps_path(pi, GH))}">共同生活援助（グループホーム）</a>'),
            ("運営法人", E(o["corp"]) + (f'<br><span class="note">法人番号 {E(o["corp_key"])}</span>' if o["corp_key"].isdigit() else "")),
            ("所在地", E(h0[1] + h0[2]) + maplink(h0[3], h0[4])),
        ]
        if o["tel"]:
            info.append(("電話番号", f'<span class="tnum">{E(o["tel"])}</span>'))
        if o["url"].startswith(("http://", "https://")):
            info.append(("ホームページ", f'<a href="{E(o["url"])}" rel="nofollow noopener" target="_blank">{E(o["url"])}</a>'))
        info.append(("事業所番号", f'<span class="tnum">{E(no)}</span>'))
        info_html = "".join(f'<tr><th scope="row">{k}</th><td class="name">{v}</td></tr>' for k, v in info)
        houses = "".join(
            f'<li><b>{E(h[0])}</b><span>{E(h[1] + h[2])}{maplink(h[3], h[4])}</span></li>' for h in o["houses"])
        other_svcs = "".join(
            f'<a href="{href(rel, ps_path(pi, c))}"><span>{E(SERVICES[c][1])}</span><span class="c">{E(pref)}</span></a>'
            for c in codes if c != GH and no in S[c][latest]["offices"])
        def fac_links(nos, limit=30):
            nos = sorted((x for x in nos if x != no and fac_path(x)), key=lambda x: names[x])
            more = len(nos) - limit
            out = "".join(
                f'<li><b><a href="{href(rel, fac_path(x))}">{E(names[x])}</a></b><span>{E(L["offices"][x]["corp"])}｜{E(PREFS[L["offices"][x]["p"]] + (next(iter(L["offices"][x]["munis"]))[1] if L["offices"][x]["munis"] else ""))}</span></li>'
                for x in nos[:limit])
            return out, more
        corp_html, corp_more = fac_links(by_corp[o["corp_key"]])
        muni_html, muni_more = fac_links(by_muni[mk]) if mk else ("", 0)
        body = f"""
<section><h2>基本情報</h2>
<div class="table-wrap"><table class="info"><tbody>{info_html}</tbody></table></div>
<p class="note">{E(LATEST_LABEL)}時点の国の公表データです。空き状況や費用などは、事業所に直接お問い合わせください。</p></section>
<section><h2>住居の一覧</h2><p class="sec-sub">この事業所が運営する住居です（{len(o["houses"])}か所）。</p>
<div class="dir"><details open><summary>住居<span class="c">{len(o["houses"])}か所</span></summary><ul>{houses}</ul></details></div></section>
{f'<section><h2>この事業所が行うほかのサービス</h2><p class="sec-sub">同じ事業所番号で公表されているサービスです。</p><div class="pref-grid">{other_svcs}</div></section>' if other_svcs else ''}
{f'<section><h2>{E(o["corp"])}のほかのグループホーム</h2><div class="dir"><details open><summary>同じ法人<span class="c">{len(by_corp[o["corp_key"]]) - 1}件</span></summary><ul>{corp_html}</ul></details></div>{f"<p class=note>ほか{corp_more}件。</p>" if corp_more > 0 else ""}</section>' if corp_html else ''}
{f'<section><h2>{E(mk[1])}のほかのグループホーム</h2><div class="dir"><details><summary>同じ市区町村<span class="c">{len(by_muni[mk]) - 1}件</span></summary><ul>{muni_html}</ul></details></div>{f"<p class=note>ほか{muni_more}件は<a href={chr(34)}{href(rel, muni_path(*mk))}{chr(34)}>{E(mk[1])}のページ</a>へ。</p>" if muni_more > 0 else ""}</section>' if muni_html else ''}"""
        page(path, f"{name}（{place}）のグループホーム情報",
             f"{name}は{o['corp']}が運営する{place}のグループホーム（共同生活援助）。住居{len(o['houses'])}か所の所在地、電話番号、同じ法人や同じ地域のグループホーム。",
             hero, body)
        count += 1
    print(f"施設ページ: {count:,}")


if __name__ == "__main__":
    if OUT.exists():
        shutil.rmtree(OUT)
    build()
