"""地域別レポート（有料版）を市区町村ごとに HTML で書き出す。PDF にするのは scripts/report_pdf.js。

  python scripts/regional_report.py <出力フォルダ> <境界データのフォルダ> [都道府県:市区町村 ...]

境界データは国土数値情報の行政区域（N03）を GeoJSON にしたもの
（https://github.com/smartnews-smri/japan-topography の N03-21_<都道府県番号>_210101.json）。
市区町村を指定しなければ TARGETS の全部を作る。サイト（public/）には出さない。
"""
import collections
import csv
import datetime
import gzip
import html
import json
import math
import pathlib
import re
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import build as B  # noqa: E402
from services import PREFS, SERVICES  # noqa: E402

# 人口の多い20市区（2026年1月1日）＋見本の船橋市
TARGETS = [
    ("神奈川県", "横浜市"), ("大阪府", "大阪市"), ("愛知県", "名古屋市"), ("北海道", "札幌市"), ("福岡県", "福岡市"),
    ("神奈川県", "川崎市"), ("兵庫県", "神戸市"), ("京都府", "京都市"), ("埼玉県", "さいたま市"), ("広島県", "広島市"),
    ("宮城県", "仙台市"), ("千葉県", "千葉市"), ("東京都", "世田谷区"), ("福岡県", "北九州市"), ("大阪府", "堺市"),
    ("静岡県", "浜松市"), ("新潟県", "新潟市"), ("東京都", "練馬区"), ("東京都", "大田区"), ("熊本県", "熊本市"),
    ("千葉県", "船橋市"),
]
MAIN = ["65", "63", "46", "33", "22", "52", "60", "45", "11", "24"]  # くわしく見るサービス
MAPS = ["65", "63", "46", "33"]  # 近くにない地域の地図を作るサービス

# サービス別の版。svcs は「ねらい目」と地図を出すサービス、main は収支試算の中心のサービス
EDITIONS = {
    "child": dict(name="放課後等デイ・児童発達支援版", short="放デイ・児発版", svcs=["65", "63"], main="65", mgr="児童発達支援管理責任者"),
    "gh": dict(name="グループホーム版", short="グループホーム版", svcs=["33"], main="33", mgr="サービス管理責任者"),
    "b": dict(name="就労継続支援B型版", short="就労B型版", svcs=["46"], main="46", mgr="サービス管理責任者"),
}
MAPCACHE = {}
INV = {}  # いま作っている版の投資の判断（まとめで使う）
SITE = {}  # いま作っている版の立地の点数
CAP = ["65", "63", "46", "22", "45", "60"]  # 定員を足すサービス（グループホームは定員のデータがない）
E = html.escape


def short(c):
    return SERVICES[c][1]


def lname(c):
    """本文で使う長い名前。グループホームは通称を前に出す。"""
    return "グループホーム（共同生活援助）" if c == "33" else SERVICES[c][0]


def in_city(mk, pi, city):
    """政令市は区ごとに数えているので、区もまとめて市に入れる。"""
    return mk[0] == pi and (mk[1] == city or (city.endswith("市") and mk[1].startswith(city) and mk[1].endswith("区")))


def unit_of(mk):
    """近くの市と比べるときの単位。政令市の区は市にまとめる。"""
    m = re.match(r"(.+?市)(.+区)$", mk[1])
    return (mk[0], m.group(1)) if m and mk[0] != PREFS.index("東京都") else mk


# ---------------------------------------------------------------- 地図

def load_rings(geo_dir, pi, city):
    f = pathlib.Path(geo_dir) / f"N03_{pi + 1:02d}.json"
    g = json.loads(f.read_text(encoding="utf-8"))
    rings = []
    for ft in g["features"]:
        pr = ft["properties"]
        if pr["N03_004"] != city and pr["N03_003"] != city:
            continue
        gm = ft["geometry"]
        for poly in (gm["coordinates"] if gm["type"] == "MultiPolygon" else [gm["coordinates"]]):
            r = poly[0]
            xs, ys = [p[0] for p in r], [p[1] for p in r]
            rings.append((r, (min(xs), min(ys), max(xs), max(ys))))
    if not rings:
        raise SystemExit(f"{city} の境界が {f.name} にありません")
    return rings


def inside(rings, x, y):
    c = False
    for r, bb in rings:
        if not (bb[0] <= x <= bb[2] and bb[1] <= y <= bb[3]):
            continue
        j = len(r) - 1
        for i in range(len(r)):
            xi, yi = r[i]
            xj, yj = r[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                c = not c
            j = i
    return c


class Grid:
    """近い点をすばやく探すためのます目（1ます約0.5km）。"""

    def __init__(self, pts, kx, ky, size=0.5):
        self.kx, self.ky, self.s = kx, ky, size
        self.b = collections.defaultdict(list)
        for p in pts:
            self.b[self.key(p[0], p[1])].append(p)

    def key(self, x, y):
        return int(math.floor(x * self.kx / self.s)), int(math.floor(y * self.ky / self.s))

    def nearest(self, x, y, limit=8.0):
        kx0, ky0 = self.key(x, y)
        best = None
        for rad in range(0, int(limit / self.s) + 1):
            for i in range(-rad, rad + 1):
                for j in range(-rad, rad + 1):
                    if max(abs(i), abs(j)) != rad:
                        continue
                    for p in self.b.get((kx0 + i, ky0 + j), ()):
                        d = math.hypot((p[0] - x) * self.kx, (p[1] - y) * self.ky)
                        if best is None or d < best:
                            best = d
            if best is not None and best <= rad * self.s:
                return best
        return best if best is not None else limit

    def any_within(self, x, y, d):
        kx0, ky0 = self.key(x, y)
        r = int(d / self.s) + 1
        for i in range(-r, r + 1):
            for j in range(-r, r + 1):
                for p in self.b.get((kx0 + i, ky0 + j), ()):
                    if math.hypot((p[0] - x) * self.kx, (p[1] - y) * self.ky) < d:
                        return True
        return False


def town_of(addr_muni, addr, city):
    """「大阪府大阪市北区」「梅田1丁目…」→「北区梅田」。"""
    if city not in addr_muni:
        return ""
    ward = ""
    m = re.search(re.escape(city) + r"(.+区)$", addr_muni)
    if m:
        ward = m.group(1)
    a = addr.replace("大字", "").strip()
    a = a[len(city):] if a.startswith(city) else a
    a = a[len(ward):] if ward and a.startswith(ward) else a
    t = re.split(r"[0-9０-９\-－ー−]|[一二三四五六七八九十]+丁|[一二三四五六七八九十]+条|番地|字", a)[0]
    t = t.strip()
    return (ward + t) if t else ""


def gap_map(rows, rings, code, city):
    """サービスの事業所から遠い場所を、0.25〜0.5km のます目で調べる。"""
    xs = [p[0] for r, _ in rings for p in r]
    ys = [p[1] for r, _ in rings for p in r]
    bb = (min(xs), min(ys), max(xs), max(ys))
    lat0 = (bb[1] + bb[3]) / 2
    kx, ky = 111.32 * math.cos(math.radians(lat0)), 110.95
    area_bb = (bb[2] - bb[0]) * kx * (bb[3] - bb[1]) * ky
    step = max(0.25, math.sqrt(area_bb / 9000))
    pad = 0.06

    def near(r):
        return bb[0] - pad < r[0] < bb[2] + pad and bb[1] - pad < r[1] < bb[3] + pad

    svc = [r for r in rows[code] if near(r)]
    others = [r for c, rs in rows.items() for r in rs if near(r)]
    gs, go = Grid(svc, kx, ky), Grid(others, kx, ky)
    dx, dy = step / kx, step / ky
    cells = []
    y = bb[1] + dy / 2
    while y < bb[3]:
        x = bb[0] + dx / 2
        while x < bb[2]:
            if inside(rings, x, y):
                cells.append((x, y, gs.nearest(x, y), go.any_within(x, y, 0.5)))
            x += dx
        y += dy
    n = len(cells)
    far = [c for c in cells if c[2] > 1.0]
    live = [c for c in cells if c[3]]
    live_far = [c for c in far if c[3]]
    # 住宅地の空白をまとまりに分ける
    idx = {(int((c[0] - bb[0]) / dx), int((c[1] - bb[1]) / dy)): c for c in live_far}
    seen, groups = set(), []
    for k in idx:
        if k in seen:
            continue
        st, mem = [k], []
        seen.add(k)
        while st:
            u = st.pop()
            mem.append(idx[u])
            for d in [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)]:
                v = (u[0] + d[0], u[1] + d[1])
                if v in idx and v not in seen:
                    seen.add(v)
                    st.append(v)
        groups.append(mem)
    groups.sort(key=len, reverse=True)
    clusters = []
    city_others = [r for r in others if inside(rings, r[0], r[1])]
    for m in groups[:8]:
        a = len(m) * step * step
        if a < 0.4:
            break
        cx = sum(c[0] for c in m) / len(m)
        cy = sum(c[1] for c in m) / len(m)
        towns = collections.Counter()
        for r in city_others:
            if any(math.hypot((r[0] - c[0]) * kx, (r[1] - c[1]) * ky) < step for c in m):
                t = town_of(r[2], r[3], city)
                if t:
                    towns[t] += 1
        if not towns:
            for r in sorted(city_others, key=lambda r: math.hypot((r[0] - cx) * kx, (r[1] - cy) * ky))[:5]:
                t = town_of(r[2], r[3], city)
                if t:
                    towns[t] += 1
        near2 = [r for r in svc if math.hypot((r[0] - cx) * kx, (r[1] - cy) * ky) < 2.0]
        inner = sum(1 for r in city_others if any(math.hypot((r[0] - c[0]) * kx, (r[1] - c[1]) * ky) < step for c in m))
        clusters.append(dict(a=a, x=cx, y=cy, towns=[t for t, _ in towns.most_common(3)], md=max(c[2] for c in m),
                             avgd=sum(c[2] for c in m) / len(m), comp=len(near2), compcap=sum(r[5] for r in near2), inner=inner))
    svg = map_svg(rings, bb, kx, ky, step, cells, [r for r in svc], clusters)
    return dict(n=n, area=n * step * step, far=len(far) / n * 100 if n else 0,
                live_far=len(live_far) / len(live) * 100 if live else 0, clusters=clusters, svg=svg,
                inside=sum(1 for r in svc if inside(rings, r[0], r[1])))


BANDS = [(0.5, "#cfe9d3", "0.5km以内"), (1.0, "#8fcf9c", "0.5〜1km"), (2.0, "#f6c36b", "1〜2km"), (99, "#d9792b", "2km より遠い")]


def map_svg(rings, bb, kx, ky, step, cells, svc, clusters):
    W = 640
    w_km, h_km = (bb[2] - bb[0]) * kx, (bb[3] - bb[1]) * ky
    sc = W / w_km
    H = h_km * sc
    if H > 760:
        sc = 760 / h_km
        H = 760
        W = w_km * sc

    def P(x, y):
        return (x - bb[0]) * kx * sc, (bb[3] - y) * ky * sc

    cs = step * sc + 0.35
    out = []
    for x, y, d, _ in cells:
        col = next(c for t, c, _ in BANDS if d <= t)
        px, py = P(x, y)
        out.append(f'<rect x="{px - cs / 2:.1f}" y="{py - cs / 2:.1f}" width="{cs:.1f}" height="{cs:.1f}" fill="{col}"/>')
    for r, _ in rings:
        pts = " ".join(f"{a:.1f},{b:.1f}" for a, b in (P(x, y) for x, y in r))
        out.append(f'<polygon points="{pts}" fill="none" stroke="#1f4d3a" stroke-width="1.2"/>')
    for r in svc:
        px, py = P(r[0], r[1])
        if -4 < px < W + 4 and -4 < py < H + 4:
            out.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="2.6" fill="#1f4d3a" stroke="#fff" stroke-width=".8"/>')
    for i, c in enumerate(clusters, 1):
        px, py = P(c["x"], c["y"])
        out.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="10" fill="#fff" stroke="#b3392c" stroke-width="2"/>'
                   f'<text x="{px:.1f}" y="{py + 4.5:.1f}" text-anchor="middle" font-size="13" font-weight="700" fill="#b3392c">{i}</text>')
    return f'<svg viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" height="{H:.0f}" class="map">{"".join(out)}</svg>'


# ---------------------------------------------------------------- グラフ

def bars_h(items, unit="", max_v=None, fmt="{:,.1f}"):
    """横棒。items = [(ラベル, 値, 強調するか)]"""
    W, rh, lw = 520, 22, 150
    mx = max_v or max([v for _, v, _ in items] + [0.0001])
    out = []
    for i, (lab, v, hi) in enumerate(items):
        y = i * rh
        bw = (W - lw - 70) * (v / mx)
        out.append(f'<text x="{lw - 8}" y="{y + 15}" text-anchor="end" class="lab">{E(lab)}</text>'
                   f'<rect x="{lw}" y="{y + 4}" width="{bw:.1f}" height="{rh - 8}" rx="2" fill="{"#2d7a45" if hi else "#b2dbb9"}"/>'
                   f'<text x="{lw + bw + 6:.1f}" y="{y + 15}" class="val">{fmt.format(v)}{unit}</text>')
    return f'<svg viewBox="0 0 {W} {len(items) * rh}" class="chart">{"".join(out)}</svg>'


def line_chart(periods, series, W=250, H=120):
    """小さな折れ線。series = [値, ...]（None は線を切る）"""
    v = [x for x in series if x is not None]
    if not v:
        return ""
    lo, hi = 0, max(v) * 1.15 or 1
    pl, pr, pt, pb = 6, 34, 8, 20
    n = len(periods)

    def P(i, x):
        return pl + i / (n - 1) * (W - pl - pr), H - pb - (x - lo) / (hi - lo) * (H - pt - pb)

    pts = [P(i, x) for i, x in enumerate(series) if x is not None]
    line = " ".join(f"{a:.1f},{b:.1f}" for a, b in pts)
    lx, ly = pts[-1]
    fx, fy = pts[0]
    return (f'<svg viewBox="0 0 {W} {H}" class="chart">'
            f'<line x1="{pl}" x2="{W - pr}" y1="{H - pb}" y2="{H - pb}" stroke="#dfe8db"/>'
            f'<polyline points="{line}" fill="none" stroke="#2d7a45" stroke-width="2"/>'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3" fill="#2d7a45"/>'
            f'<text x="{lx + 5:.1f}" y="{ly + 4:.1f}" class="val">{v[-1]:,}</text>'
            f'<text x="{fx:.1f}" y="{fy - 6:.1f}" class="ax">{v[0]:,}</text>'
            f'<text x="{pl}" y="{H - 5}" class="ax">{B.label(periods[0], True)}</text>'
            f'<text x="{W - pr}" y="{H - 5}" class="ax" text-anchor="end">{B.label(periods[-1], True)}</text></svg>')


def stack_bar(parts):
    """100%の横帯。parts = [(ラベル, 数)]"""
    tot = sum(v for _, v in parts) or 1
    cols = ["#1f4d3a", "#2d7a45", "#3d9a58", "#8fcf9c", "#cfe9d3", "#f6c36b", "#d9792b", "#c9d3cc"]
    x, out, leg = 0.0, [], []
    for (lab, v), col in zip(parts, cols):
        w = v / tot * 600
        out.append(f'<rect x="{x:.1f}" y="0" width="{w:.1f}" height="28" fill="{col}"/>')
        x += w
        leg.append(f'<span class="lg"><i style="background:{col}"></i>{E(lab)} {v / tot * 100:.0f}%</span>')
    return f'<svg viewBox="0 0 600 28" class="chart">{"".join(out)}</svg><p class="legend">{"".join(leg)}</p>'


# ---------------------------------------------------------------- 本体

def nice_ticks(lo, hi, n=5):
    """目盛りの値（きりのよい数）"""
    span = (hi - lo) or 1
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    a = math.floor(lo / step) * step
    out = []
    while a <= hi + step * 0.01:
        out.append(round(a, 6))
        a += step
    return out


def xy_chart(series, xs, xlab, ylab, W=560, H=200, marks=(), fmt="{:,.0f}", every=None):
    """折れ線のグラフ。series = [(名前, 色, [値])]。xs は横の目盛りの値。marks = [(x, 文字)] は縦の点線。"""
    vals = [v for _, _, vs in series for v in vs if v is not None]
    lo, hi = min(vals + [0]), max(vals + [0])
    ticks = nice_ticks(lo, hi)
    lo, hi = ticks[0], ticks[-1]
    pl, pr, pt, pb = 58, 14, 12, 36
    x0, x1 = xs[0], xs[-1]

    def P(x, y):
        return pl + (x - x0) / ((x1 - x0) or 1) * (W - pl - pr), pt + (hi - y) / ((hi - lo) or 1) * (H - pt - pb)
    out = []
    for t in ticks:
        _, y = P(x0, t)
        out.append(f'<line x1="{pl}" x2="{W - pr}" y1="{y:.1f}" y2="{y:.1f}" stroke="{"#7d8f82" if t == 0 else "#e3ebe0"}"/>'
                   f'<text x="{pl - 6}" y="{y + 4:.1f}" text-anchor="end" class="ax">{fmt.format(t)}</text>')
    step = every or max(1, len(xs) // 8)
    for x in xs[::step]:
        px, _ = P(x, lo)
        out.append(f'<text x="{px:.1f}" y="{H - pb + 15}" text-anchor="middle" class="ax">{fmt.format(x) if not isinstance(x, str) else x}</text>')
    for x, lab in marks:
        px, _ = P(x, lo)
        out.append(f'<line x1="{px:.1f}" x2="{px:.1f}" y1="{pt}" y2="{H - pb}" stroke="#b3392c" stroke-dasharray="4 3"/>'
                   f'<text x="{px + 4:.1f}" y="{pt + 10}" class="ax" fill="#b3392c">{E(lab)}</text>')
    leg = []
    for name, col, vs in series:
        pts = " ".join(f"{a:.1f},{b:.1f}" for a, b in (P(x, v) for x, v in zip(xs, vs) if v is not None))
        out.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2.2"/>')
        leg.append(f'<span class="lg"><i style="background:{col}"></i>{E(name)}</span>')
    out.append(f'<text x="{(pl + W - pr) / 2:.0f}" y="{H - 4}" text-anchor="middle" class="ax">{E(xlab)}</text>'
               f'<text x="12" y="{pt + (H - pt - pb) / 2:.0f}" class="ax" transform="rotate(-90 12 {pt + (H - pt - pb) / 2:.0f})" text-anchor="middle">{E(ylab)}</text>')
    return f'<svg viewBox="0 0 {W} {H}" class="chart xy">{"".join(out)}</svg><p class="legend">{"".join(leg)}</p>'


def tornado(items, unit="万円"):
    """感応度のグラフ。items = [(項目, 下げたときの変化, 上げたときの変化, 下げ幅の文字, 上げ幅の文字)]"""
    W, rh, lw = 600, 30, 175
    mx = max([abs(v) for it in items for v in it[1:3]] + [1])
    mid = lw + (W - lw) / 2
    half = (W - lw) / 2 - 95
    out = [f'<line x1="{mid:.1f}" x2="{mid:.1f}" y1="0" y2="{len(items) * rh}" stroke="#7d8f82"/>']
    for i, (lab, a, b, la, lb) in enumerate(items):
        y = i * rh
        out.append(f'<text x="{lw - 8}" y="{y + 19}" text-anchor="end" class="lab">{E(lab)}</text>')
        for v, t in ((a, la), (b, lb)):
            w = abs(v) / mx * half
            x = mid if v >= 0 else mid - w
            col = "#2d7a45" if v >= 0 else "#d9792b"
            tx = mid + w + 4 if v >= 0 else mid - w - 4
            out.append(f'<rect x="{x:.1f}" y="{y + 6}" width="{w:.1f}" height="{rh - 12}" fill="{col}" rx="2"/>'
                       f'<text x="{tx:.1f}" y="{y + 19}" text-anchor="{"start" if v >= 0 else "end"}" class="val">{E(t)} {v:+,.0f}{unit}</text>')
    return f'<svg viewBox="0 0 {W} {len(items) * rh}" class="chart">{"".join(out)}</svg>'


# ---------------------------------------------------------------- 投資の判断

def month_flows(cap, days, util, perday, cost, open_, months=120):
    """月ごとの手元資金の出入り（万円）。0か月目に開業費用、報酬は2か月おくれて入る。"""
    rev = []
    out = [-open_]
    for m in range(months):
        u = min(RAMP[m], util) if m < len(RAMP) else util
        rev.append(cap * days / 12 * u * perday / 1e4)
        out.append((rev[m - 2] if m >= 2 else 0) - cost / 12)
    return out


def irr(fl, lo=-0.99, hi=1.0):
    """月ごとの出入りから、年あたりの内部収益率。10年で元が取れないときは None。"""
    def npv(r):
        return sum(v / (1 + r) ** i for i, v in enumerate(fl))
    if npv(0) <= 0:
        return None
    lo = 0.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if npv(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (1 + lo) ** 12 - 1


def invest(cap, days, util, perday, cost, open_):
    fl = month_flows(cap, days, util, perday, cost, open_)
    cum, c = [], 0.0
    for v in fl:
        c += v
        cum.append(c)
    need = -min(cum)
    profit = cap * days * util * perday / 1e4 - cost
    back = next((i for i, v in enumerate(cum) if i > 12 and v >= 0), None)
    r = 0.05
    npv = sum(v / (1 + r) ** (i / 12) for i, v in enumerate(fl))
    return dict(fl=fl, cum=cum, need=need, profit=profit, roi=profit / need if need else None, back=back,
                c5=cum[60], c10=cum[120], irr=irr(fl), npv=npv)


# ---------------------------------------------------------------- 立地の点数（需要と受け皿）

ADULT = ["20～24歳", "25～29歳", "30～34歳", "35～39歳", "40～44歳", "45～49歳", "50～54歳", "55～59歳", "60～64歳"]
DEMAND = {"65": {"5～9歳": 0.8, "10～14歳": 1, "15～19歳": 0.6}, "63": {"0～4歳": 1, "5～9歳": 0.2},
          "46": dict({"15～19歳": 0.4}, **{a: 1 for a in ADULT}), "33": dict({"15～19歳": 0.4}, **{a: 1 for a in ADULT})}
DEM_WHO = {"65": "6〜17歳の子ども", "63": "0〜5歳の子ども", "46": "18〜64歳の人", "33": "18〜64歳の人"}
RADIUS = {"65": 2.0, "63": 2.0, "46": 3.0, "33": 3.0}  # 通ってくる範囲（km）。放デイ・児発は送迎、B型・GHは電車・バスも使う
NEWCAP = {"65": 10, "63": 10, "46": 20, "33": 1}  # 新しく開く受け皿（グループホームは定員のデータがないので1か所＝1）
SMALL = []


def load_small():
    p = B.ROOT / "data" / "report" / "smallarea.csv"
    if SMALL or not p.exists():
        return
    with p.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            SMALL.append(dict(muni=r["muni"], town=r["town"], x=float(r["lng"]), y=float(r["lat"]),
                              v={k: int(r[k]) for k in r if k.endswith("歳") or k.endswith("以上")}))


def area_name(muni, town, city):
    ward = muni[len(city):] if muni.startswith(city) else ""
    t = re.sub(r"（.*?）$", "", town)
    t = re.sub(r"[0-9０-９一二三四五六七八九十]+(丁目|丁|番町|条.*|線.*)$", "", t)
    return ward + t


def site_model(c, city, rings, rows, periods):
    """町ごとに、半径 R km の需要（サービスを使う年れいの人口）と受け皿（同じサービスの定員）をくらべる。
    点数＝(需要 ÷ (今の受け皿＋新しい受け皿)) ÷ (市全体の 需要 ÷ 受け皿)。1.0 が市の平均。"""
    towns = [t for t in SMALL if t["muni"].startswith(city)]
    if not towns:
        return None
    R, new = RADIUS[c], NEWCAP[c]
    w = DEMAND[c]
    for t in towns:
        t["d"] = sum(t["v"].get(a, 0) * k for a, k in w.items())
    xs, ys = [t["x"] for t in towns], [t["y"] for t in towns]
    pad = 0.06
    svc = [r for r in rows[c] if min(xs) - pad < r[0] < max(xs) + pad and min(ys) - pad < r[1] < max(ys) + pad]
    caps_ = sorted(r[5] for r in svc if r[5] > 0)
    med = caps_[len(caps_) // 2] if caps_ else 10

    def seat(r):
        return 1 if c == "33" else (r[5] if r[5] > 0 else med)
    d_city = sum(t["d"] for t in towns)
    s_city = sum(seat(r) for r in svc if inside(rings, r[0], r[1]))
    if not d_city or not s_city:
        return None
    base = d_city / s_city
    # 最近開いた事業所と、それが続いているか（最初の時点より後に載り、最後の1年より前に載ったもの）
    H = HIST.get(c, {})
    born = []
    if H:
        seen = set(H[periods[0]])
        for p in periods[1:-2]:
            for no in H[p]:
                if no not in seen and no in POS[c]:
                    born.append((POS[c][no], no not in H[periods[-1]]))
            seen |= set(H[p])
    born_city = [g for (x, y), g in born if inside(rings, x, y)]
    g_city = sum(born_city) / len(born_city) if born_city else 0
    areas = collections.defaultdict(list)
    for t in towns:
        areas[area_name(t["muni"], t["town"], city)].append(t)
    out = []
    for name, ts in areas.items():
        dd = sum(t["d"] for t in ts)
        if dd <= 0 or not name:
            continue
        cx = sum(t["x"] * t["d"] for t in ts) / dd
        cy = sum(t["y"] * t["d"] for t in ts) / dd
        dem_r = sum(t["d"] for t in towns if abs(t["y"] - cy) < R / 110 and abs(t["x"] - cx) < R / 80 and km(cx, cy, t["x"], t["y"]) <= R)
        near_s = [r for r in svc if abs(r[1] - cy) < R / 110 and abs(r[0] - cx) < R / 80 and km(cx, cy, r[0], r[1]) <= R]
        s_r = sum(seat(r) for r in near_s)
        idx = dem_r / (s_r + new) / base
        nb = [g for (x, y), g in born if abs(y - cy) < R / 110 and abs(x - cx) < R / 80 and km(cx, cy, x, y) <= R]
        g_r = sum(nb) / len(nb) if nb else None
        sc = idx * (1 - 0.5 * max(0.0, (g_r or 0) - g_city)) if len(nb) >= 3 else idx
        nd = min((km(cx, cy, r[0], r[1]) for r in svc), default=9.9)
        out.append(dict(towns=[name], x=cx, y=cy, dem=dem_r, seats=s_r, comp=len(near_s), compcap=s_r if c != "33" else 0,
                        idx=idx, sc=sc, born=len(nb), gone=sum(nb), g=g_r, avgd=nd, md=nd, a=0, inner=1, own=dd))
    out.sort(key=lambda a: -a["sc"])
    return dict(areas=out, base=base, d_city=d_city, s_city=s_city, g_city=g_city, n_born=len(born_city), R=R, new=new, med=med)


def collect():
    periods, latest, prev, _, S = B.load()
    B.load_pop()
    codes = [c for c in SERVICES if c in S]
    # 地図用：最新時点の位置（経度, 緯度, 住所の市区町村, 住所の続き, 定員）
    rows = {}
    for c in codes:
        seen, rs = set(), []
        for r in B.read_slim(B.SLIM / c / f"{latest}.csv.gz"):
            if r["lat"] and r["lng"] and (r["no"], r["addr"]) not in seen:
                seen.add((r["no"], r["addr"]))
                rs.append((float(r["lng"]), float(r["lat"]), r["muni"], r["addr"], r["no"],
                           int(r["cap"]) if (r.get("cap") or "").isdigit() else 0))
        rows[c] = rs
    # 続いているか：時点ごとの事業所番号と市区町村（主なサービスだけ）
    for c in SURV:
        for p in periods:
            HIST[c][p] = {}
            for r in B.read_slim(B.SLIM / c / f"{p}.csv.gz"):
                HIST[c][p][r["no"]] = (int(r["pref"]), r["muni"])
                if r["lat"] and r["lng"]:
                    POS[c][r["no"]] = (float(r["lng"]), float(r["lat"]))
    return periods, latest, prev, S, codes, rows


FACTS = json.loads((B.ROOT / "data" / "report" / "city_facts.json").read_text(encoding="utf-8"))
DAYS = 280  # 放課後等デイの1年の営業日（福祉医療機構の経営調査の平均 280.4日）
CAP10 = 10  # 試算の定員
BASE_PRICE = 10.72  # 船橋市（4級地）の単価。1人1日の報酬の目安はこの単価のときの値
STAFF = 2000  # 職員6人の人件費（万円、千葉県の保育士の給料のとき）
OTHER = 680  # 家賃・送迎車・光熱費など＋開業費用の償却（万円、船橋市のとき）
RENT = 240  # そのうち家賃（月20万円、船橋市のとき）
GH_REV = 233  # グループホームの定員1人あたりの1年の収益（万円、全国平均。経営実態調査 2022年度決算 3,496万円÷定員15）
GH_BASE = 10.5  # 全国平均の1単位の単価のおおよそ
GH_STAFF = 0.617  # 収益に対する給与の割合（令和7年 経営概況調査）
GH_COST = 0.909  # 収益に対する費用の割合（収支差率9.1%）
GH_RENT = 360  # そのうち建物の家賃（月30万円、全国平均の家賃のとき）
GH_UTIL = 0.9  # 入居率
GH_OPEN = 1200  # 開業費用（改修・消防設備・敷金・開業前の人件費）
OPEN = 900  # 開業費用（万円）
RAMP = [.3] * 3 + [.5] * 3 + [.7] * 3 + [.8] * 3 + [.85] * 6  # 開業からの利用率の伸び（その後は標準の利用率）


def fy_label(fy):
    return f"{fy}年度"


def sim_year(util, perday, cost):
    """1年の収支（万円）"""
    return CAP10 * DAYS * util * perday / 1e4 - cost


def cash_path(util, perday, cost, months=120, cap=CAP10, days=DAYS, open_=OPEN):
    """手元資金の動き。報酬は2か月おくれて入る。いちばん減ったところと、元が取れる月。"""
    cash, low, back = -open_, -open_, None
    rev = []
    for m in range(months):
        u = min(RAMP[m], util) if m < len(RAMP) else util
        rev.append(cap * days / 12 * u * perday / 1e4)
        cash += (rev[m - 2] if m >= 2 else 0) - cost / 12
        low = min(low, cash)
        if back is None and m > 12 and cash >= 0:
            back = m + 1
    return low, back


SURV = ["65", "63", "46", "33", "22"]  # 続いているかを見るサービス
HIST = collections.defaultdict(dict)  # HIST[サービス][時点] = {事業所番号: (県, 市区町村)}
POS = collections.defaultdict(dict)  # POS[サービス][事業所番号] = (経度, 緯度)


def survival(periods, c, keep):
    """keep(県, 市区町村) に入る事業所について、
    (最初の時点にあった数, そのうち今もある割合, 途中で新しく載った数, そのうち今は見えない割合)"""
    first, last = periods[0], periods[-1]
    H = HIST[c]
    a = [no for no, v in H[first].items() if keep(*v)]
    a_keep = sum(1 for no in a if no in H[last]) / len(a) if a else None
    seen = set(H[first])
    born = []
    for p in periods[1:-2]:  # 最後の1年に載った分は、まだ早いので入れない
        for no, v in H[p].items():
            if no not in seen and keep(*v):
                born.append(no)
        seen |= set(H[p])
    b_gone = sum(1 for no in born if no not in H[last]) / len(born) if born else None
    return len(a), a_keep, len(born), b_gone


NAT_SURV = {}  # 全国の続いている割合（サービスごとに1回だけ数える）
NAT_OPEN = {}  # 全国の曜日・時間の割合（サービスごとに1回だけ数える）
CHAIN_MIN = 50  # 全国で50か所以上の事業所を運営する法人を「大手」とする
CORP_N = collections.Counter()  # 法人ごとの全国の事業所の数（main で数える）

# ほかのサービスの試算（福祉医療機構の経営調査の全国平均。単価は4級地のときの値）
OTHERS = {
    "63": dict(cap=10, util=0.827, perday=15786, base=10.72, days=266, staff=2400, other=780, rent=240, open=900, price="unit_price",
               src="2024年度 児童発達支援の経営状況（利用率82.7%、1人1日15,786円、営業日266日、利益率8.5%、赤字の事業所28.4%）"),
    "46": dict(cap=20, util=0.817, perday=9126, base=10.68, days=260, staff=2600, other=930, rent=300, open=1100, price="unit_price_b",
               src="2024年度 就労継続支援B型の経営状況（利用率81.7%、1人1日9,126円、利益率9.1%、赤字の事業所30.4%、平均工賃月23,583円）"),
}


def load_child():
    """社人研の将来推計人口（子ども）。{市区: {年: (0-4, 5-9, 10-14, 15-19)}}"""
    out = collections.defaultdict(dict)
    f = B.ROOT / "data" / "report" / "child_proj.csv"
    for line in f.read_text(encoding="utf-8").splitlines()[2:]:
        v = line.split(",")
        out[v[0]][int(v[1])] = tuple(int(x) for x in v[2:6])
    return out


CHILD = load_child()


def load_places():
    """国土数値情報の学校（小学校・特別支援学校）と駅。"""
    d = B.ROOT / "data" / "report"
    sch = collections.defaultdict(list)
    with open(d / "schools.csv", encoding="utf-8") as f:
        seen = set()
        for r in csv.DictReader(f):
            k = (r["name"], round(float(r["lng"]), 2), round(float(r["lat"]), 2))  # 小学部・中学部などで同じ学校が何行もある
            if k not in seen:
                seen.add(k)
                sch[r["kind"]].append((float(r["lng"]), float(r["lat"]), r["name"], r["pref"]))
    with open(d / "stations.csv", encoding="utf-8") as f:
        sta = [(float(r["lng"]), float(r["lat"]), r["name"], r["line"]) for r in csv.DictReader(f)]
    return sch, sta


SCHOOLS, STATIONS = load_places()


def km(x1, y1, x2, y2):
    return math.hypot((x1 - x2) * 111.32 * math.cos(math.radians(y1)), (y1 - y2) * 110.95)


def nearby(pts, x, y, r):
    """(距離, 点) を近い順に。r km 以内だけ。"""
    out = []
    for p in pts:
        if abs(p[1] - y) < r / 110 and abs(p[0] - x) < r / 80:
            d = km(x, y, p[0], p[1])
            if d <= r:
                out.append((d, p))
    return sorted(out)


def kids(t, c):
    """5歳きざみの人数から、サービスを使う年れいの子どもの数のおおよそを出す。"""
    a0, a5, a10, a15 = t
    return a5 * 0.8 + a10 + a15 * 0.6 if c == "65" else a0 + a5 * 0.2  # 放デイ6〜17歳、児発0〜5歳


def kids_at(city, c, y):
    t = CHILD.get(city)
    if not t:
        return None
    y0 = max(k for k in t if k <= y)
    y1 = min((k for k in t if k >= y), default=y0)
    v0, v1 = kids(t[y0], c), kids(t[y1], c)
    return v0 if y1 == y0 else v0 + (v1 - v0) * (y - y0) / (y1 - y0)


def ward_of(cl):
    m = re.match(r"(.+?区)", cl["towns"][0]) if cl["towns"] else None
    return m.group(1) if m else ""


def cap_rule(caps, c, cl=None):
    """総量規制などで、その場所に開けるか。(倍率, 注意の文) 倍率0は開けない。"""
    r = caps.get(c)
    if not r:
        return 1, ""
    if r["level"] == "stop":
        return 0, "市が新しい指定を止めています"
    if r["level"] == "watch":
        return 0.85, "市の計画の量に届くと断られることがあります"
    if "wards" in r and cl is not None:
        w = ward_of(cl)
        n = r["wards"].get(w, 0)
        if not n:
            return 0, f"{w or 'この区'}は募集なし（総量規制）"
        return 0.7, f"{w}の募集枠は{n}か所（総量規制、選ばれる必要あり）"
    return 0.6, "市の募集で選ばれる必要があります（総量規制）"


def city_counts(S, c, p, pi, city):
    if p not in S[c]:
        return None
    return sum(v for mk, v in S[c][p]["muni"].items() if in_city(mk, pi, city))


def build_one(ctx, pi, city, geo_dir, ranks, ed="child"):
    periods, latest, prev, S, codes, rows = ctx
    ED = EDITIONS[ed]
    SV = ED["svcs"]
    child = ed == "child"
    pref = PREFS[pi]
    pop = B.POP_MUNI[pi].get(city)
    ppop = B.POP_PREF[pi]
    npop = sum(B.POP_PREF.values())
    lp, lq, l0 = B.label(latest), B.label(prev), B.label(periods[0])

    ser = {c: [city_counts(S, c, p, pi, city) for p in periods] for c in codes}
    now = {c: ser[c][-1] for c in codes}
    total = [sum((ser[c][i] or 0) for c in codes) for i in range(len(periods))]

    def rate(n, base):
        return n / base * 1e4 if base else 0

    comp = {}
    for c in codes:
        comp[c] = (rate(now[c], pop), rate(S[c][latest]["pref"].get(pi, 0), ppop), rate(S[c][latest]["n"], npop))

    # 新しく開いた・なくなった（前の時点とくらべて）
    newl, gone = collections.defaultdict(list), collections.defaultdict(list)
    for c in codes:
        L, Pv = S[c][latest]["offices"], S[c][prev]["offices"]
        for no in set(L) - set(Pv):
            if any(in_city(mk, pi, city) for mk in L[no]["munis"]):
                newl[c].append(L[no])
        for no in set(Pv) - set(L):
            if any(in_city(mk, pi, city) for mk in Pv[no]["munis"]):
                gone[c].append(Pv[no])
    n_new, n_gone = sum(map(len, newl.values())), sum(map(len, gone.values()))

    # 運営する法人
    offs = [o for c in codes for o in S[c][latest]["offices"].values() if any(in_city(mk, pi, city) for mk in o["munis"])]
    ctype = collections.Counter(B.corp_form(o["corp"]) for o in offs)
    corps = collections.Counter(o["corp_key"] or o["corp"] for o in offs)
    corp_name = {o["corp_key"] or o["corp"]: o["corp"] for o in offs}
    corp_svc = collections.defaultdict(set)
    for c in codes:
        for o in S[c][latest]["offices"].values():
            if any(in_city(mk, pi, city) for mk in o["munis"]):
                corp_svc[o["corp_key"] or o["corp"]].add(short(c))
    n_corp = len(corps)

    # 定員
    caps = {}
    for c in CAP:
        v = [int(o["cap"]) for o in S[c][latest]["offices"].values()
             if any(in_city(mk, pi, city) for mk in o["munis"]) and str(o["cap"]).isdigit() and int(o["cap"]) > 0]
        if now[c]:
            caps[c] = (now[c], len(v), sum(v), statistics.median(v) if v else 0)

    # 区ごと（政令市）／近くの市区町村
    wards = sorted({mk[1] for c in codes for mk in S[c][latest]["muni"] if in_city(mk, pi, city) and mk[1] != city})
    cent = collections.defaultdict(list)
    for c in codes:
        for o in S[c][latest]["offices"].values():
            for (_, _, _, lat, lng) in o["houses"][:1]:
                if lat and lng:
                    for mk in o["munis"]:
                        cent[unit_of(mk)].append((float(lat), float(lng)))
    C = {k: (sum(a for a, _ in v) / len(v), sum(b for _, b in v) / len(v)) for k, v in cent.items()}
    me = C.get((pi, city))
    near = []
    if me:
        near = [k for _, k in sorted(
            (math.hypot(C[k][0] - me[0], (C[k][1] - me[1]) * math.cos(math.radians(me[0]))), k)
            for k in C if k != (pi, city) and k[0] == pi)[:6]]

    def unit_count(c, k):
        return sum(v for mk, v in S[c][latest]["muni"].items() if unit_of(mk) == k)

    # 地図
    rings = load_rings(geo_dir, pi, city)
    maps = {}
    for c in MAPS:
        if (pi, city, c) not in MAPCACHE:
            MAPCACHE[(pi, city, c)] = gap_map(rows, rings, c, city)
        maps[c] = MAPCACHE[(pi, city, c)]


    # 利用率・試算・人の集めやすさ
    cf = FACTS["city"].get(city, {})
    pf = FACTS["pref"].get(pref, {})
    nf = FACTS["national"]
    price = FACTS["unit_price"][cf.get("grade", "0")]
    grade = cf.get("grade", "0")
    grade_txt = f"{grade}級地" if grade != "0" else "その他（上乗せなし）"
    r = price / BASE_PRICE
    chiba = FACTS["pref"]["千葉県"]
    pay_r = pf["pay"] / chiba["pay"]  # 保育士の年収の、千葉県とのくらべ
    rent_r = cf["rent"] / FACTS["city"]["船橋市"]["rent"]  # 家賃の、船橋市とのくらべ
    rent = round(RENT * rent_r)
    staff = round(STAFF * pay_r)
    other = OTHER - RENT + rent
    cost = staff + other
    per = {"慎重": (0.80, 11000 * r), "標準": (0.89, 12500 * r), "好調": (0.93, 13300 * r)}
    hd = cf.get("houday")
    n65, nc65, sm65, _ = caps.get("65", (now["65"], 0, 0, 0))
    avg_cap = sm65 / nc65 if nc65 else 10
    util = cap_then = None
    if hd:
        ps = [p for p in (f"{hd['fy']}09", f"{hd['fy'] + 1}03") if p in periods]
        n_then = sum(ser["65"][periods.index(p)] for p in ps) / len(ps) if ps else now["65"]
        cap_then = n_then * avg_cap
        util = hd["nd"] / (cap_then * DAYS / 12)
    be = cost / (CAP10 * DAYS * per["標準"][1] / 1e4)
    low, back = cash_path(0.89, per["標準"][1], cost)


    # ねらい目の場所・サービス
    caps_open = cf.get("open", {}).get("caps", {})
    svc_view = {}
    for c in MAIN:
        a, b, _ = comp[c]
        ratio = a / b if b else 1
        g = (now[c] - ser[c][0]) / ser[c][0] if ser[c][0] else None
        if ratio < 0.85:
            verdict = "少ない（ねらい目）" if (g or 0) > 0.2 else "少ない"
        elif ratio > 1.2:
            verdict = "多い（競争がきびしい）"
        else:
            verdict = "ふつう"
        rule = caps_open.get(c)
        if rule:
            verdict = {"stop": "新しく開けない（市が止めている）", "limit": "市の募集で選ばれれば開ける（総量規制）",
                       "watch": "計画の量しだいで断られることがある"}[rule["level"]]
        svc_view[c] = (ratio, g, len(newl[c]) - len(gone[c]), verdict)
    cands, blocked = [], []
    load_small()
    SITE.clear()
    for c in SV:
        sm = site_model(c, city, rings, rows, periods)
        if sm:
            SITE[c] = sm
            picked = []
            for cl in sm["areas"]:
                if len(picked) >= 8:
                    break
                if any(km(cl["x"], cl["y"], q["x"], q["y"]) < sm["R"] * 0.75 for q in picked):
                    continue
                mul, note = cap_rule(caps_open, c, cl)
                if not mul:
                    blocked.append((c, cl, note))
                    continue
                picked.append(cl)
                cands.append((cl["sc"] * mul, c, cl, note))
            continue
        a, b, _ = comp[c]
        sf = max(0.7, min(1.5, b / a)) if a else 1.5
        if c == "65" and util:
            sf *= max(0.8, min(1.2, util / 0.89))
        for cl in maps[c]["clusters"]:
            if cl["inner"] == 0 or not cl["towns"]:
                continue
            mul, note = cap_rule(caps_open, c, cl)
            if not mul:
                blocked.append((c, cl, note))
                continue
            sc = math.sqrt(cl["inner"]) * min(cl["avgd"], 3) / math.sqrt(1 + cl["comp"]) * sf * mul
            cands.append((sc, c, cl, note))
    cands.sort(key=lambda x: -x[0])
    seen_t, merged = {}, []
    for sc, c, cl, note in cands:  # 同じ場所が複数のサービスで出たら1行にまとめる
        k = cl["towns"][0]
        if k in seen_t:
            seen_t[k][3].append(c)
            continue
        row = [sc, c, cl, [c], note]
        seen_t[k] = row
        merged.append(row)
    cands = [tuple(r) for r in merged[:5]]
    top_sc = cands[0][0] if cands else 1

    # ほかとの違い（曜日・時間・定員）
    def open_stats(offs_):
        sat = sun = late = n_c = n_h = 0
        for o in offs_:
            cl_ = o["closed"].strip()
            if cl_:
                n_c += 1
                sat += "土" not in cl_
                sun += not re.search(r"日曜|土日|(^|[、・,，\s/])日($|[、・,，\s/祝])", cl_)
            m = re.search(r"-(\d{1,2}):(\d{2})$", o["hours"].strip())
            if m:
                n_h += 1
                late += int(m.group(1)) * 60 + int(m.group(2)) >= 18 * 60 + 30
        return (sat / n_c if n_c else None, sun / n_c if n_c else None, late / n_h if n_h else None, n_c, n_h)
    diff = {}
    for c in ["65", "63", "46"]:
        mine = [o for o in S[c][latest]["offices"].values() if any(in_city(mk, pi, city) for mk in o["munis"])]
        allo = list(S[c][latest]["offices"].values())
        capv = [int(o["cap"]) for o in mine if str(o["cap"]).isdigit() and int(o["cap"]) > 0]
        diff[c] = (open_stats(mine), NAT_OPEN.setdefault(c, open_stats(allo)), capv)

    # ほかのサービスの試算
    oth = {}
    for c, m in OTHERS.items():
        pr = FACTS[m["price"]][grade]
        pd = m["perday"] * pr / m["base"]
        st = round(m["staff"] * pay_r)
        ct = st + m["other"] - m["rent"] + round(m["rent"] * rent_r)
        rv = m["cap"] * m["days"] * m["util"] * pd / 1e4
        oth[c] = dict(m, pr=pr, pd=pd, st=st, ct=ct, rv=rv, bal=rv - ct, be=ct / (m["cap"] * m["days"] * pd / 1e4))
    gh_pr = FACTS["unit_price_gh"][grade]
    # グループホーム（介護サービス包括型）：厚生労働省の経営実態調査（2022年度決算）の全国平均から
    gh_rv = GH_REV * CAP10 * gh_pr / GH_BASE
    gh_st = round(GH_REV * CAP10 * GH_STAFF * pf["pay"] / nf["pay"])
    gh_rent = round(GH_RENT * cf["rent"] / nf["rent"])
    gh_ct = gh_st + round(GH_REV * CAP10 * (GH_COST - GH_STAFF)) - GH_RENT + gh_rent
    gh = dict(cap=CAP10, util=GH_UTIL, pd=gh_rv * 1e4 / (CAP10 * 365 * GH_UTIL), rv=gh_rv, st=gh_st, ct=gh_ct, rent=gh_rent,
              bal=gh_rv - gh_ct, be=gh_ct / (gh_rv / GH_UTIL), days=365, open=GH_OPEN, pr=gh_pr)
    oth["46"]["open"] = OTHERS["46"]["open"]
    # この版の中心の試算
    if ed == "child":
        est = dict(c="65", cap=CAP10, bal=sim_year(*per["標準"], cost), be=be, low=low, back=back)
    else:
        m_ = gh if ed == "gh" else oth["46"]
        lw, bk = cash_path(m_["util"], m_["pd"], m_["ct"], cap=m_["cap"], days=m_["days"], open_=m_["open"])
        est = dict(c=ED["main"], cap=m_["cap"], bal=m_["bal"], be=m_["be"], low=lw, back=bk)

    # 10年後の需要（子どもの数の推計）
    dem = {}
    if city in CHILD:
        y_off = len(periods) / 2 - 0.5  # データの期間（年）
        for c in ("65", "63"):
            k25 = kids_at(city, c, 2025)
            g_off = (now[c] / ser[c][0]) ** (1 / y_off) - 1 if ser[c][0] else 0
            g_kid = (kids_at(city, c, 2026) / kids_at(city, c, 2021)) ** (1 / 5) - 1
            share_g = max(0.0, min(0.08, (1 + g_off) / (1 + g_kid) - 1))  # 使う子の割合の伸び（年）
            avg = (caps.get(c, (0, 0, 0, 0))[2] / caps[c][1]) if c in caps and caps[c][1] else 10
            if c == "65" and util:
                base = hd["nd"] * kids_at(city, c, 2025) / kids_at(city, c, hd["fy"] + 0.5)  # 2025年の需要（人日/月）
            else:
                base = now[c] * avg * DAYS / 12 * 0.89
            one = avg * DAYS / 12 * 0.89  # 1事業所が受け止められる量（利用率89%）
            rows_d = []
            for y in (2025, 2030, 2035):
                kv = kids_at(city, c, y)
                lo = base * kv / k25
                hi = lo * (1 + share_g) ** min(y - 2025, 5)
                rows_d.append((y, kv, lo, hi, lo / one, hi / one))
            dem[c] = dict(rows=rows_d, g=share_g, now=now[c], one=one, k20=kids_at(city, c, 2020), k35=kids_at(city, c, 2035), k25=k25)

    # 法人の出入り・大手
    def corps_in(p):
        cs = collections.defaultdict(list)
        for c in codes:
            for o in S[c][p]["offices"].values():
                if any(in_city(mk, pi, city) for mk in o["munis"]):
                    cs[o["corp_key"] or o["corp"]].append((c, o))
        return cs
    cin_now, cin_prev = corps_in(latest), corps_in(prev)
    c_new = [k for k in cin_now if k not in cin_prev]
    c_gone = [k for k in cin_prev if k not in cin_now]
    chains = {k: v for k, v in cin_now.items() if CORP_N[k] >= CHAIN_MIN}
    chain_share = sum(len(v) for v in chains.values()) / max(sum(len(v) for v in cin_now.values()), 1)
    new_chain = sum(1 for c in codes for o in newl[c] if CORP_N[o["corp_key"] or o["corp"]] >= CHAIN_MIN)
    new_chain_share = new_chain / n_new if n_new else 0
    chain65 = [o for k, v in chains.items() for c, o in v if c == ED["main"]]
    share65 = len(chain65) / now[ED["main"]] if now[ED["main"]] else 0

    # 続いているか
    pn = PREFS[pi]

    def keep_city(pf, m):
        return pf == pi and m.startswith(pn) and in_city((pi, m[len(pn):]), pi, city)
    surv = {c: (survival(periods, c, keep_city), NAT_SURV.setdefault(c, survival(periods, c, lambda pf, m: True))) for c in SURV}

    # ---- 要点
    grow = sorted([c for c in MAIN if ser[c][0]], key=lambda c: -(now[c] - ser[c][0]))
    over = sorted([c for c in MAIN if comp[c][1]], key=lambda c: -(comp[c][0] / comp[c][1]))
    zero = [short(c) for c in codes if not now[c]]
    pts = [
        f"{lp}の時点で、{city}にある障害福祉の事業所はあわせて<b>{total[-1]:,}件</b>です。{l0}の{total[0]:,}件から"
        f"<b>{(total[-1] - total[0]) / total[0] * 100:+.0f}%</b>{'増えました' if total[-1] >= total[0] else 'へりました'}。",
        f"いちばん増えたのは<b>{short(grow[0])}</b>（{ser[grow[0]][0]:,}件→{now[grow[0]]:,}件）、"
        f"次が{short(grow[1])}（{ser[grow[1]][0]:,}件→{now[grow[1]]:,}件）です。",
        f"人口1万人あたりで{pref}の平均より多いのは<b>{short(over[0])}</b>（県の{comp[over[0]][0] / comp[over[0]][1]:.1f}倍）、"
        f"少ないのは<b>{short(over[-1])}</b>（県の{comp[over[-1]][0] / comp[over[-1]][1]:.1f}倍）です。",
        f"{short(ED['main'])}から1km以上はなれている場所は、市の面積の<b>約{maps[ED['main']]['far']:.0f}%</b>、"
        f"住宅地の目安とした場所にかぎると<b>約{maps[ED['main']]['live_far']:.0f}%</b>です。",
        f"{lq}から{lp}までの半年で、新しく<b>{n_new:,}件</b>が載り、<b>{n_gone:,}件</b>が見えなくなりました。",
    ]
    if util and child:
        pts.append(f"放課後等デイの利用率（定員がどれくらい埋まっているか）の目安は<b>約{min(util, 9.99) * 100:.0f}%</b>です"
                   f"（{fy_label(hd['fy'])}の市の数字から計算。全国の事業所の平均は89%）。")
    pts.append(f"定員{est['cap']}人の{lname(ED['main'])}を開くと、平均的な事業所並みに動かせた場合の収支は<b>年{est['bal']:+,.0f}万円</b>、"
               f"黒字になる{'入居率' if ed == 'gh' else '利用率'}は<b>約{est['be'] * 100:.0f}%</b>です（{grade_txt}で試算）。")
    if cands:
        sc, c, cl, cs, _ = cands[0]
        pts.insert(0, f"<b>結論：開設するなら、{E('・'.join(short(x) for x in cs))}を{E('・'.join(cl['towns'][:2]))}のあたりで</b>が第一候補です。"
                      + (f"通ってくる範囲の{DEM_WHO[c]}の数にくらべて{E(short(c))}の受け皿が少なく、新しく開いても{'1か所' if c == '33' else '1席'}あたりの需要が市の平均の約{cl['idx']:.1f}倍あります"
                         if "idx" in cl else f"近くに{E(short(c))}が少なく（いちばん近いところまで平均{cl['avgd']:.1f}km）、人の住む地域です")
                      + "（くわしくは「結論：開設するならどこが良いか」の章）。")
    for c_, r_ in caps_open.items():
        if c_ in SV or (child and c_ in ("65", "63")):
            pts.append(f"<b>注意（{E(short(c_))}）：</b>{E(r_['text'])}")
    if ed == "gh":
        pts.append("<b>注意：</b>グループホームは、2027年4月1日から、市の計画の量をこえると新しい指定をしないこと（総量規制）ができるようになります。")
    if "65" in dem and child:
        d_ = dem["65"]
        pts.append(f"放課後等デイを使う年れい（6〜17歳）の子どもは、2025年から2035年に<b>{(d_['k35'] / d_['k25'] - 1) * 100:+.0f}%</b>"
                   f"{'へる' if d_['k35'] < d_['k25'] else 'ふえる'}見こみです（社人研の推計）。")
    pts.append(f"{E(city)}の事業所のうち<b>約{chain_share * 100:.0f}%</b>は、全国で{CHAIN_MIN}か所以上を運営する大手の法人です"
               f"（半年で新しく載った事業所では約{new_chain_share * 100:.0f}%）。")
    if zero:
        pts.append("まだ1件もないサービス：" + "、".join(zero[:8]) + ("ほか" if len(zero) > 8 else "") + "。")

    # ---- HTML
    h = []
    h.append(f'''<section class="cover">
<p class="eyebrow">ジモふく 地域レポート</p>
<h1>{E(city)}の障害福祉<br>事業所レポート</h1>
<p class="edition">{E(ED["name"])}</p>
<p class="sub">{E(pref)}{E(city)}／データの時点：{lp}</p>
<!--TOC-->
<p class="by">ジモふく（https://jimofuku.jp/）</p>
</section>''')

    # 1枚でわかるまとめ
    def tile(label, val, sub=""):
        return f'<div class="tile"><p class="tl">{label}</p><p class="tv">{val}</p><p class="ts">{sub}</p></div>'
    mc = ED["main"]
    tiles = [
        tile("障害福祉の事業所", f"{total[-1]:,}件", f"{l0}から{(total[-1] - total[0]) / total[0] * 100:+.0f}%"),
        tile(f"{short(mc)}の事業所", f"{now[mc]:,}件", f"人口あたり県の{svc_view[mc][0]:.2f}倍・2021年から{svc_view[mc][1] * 100:+.0f}%" if svc_view[mc][1] is not None else ""),
        tile(f"{short(mc)}定員{est['cap']}人の収支", f"年{est['bal']:+,.0f}万円", f"黒字になる{'入居率' if ed == 'gh' else '利用率'} 約{est['be'] * 100:.0f}%"),
        tile("投下資本（要る手元資金）", f"約{-est['low']:,.0f}万円", f"ROI 約{est['bal'] / -est['low'] * 100:.0f}%・回収 約{est['back'] // 12}年{est['back'] % 12}か月" if est["back"] else "10年では回収できない"),
        tile("すべての仕事の求人倍率", f"{pf['all']:.2f}倍", f"全国{nf['all']:.2f}倍・{E(pref)}"),
        tile("借家の平均家賃", f"月{cf['rent'] / 1e4:.1f}万円", f"全国{nf['rent'] / 1e4:.1f}万円（住宅・土地統計）"),
    ] if not child else [
        tile("障害福祉の事業所", f"{total[-1]:,}件", f"{l0}から{(total[-1] - total[0]) / total[0] * 100:+.0f}%"),
        tile("放課後等デイの利用率", f"約{util * 100:.0f}%" if util else "—", ("定員をこえる計算（くわしくは利用率の章）" if util > 1.2 else "全国の平均89%") if util else "市の数字なし（全国89%で試算）"),
        tile("放デイ定員10人の収支", f"年{sim_year(*per['標準'], cost):+,.0f}万円", f"黒字になる利用率 約{be * 100:.0f}%"),
        tile("投下資本（要る手元資金）", f"約{-low:,.0f}万円", f"ROI 約{sim_year(*per['標準'], cost) / -low * 100:.0f}%・回収 約{back // 12}年{back % 12}か月" if back else "10年では回収できない"),
        tile("保育士の求人倍率", f"{pf['hoiku']:.2f}倍", f"全国{nf['hoiku']:.2f}倍・{E(pref)}"),
        tile("6〜17歳の子ども（2035年）", f"{(dem['65']['k35'] / dem['65']['k25'] - 1) * 100:+.0f}%" if "65" in dem else "—", "2025年とくらべて（社人研）"),
    ]
    sig = []
    for c in ["65", "63", "46", "33"]:
        ratio, g, half, verdict = svc_view[c]
        bal = sim_year(*per["標準"], cost) if c == "65" else gh["bal"] if c == "33" else oth[c]["bal"]
        rule = caps_open.get(c)
        col = "red" if (rule and rule["level"] == "stop") else "yellow" if rule or "きびしい" in verdict else "green" if "少ない" in verdict else "plain"
        sig.append(f'<tr><th><span class="dot {col}"></span>{E(short(c))}</th><td style="text-align:left">{E(verdict)}</td>'
                   f'<td>{ratio:.2f}倍</td><td>年{bal:+,.0f}万円</td></tr>')
    first = (f'{E("・".join(short(x) for x in cands[0][3]))}を<b>{E("・".join(cands[0][2]["towns"][:2]))}</b>のあたりで' if cands else "場所より中身で差をつける")
    h.append('<section class="pb"><h2>§1枚でわかるまとめ</h2>'
             f'<p class="lead">第一候補：{first}</p><div class="tiles">' + "".join(tiles) + '</div>'
             '<h3>主なサービスの信号</h3><table class="small sig"><tr><th>サービス</th><th>見立て</th><th>人口あたり（県とくらべて）</th><th>定員1か所の試算（標準）</th></tr>'
             + "".join(sig) + '</table><p class="note">緑＝少なくてねらい目、黄＝競争がきびしいか市の決まりに注意、赤＝市が新しい指定を止めている。'
             '試算は放デイ・児発・グループホームが定員10人、B型が定員20人の1年の収支（全国平均並みに動かせた場合）。</p>'
             + '</section>')

    h.append('<section class="pb"><h2>§要点</h2><ol class="points">' + "".join(f"<li>{x}</li>" for x in pts) + "</ol>")
    h.append(f'<h3>主なサービスの数</h3><table><tr><th>サービス</th><th>{E(l0)}</th><th>{E(lp)}</th><th>増えた数</th>'
             f'<th>人口1万人あたり</th><th>主要{len(TARGETS)}市区の中で</th></tr>')
    for c in MAIN:
        a, f = now[c], ser[c][0]
        h.append(f'<tr><th>{E(short(c))}</th><td>{f if f is not None else "—"}</td><td>{a:,}</td>'
                 f'<td>{(a - f) if f is not None else "—":+}</td><td>{comp[c][0]:.2f}</td><td>{ranks[c].get((pi, city), "—")}位</td></tr>'
                 if f is not None else
                 f'<tr><th>{E(short(c))}</th><td>—</td><td>{a:,}</td><td>—</td><td>{comp[c][0]:.2f}</td><td>{ranks[c].get((pi, city), "—")}位</td></tr>')
    h.append(f'</table><p class="note">「主要{len(TARGETS)}市区の中で」は、人口1万人あたりの数が多い順の順位です（1位がいちばん多い）。</p></section>')


    # 結論
    h.append('<section class="pb"><h2>§結論：開設するならどこが良いか</h2>'
             + ('<p>町ごとに、<b>通ってくる範囲に住んでいる、サービスを使う年れいの人の数（需要）</b>と、'
                '<b>その範囲にすでにある同じサービスの定員（受け皿）</b>をくらべました。新しく開いたとき、1つの席あたりの需要が市の平均より何倍多いかが「需給の倍率」です。'
                '1.0 が市の平均で、大きいほど、開いた事業所が埋まりやすいと考えられる場所です。</p>' if SITE else
                '<p>近くにない地域の地図（あとの章）、人口あたりの数、利用率をあわせて、新しく開くときの候補を点数にしました。'
                '点数が高いほど「近くに同じサービスが少なく、人が住んでいて、まわりの競争がゆるい」場所です。</p>'))
    if cands:
        h.append('<h3>ねらい目の場所トップ5</h3><table class="small rank"><tr><th>順位</th><th>サービス</th><th>場所</th><th>点数</th><th>理由</th></tr>')
        for j, (sc, c, cl, cs, note) in enumerate(cands, 1):
            if "idx" in cl:
                sm = SITE[c]
                unit = "か所" if c == "33" else "人分"
                why = (f"半径{sm['R']:.0f}kmに{DEM_WHO[c]}が約{cl['dem']:,.0f}人。いまある{E(short(c))}は{cl['comp']}か所"
                       + (f"（定員あわせて{cl['seats']:,.0f}人）" if c != "33" else "") + "。"
                       f"新しく{sm['new']}{unit}増やしても、{'1か所' if c == '33' else '1席'}あたりの需要は市の平均の<b>{cl['idx']:.2f}倍</b>。")
                if cl["born"] >= 3:
                    why += f"近くで2022年以降に開いた{cl['born']}か所のうち{cl['born'] - cl['gone']}か所が今も続いている。"
                elif cl["born"]:
                    why += f"近くで2022年以降に開いたのは{cl['born']}か所。"
                else:
                    why += "近くで最近開いた事業所はない（需要がためされていない）。"
            else:
                why = (f"いちばん近い{E(short(c))}まで平均{cl['avgd']:.1f}km（遠いところで{cl['md']:.1f}km）。"
                       f"まわり2km以内の{E(short(c))}は{cl['comp']}か所" + (f"（定員あわせて{cl['compcap']}人）" if cl['compcap'] else "") + "。"
                       f"ほかの福祉の事業所が{cl['inner']}か所あり、人の住む地域。広さ約{cl['a']:.1f}km²。")
            if len(cs) > 1:
                why += f"{'・'.join(short(x) for x in cs[1:])}も同じように少ない場所です。"
            if note:
                why += f"【注意】{E(note)}。"
            h.append(f'<tr><th>{j}</th><td>{E("・".join(short(x) for x in cs))}</td><td>{E("・".join(cl["towns"]))}のあたり</td>'
                     f'<td><b>{sc / top_sc * 100:.0f}</b></td><td>{why}</td></tr>')
        h.append('</table>')
        for c_, sm in SITE.items():
            top = sm["areas"][:10]
            h.append(f'<h3>{E(short(c_))}：需給の倍率の高い地区（上位10）</h3>'
                     + bars_h([(a["towns"][0], a["idx"], j < 3) for j, a in enumerate(top)], unit="倍", fmt="{:.2f}")
                     + f'<p class="note">市全体では、{DEM_WHO[c_]}約{sm["d_city"]:,.0f}人に対して、{E(short(c_))}の受け皿は'
                     + (f'{sm["s_city"]:,.0f}か所' if c_ == "33" else f'定員{sm["s_city"]:,.0f}人分')
                     + f'（1席あたり約{sm["base"]:,.0f}人）。これを1.0として、半径{sm["R"]:.0f}km の中の数でくらべています。'
                     + (f'市の中で2022年以降に開いた{E(short(c_))}{sm["n_born"]}か所のうち、今は見えないのは{sm["g_city"] * 100:.0f}%で、'
                        'これより閉じた割合が高い地区は点数を下げています。' if sm["n_born"] else '') + '</p>')
        if SITE:
            h.append('<div class="method"><b>この点数の考え方と限界</b><ul>'
                     '<li>考え方は、商圏分析の「フェアシェア」（需要を、まわりの受け皿で分け合うと考える）を簡単にしたものです。'
                     '小売の出店で使われるハフモデル（近くて大きい店ほど客を集める）の、距離の重みを「半径の中か外か」にしぼった形です。</li>'
                     '<li>需要は、国勢調査（2020年）の町ごとの年れい別の人口です。障害のある人の割合は町によってちがいますが、町ごとの数は公表されていないので、市の中では同じと考えています。</li>'
                     + ('<li>グループホームは定員のデータがないので、受け皿は事業所の数で数えています。また、住む場所なので、市の外や遠くの町から入居する人も多く、ほかのサービスより距離の意味が小さくなります。</li>' if "33" in SITE else '')
                     + '<li>受け皿は、WAM NET に載っている事業所の定員です。市の外の近くの事業所もふくみます。市の外に住む人の需要はふくめていないので、市の境に近い地区は低めに出ます。</li>'
                     '<li>実際の利用は、送迎の範囲、事業所の評判、相談支援専門員や学校からの紹介でも大きく変わります。点数は「どこから見に行くか」の順番として使い、'
                     '最後は現地を歩き、近くの事業所の空き状況や相談支援事業所の話を聞いて決めてください。</li></ul></div>')
        if child:
            h.append('<h3>候補地のまわり（送迎・通いやすさ）</h3><table class="small txt2"><tr><th>順位</th><th>いちばん近い駅</th><th>小学校</th><th>いちばん近い特別支援学校</th></tr>')
        else:
            h.append('<h3>候補地のまわり（通いやすさ）</h3><table class="small txt2"><tr><th>順位</th><th>いちばん近い駅</th><th>1km以内の駅</th><th>2km以内の同じサービス</th></tr>')
        for j, (sc, c, cl, cs, note) in enumerate(cands, 1):
            st = nearby(STATIONS, cl["x"], cl["y"], 5)
            sta = f'{E(st[0][1][2])}駅（{E(st[0][1][3])}）約{st[0][0]:.1f}km' if st else "5km以内になし"
            if child:
                el = nearby(SCHOOLS["小学校"], cl["x"], cl["y"], 2)
                sp = nearby(SCHOOLS["特別支援学校"], cl["x"], cl["y"], 15)
                h.append(f'<tr><td>{j}</td><td>{sta}</td>'
                         f'<td>{f"2km以内に{len(el)}校（いちばん近いところ約{el[0][0]:.1f}km）" if el else "2km以内になし"}</td>'
                         f'<td>{E(sp[0][1][2]) + f" 約{sp[0][0]:.1f}km" if sp else "15km以内になし"}</td></tr>')
            else:
                n1 = len({q[1][2] for q in st if q[0] <= 1})
                h.append(f'<tr><td>{j}</td><td>{sta}</td><td>{n1}駅</td><td>{cl["comp"]}か所</td></tr>')
        h.append('</table><p class="note">候補地のまとまりの真ん中から、まっすぐの距離です。'
                 + ('放課後等デイは学校へのお迎えが毎日あるので、小学校・特別支援学校から車で10〜15分（およそ3〜5km）以内だと送迎が組みやすくなります。' if child else
                    '就労B型は、利用者が電車やバスで通うことが多いので、駅から歩ける場所（1km以内）だと利用者を集めやすくなります。' if ed == "b" else
                    'グループホームは住む場所なので、駅や買い物の近さ、日中に通う先（生活介護・B型など）への行きやすさが選ばれる理由になります。')
                 + '学校と駅の位置：国土交通省「国土数値情報（学校 2021年、鉄道 2023年）」。</p>')
    else:
        h.append('<p>住宅地で、同じサービスから1km以上はなれた大きな空白は見つかりませんでした。場所よりも、中身（曜日・時間・受け入れる子ども）で差をつける地域です（次の章）。</p>')
    if blocked:
        bl = collections.OrderedDict()
        for c, cl, note in blocked:
            bl.setdefault((short(c), note), []).append("・".join(cl["towns"][:2]))
        h.append('<p class="contact"><b>候補から外した場所：</b>' + "".join(
            f'{E(s_)}の{E("、".join(v[:3]))}のあたり（{E(n_)}）。' for (s_, n_), v in bl.items()) + '空白でも、市の決まりで新しく開けない場所です。</p>')
    h.append('<h3>サービスごとの見立て</h3><table class="small"><tr><th>サービス</th><th>人口あたり（県とくらべて）</th><th>2021年からの伸び</th><th>半年の増減</th><th>見立て</th></tr>')
    for c in MAIN[:6]:
        ratio, g, half, verdict = svc_view[c]
        h.append(f'<tr><th>{E(short(c))}</th><td>{ratio:.2f}倍</td><td>{f"{g * 100:+.0f}%" if g is not None else "—"}</td><td>{half:+d}</td>'
                 f'<td style="text-align:left">{"<b>" + verdict + "</b>" if "ねらい目" in verdict else verdict}</td></tr>')
    h.append('</table><p class="note">' + ('上の「ねらい目の場所」の点数は、需給の倍率に、近くで最近開いた事業所が続いているかと、市の決まり（総量規制）を加えたものです（1位を100とする）。'
             if SITE else '点数は、空白の広さ・人の住んでいる度合い（ほかの福祉の事業所の数）・近くの事業所までの距離・2km以内の同じサービスの数・'
             'そのサービスが県の平均より少ないか・利用率（放課後等デイのみ）から計算した、くらべるための目安です（1位を100とする）。')
             + 'この表の「見立て」は市全体の数の多い少ないで、町ごとの差は上の表で見てください。実際の物件さがしでは、駅や学校からの距離、送迎のしやすさもあわせて見てください。</p></section>')

    # 特別支援学校とまわりの放デイ
    sps = [p_ for p_ in SCHOOLS["特別支援学校"] if p_[3] == pref and inside(rings, p_[0], p_[1])]
    if sps and child:
        r65 = rows["65"]
        tab = []
        for x, y, nm, _ in sps:
            nb = nearby(r65, x, y, 2)
            tab.append((len(nb), sum(q[1][5] for q in nb), nearby(r65, x, y, 8)[:1], nm, x, y))
        tab.sort(key=lambda t: (t[0], -(t[2][0][0] if t[2] else 99)))
        h.append(f'<section class="pb"><h2>§特別支援学校とまわりの放課後等デイ</h2><p>{E(city)}の中の特別支援学校（{len(sps)}校）ごとに、'
                 '2km以内にある放課後等デイの数です。放課後等デイを使う子の多くは特別支援学校・特別支援学級に通っていて、学校へのお迎えが毎日あります。'
                 'まわりの放課後等デイが少ない学校の近くは、利用者を集めやすい場所です。</p>'
                 '<table class="small txt2"><tr><th>特別支援学校</th><th>2km以内の放課後等デイ</th><th>その定員</th><th>いちばん近い放課後等デイ</th><th>だいたいの場所</th></tr>')
        for n_, cap_, nr, nm, x, y in tab:
            town = ""
            for _, rr_ in nearby([q for qs in rows.values() for q in qs], x, y, 0.6)[:5]:
                town = town_of(rr_[2], rr_[3], city)
                if town:
                    break
            h.append(f'<tr><td>{E(nm)}</td><td>{n_}か所</td><td>{cap_ if cap_ else "—"}{"人" if cap_ else ""}</td>'
                     f'<td>{f"約{nr[0][0]:.1f}km" if nr else "8km以内になし"}</td><td>{E(town) or "—"}</td></tr>')
        h.append('</table><p class="note">学校の位置：国土交通省「国土数値情報（学校）」2021年。名前に「特別支援学校」「支援学校」「養護学校」がつく学校を数えています（分校・分教室もふくむ）。'
                 '特別支援学級（ふつうの小中学校の中の学級）の子どもも多く使っているので、小学校の多い住宅地もあわせて見てください。</p></section>')

    # ほかとの違い
    if ed != "gh":
        h.append('<section class="pb"><h2>§ほかとの違いの作り方</h2><p>同じサービスの事業所が、どの曜日・時間に開いているかを数えました。'
                 'ほかが開いていない曜日や時間に開くと、それだけで選ばれる理由になります。</p>'
                 f'<table class="small"><tr><th>サービス</th><th>土曜に開いている</th><th>日曜に開いている</th><th>平日18時半すぎまで</th><th>定員（真ん中の値）</th></tr>')
        tips = []
        for c in (SV if child else ["46"]):
            (sat, sun, late, n_c, n_h), (nsat, nsun, nlate, _, _), capv = diff[c]

            def cell(v, nv):
                return f"{v * 100:.0f}%<small>（全国{nv * 100:.0f}%）</small>" if v is not None else "—"
            md = statistics.median(capv) if capv else None
            h.append(f'<tr><th>{E(short(c))}</th><td>{cell(sat, nsat)}</td><td>{cell(sun, nsun)}</td><td>{cell(late, nlate)}</td>'
                     f'<td>{f"{md:g}人" if md else "—"}</td></tr>')
            kid = c in ("65", "63")
            who = "共働きの家庭" if kid else "平日に通いにくい人や、ほかの日に働いている人"
            if sat is not None and n_c >= 5:
                if sat < nsat - 0.05:
                    tips.append(f"{short(c)}は、土曜に開いているところが{sat * 100:.0f}%と全国（{nsat * 100:.0f}%）より少なめです。土曜に開くと、{who}に選ばれやすくなります。")
                if sun < nsun - 0.03:
                    tips.append(f"{short(c)}は、日曜に開いているところが{sun * 100:.0f}%と全国（{nsun * 100:.0f}%）より少なめです。日曜も開けば、{who}に選ばれやすくなります。")
            if kid and late is not None and n_h >= 5 and late < nlate - 0.02:
                tips.append(f"{short(c)}は、平日18時半すぎまで開いているところが{late * 100:.0f}%と全国（{nlate * 100:.0f}%）より少なめです。夕方おそくまで預かれると、働く親の助けになります。")
        h.append('</table>')
        if tips:
            h.append('<h3>差をつけられる点</h3><ul>' + "".join(f"<li>{E(t)}</li>" for t in tips) + '</ul>')
        else:
            h.append(('<p>土日や夕方に開いている事業所は、全国並みかそれより多い地域です。受け入れる子どもの種類（重い障害の子、医療的ケアの子など）や、プログラムの中身で差をつける地域です。</p>' if child else '<p>土日に開いている事業所は、全国並みかそれより多い地域です。作業の中身（工賃の高さ、在宅での利用、送迎）で差をつける地域です。</p>'))
        h.append('<p class="note">事業所が WAM NET に届け出た「定休日」「平日の営業時間」をもとに数えています。書かれていない事業所は数に入れていません。'
                 + ('「重い障害の子を受け入れる事業所」「医療的ケアに対応する事業所」はこのデータでは分けられません。</p></section>' if child else '工賃の金額はこのデータには入っていません。</p></section>'))

    # 2. 推移
    h.append(f'<section class="pb"><h2>§事業所の数の移り変わり</h2><p>{l0}から{lp}まで、半年ごとの数です。</p>')
    h.append(B.trend_chart(periods, total).replace('class="chart"', 'class="chart big"'))
    h.append('<div class="grid3">')
    for c in MAIN[:9]:
        h.append(f'<figure><figcaption>{E(short(c))}</figcaption>{line_chart(periods, ser[c])}</figure>')
    h.append('</div><p class="note">WAM NET に事業所の情報が載った数です。グラフの左はしと右はしの数字は、最初と最新の件数です。</p></section>')

    # 3. 人口あたり
    h.append(f'<section class="pb"><h2>§人口あたりでくらべる</h2><p>人口1万人あたりの事業所の数を、{E(pref)}の平均・全国の平均とくらべました。'
             '人口の多い市と少ない市を同じものさしでくらべるための数です。</p>')
    h.append('<table><tr><th>サービス</th><th>' + E(city) + '</th><th>' + E(pref) + '</th><th>全国</th><th>県とくらべて</th></tr>')
    for c in MAIN:
        a, b, n = comp[c]
        h.append(f'<tr><th>{E(short(c))}</th><td>{a:.2f}</td><td>{b:.2f}</td><td>{n:.2f}</td><td>{a / b:.1f}倍</td></tr>' if b else "")
    h.append('</table><div class="grid2">')
    for c in MAIN[:4]:
        a, b, n = comp[c]
        h.append(f'<figure><figcaption>{E(short(c))}（人口1万人あたり）</figcaption>'
                 + bars_h([(city, a, True), (pref, b, False), ("全国", n, False)], fmt="{:.2f}") + '</figure>')
    h.append('</div></section>')

    # 4. 区ごと／近く
    if wards:
        h.append(f'<section class="pb"><h2>§区ごとの数</h2><p>主なサービスの区ごとの数です。かっこの中は人口1万人あたりの数です。</p><table class="small"><tr><th>区</th><th>人口</th>'
                 + "".join(f"<th>{E(short(c))}</th>" for c in MAIN[:6]) + "</tr>")
        for wd in wards:
            wp = B.POP_MUNI[pi].get(wd)
            cells = "".join(
                f'<td>{S[c][latest]["muni"].get((pi, wd), 0)}<small>（{rate(S[c][latest]["muni"].get((pi, wd), 0), wp):.1f}）</small></td>'
                for c in MAIN[:6])
            h.append(f'<tr><th>{E(wd[len(city):])}</th><td>{wp:,}</td>{cells}</tr>' if wp else "")
        h.append('</table>')
        wv = [(wd[len(city):], rate(S[ED["main"]][latest]["muni"].get((pi, wd), 0), B.POP_MUNI[pi].get(wd)), False)
              for wd in wards if B.POP_MUNI[pi].get(wd)]
        h.append(f'<figure><figcaption>{E(lname(ED["main"]))}（区ごと、人口1万人あたり）</figcaption>'
                 + bars_h(sorted(wv, key=lambda x: -x[1]), fmt="{:.2f}") + '</figure></section>')
    else:
        h.append(f'<section class="pb"><h2>§近くの市区町村とくらべる</h2><p>{E(pref)}の中で、事業所の場所から見て近い市区町村です。かっこの中は人口1万人あたりの数です。</p>'
                 '<table class="small"><tr><th>市区町村</th><th>人口</th>' + "".join(f"<th>{E(short(c))}</th>" for c in MAIN[:6]) + "</tr>")
        for k in [(pi, city)] + near:
            kp = B.POP_MUNI[pi].get(k[1])
            cells = "".join(f'<td>{unit_count(c, k)}<small>（{rate(unit_count(c, k), kp):.1f}）</small></td>' for c in MAIN[:6])
            h.append(f'<tr{" class=me" if k == (pi, city) else ""}><th>{E(k[1])}</th><td>{kp:,}</td>{cells}</tr>' if kp else "")
        h.append('</table></section>')

    # 5. 新しく載った
    h.append(f'<section class="pb"><h2>§半年で新しく載った事業所</h2><p>{lq}には載っていなくて、{lp}に載った事業所です（主なサービスのみ）。'
             '開いたばかりの事業所のほか、届け出の出し直しで番号が変わった事業所もふくみます。</p>')
    h.append('<table class="small"><tr><th>サービス</th><th>新しく載った</th><th>見えなくなった</th></tr>'
             + "".join(f"<tr><th>{E(short(c))}</th><td>{len(newl[c])}</td><td>{len(gone[c])}</td></tr>" for c in MAIN)
             + f"<tr><th>全サービス</th><td>{n_new}</td><td>{n_gone}</td></tr></table>")
    for c in SV + [x for x in MAIN[:6] if x not in SV]:
        if not newl[c]:
            continue
        h.append(f'<h3>{E(short(c))}（{len(newl[c])}件）</h3><table class="list"><tr><th>事業所</th><th>運営する法人</th><th>場所</th><th>定員</th></tr>')
        for o in sorted(newl[c], key=lambda o: o["name"]):
            place = next((v[1] for mk, v in o["munis"].items() if in_city(mk, pi, city)), "")
            ward = next((mk[1][len(city):] for mk in o["munis"] if in_city(mk, pi, city) and mk[1] != city), "")
            h.append(f'<tr><td>{E(o["name"])}</td><td>{E(o["corp"])}</td><td>{E(ward + re.split(r"[0-9０-９]", place)[0])}</td><td>{E(o["cap"] or "—")}</td></tr>')
        h.append("</table>")
    h.append('</section>')

    # 続いているか
    h.append(f'<section class="pb"><h2>§開いた事業所は続いているか</h2><p>{E(city)}の事業所が、どれくらい続いているかを見ました。'
             f'「失敗しやすい地域か」の目安になります。{l0}にあった事業所のうち{lp}も載っている割合と、'
             f'途中（{B.label(periods[1])}〜{B.label(periods[-3])}）に新しく載った事業所のうち{lp}には見えない割合です。</p>'
             '<table class="small"><tr><th>サービス</th><th>' + E(l0) + 'にあった</th><th>今も続いている</th><th>途中で新しく載った</th>'
             '<th>今は見えない</th><th>全国（今は見えない）</th></tr>')
    sv_notes = []
    for c, ((na, ak, nb, bg), (_, nak, _, nbg)) in surv.items():
        h.append(f'<tr><th>{E(short(c))}</th><td>{na:,}</td><td>{f"{ak * 100:.0f}%" if ak is not None else "—"}<small>（全国{nak * 100:.0f}%）</small></td>'
                 f'<td>{nb:,}</td><td><b>{f"{bg * 100:.0f}%" if bg is not None else "—"}</b></td><td>{nbg * 100:.0f}%</td></tr>')
        if bg is not None and nb >= 10:
            if bg > nbg + 0.05:
                sv_notes.append(f"{short(c)}は、新しく載った事業所が見えなくなる割合が{bg * 100:.0f}%と全国（{nbg * 100:.0f}%）より高めです。競争がきびしいか、続けにくい地域です。")
            elif bg < nbg - 0.05:
                sv_notes.append(f"{short(c)}は、新しく載った事業所が見えなくなる割合が{bg * 100:.0f}%と全国（{nbg * 100:.0f}%）より低く、続けやすい地域です。")
    h.append('</table>' + ('<ul>' + "".join(f"<li>{E(x)}</li>" for x in sv_notes) + '</ul>' if sv_notes else
                           '<p>どのサービスも、全国と大きくは変わりません。</p>')
             + '<p class="note">WAM NET の事業所番号で追いかけています。閉じた事業所のほか、引っこしや法人の変更で番号が変わった事業所も「見えない」に入るので、実際に閉じた割合より高めに出ます。'
             '最後の1年に新しく載った事業所は、まだ早いので入れていません。</p></section>')

    # 6. 法人
    h.append(f'<section class="pb"><h2>§運営している法人</h2><p>{E(city)}で事業所を運営している法人は<b>{n_corp:,}</b>あります。'
             '1つの法人がいくつものサービスを運営していることがあるので、事業所の数で数えています。</p>')
    h.append('<h3>法人の種類（事業所の数の割合）</h3>' + stack_bar(ctype.most_common()))
    h.append('<h3>事業所の多い法人（上位10）</h3><table class="small txt"><tr><th>法人</th><th>事業所の数</th><th>運営しているサービス</th></tr>')
    for k, v in corps.most_common(10):
        h.append(f'<tr><td>{E(corp_name[k])}</td><td>{v}</td><td>{E("、".join(sorted(corp_svc[k])))}</td></tr>')
    top10 = sum(v for _, v in corps.most_common(10)) / max(len(offs), 1) * 100
    h.append(f'</table><p class="note">上位10法人で、全体の事業所の約{top10:.0f}%を運営しています。</p>')
    h.append(f'<h3>大手の法人の割合</h3><p>全国で{CHAIN_MIN}か所以上の事業所を運営する法人を「大手」としました。'
             f'{E(city)}の事業所のうち<b>約{chain_share * 100:.0f}%</b>が大手、{E(lname(ED["main"]))}にかぎると<b>約{share65 * 100:.0f}%</b>です。'
             f'半年で新しく載った事業所のうち大手は<b>約{new_chain_share * 100:.0f}%</b>'
             + ("で、大手の出店が目立ちます。" if new_chain_share > chain_share + 0.05 else "です。") + '</p>')
    if chains:
        h.append('<table class="small"><tr><th>大手の法人</th><th>この市の事業所</th><th>全国の事業所</th></tr>'
                 + "".join(f'<tr><td>{E(v[0][1]["corp"])}</td><td>{len(v)}</td><td>{CORP_N[k]:,}</td></tr>'
                           for k, v in sorted(chains.items(), key=lambda kv: -len(kv[1]))[:8]) + '</table>')
    h.append(f'<h3>半年で入ってきた法人・いなくなった法人</h3><p>{lq}には{E(city)}に事業所がなく{lp}に現れた法人は<b>{len(c_new)}</b>、'
             f'反対に{lq}にはあって{lp}に見えなくなった法人は<b>{len(c_gone)}</b>です。</p>')
    if c_new:
        h.append('<table class="small txt"><tr><th>入ってきた法人</th><th>事業所</th><th>サービス</th></tr>'
                 + "".join(f'<tr><td>{E(cin_now[k][0][1]["corp"])}</td><td>{len(cin_now[k])}</td><td>{E("、".join(sorted({short(c) for c, _ in cin_now[k]})))}</td></tr>'
                           for k in sorted(c_new, key=lambda k: -len(cin_now[k]))[:10]) + '</table>')
    h.append('<p class="note">法人番号（なければ法人名）で数えています。届け出の出し直しで、同じ法人が入れ替わって見えることもあります。</p></section>')

    # 7. 定員
    h.append('<section class="pb"><h2>§定員（受け入れられる人数）</h2><p>定員は、1日に受け入れられる人数として事業所が届け出ている数です。'
             '定員が書かれている事業所だけで足しています。</p><table><tr><th>サービス</th><th>事業所</th><th>定員が書かれている</th>'
             '<th>定員の合計</th><th>1事業所の定員（真ん中の値）</th><th>人口1万人あたりの定員</th></tr>')
    for c, (n, nc, sm, md) in caps.items():
        h.append(f'<tr><th>{E(short(c))}</th><td>{n}</td><td>{nc}</td><td>{sm:,}人</td><td>{md:g}人</td><td>{rate(sm, pop):.1f}人</td></tr>')
    h.append('</table><p class="note">どれくらい埋まっているか（利用率）は、次の章で市の計画の数字と合わせて見ます。</p></section>')


    # 8. 利用率
    if child:
        h.append('<section class="pb"><h2>§放課後等デイの利用率</h2><p>利用率は、定員の何%が実際に使われているかです。'
                 '市の障害福祉計画にある「1か月に使われた日数の合計（人日）」を、その時点の定員の合計と1か月の営業日（約23日）で割って出しました。</p>')
        if util:
            users = f"{hd['users']:,}人" if hd.get("users") else "—"
            h.append(f'<table><tr><th>項目</th><th>数</th><th>説明</th></tr>'
                     f'<tr><th>1か月に使われた日数の合計</th><td>{hd["nd"]:,}人日</td><td style="text-align:left">{fy_label(hd["fy"])}・{E(hd["kind"])}</td></tr>'
                     f'<tr><th>1か月に使った子どもの数</th><td>{users}</td><td style="text-align:left">同じ資料</td></tr>'
                     f'<tr><th>そのころの定員の合計（推計）</th><td>約{cap_then:,.0f}人</td><td style="text-align:left">そのころの事業所の数×いまの1事業所あたりの定員（{avg_cap:.1f}人）</td></tr>'
                     f'<tr><th><b>利用率の目安</b></th><td><b>約{util * 100:.0f}%</b></td><td style="text-align:left">全国の事業所の平均は89.1%（福祉医療機構、2024年度）</td></tr></table>')
            msg = ("定員を大きくこえて使われている計算です。WAM NET に載っていない事業所があるか、市の外の事業所に通う子どもが多いと考えられるので、数字そのものより「足りていない側」の目安として見てください。" if util > 1.2 else
                   "定員より多く使われている計算になります。市の外の事業所に通う子どもが多いか、定員をこえて受け入れている事業所が多いと考えられます。足りていない可能性が高い地域です。" if util > 1.0 else
                   "全国の平均より高く、空きの少ない地域です。新しい事業所にも利用者が集まりやすいと考えられます。" if util >= 0.9 else
                   "全国の平均に近い地域です。場所や中身で選ばれる事業所になることが大事です。" if util >= 0.8 else
                   "全国の平均より低く、空きのある地域です。新しく開設するなら、空白の地域をねらうか、ほかにない特色が必要です。")
            h.append(f'<p>{msg}</p>')
            if hd.get("users"):
                h.append(f'<p class="note">1人の子どもが1か月に使う日数は平均{hd["nd"] / hd["users"]:.1f}日です。</p>')
            h.append(f'<p class="note">出典：<a href="{E(hd["url"])}">{E(hd["src"])}</a>。市の数字は「市に住む子どもが使った量」で、市の外の事業所に通う分もふくみます。'
                     '定員はこのレポートのデータ（市の中の事業所）なので、おおよその目安です。</p>')
        else:
            h.append('<p>市の公表資料から、放課後等デイの「1か月に使われた日数（人日）」の実績を確認できませんでした。'
                     '次の章の試算では、全国の事業所の平均（89%）を使っています。市の担当課で障害児福祉計画の実績を確かめると、より正確になります。</p>')
        h.append('</section>')

    # 9. 人の集めやすさ
    if child:
        h.append(f'<section class="pb"><h2>§人の集めやすさ</h2><p>放課後等デイや児童発達支援では、保育士や児童指導員が欠かせません。'
                 f'「有効求人倍率」は、仕事をさがしている人1人に何件の求人があるかで、高いほど人を集めにくいことを表します。</p>'
                 f'<table><tr><th>項目</th><th>{E(pref)}</th><th>全国</th></tr>'
                 f'<tr><th>保育士の有効求人倍率（2026年1月）</th><td>{pf["hoiku"]:.2f}倍</td><td>{nf["hoiku"]:.2f}倍</td></tr>'
                 f'<tr><th>すべての仕事の有効求人倍率（2026年8月）</th><td>{pf["all"]:.2f}倍</td><td>{nf["all"]:.2f}倍</td></tr>'
                 f'<tr><th>保育士の平均月給（2025年）</th><td>{pf["pay_month"] / 10:,.1f}万円<small>（ボーナスこみ年{pf["pay"] / 10:,.0f}万円）</small></td><td>{nf["pay_month"] / 10:,.1f}万円<small>（年{nf["pay"] / 10:,.0f}万円）</small></td></tr>'
                 f'<tr><th>最低賃金（時給）</th><td>{pf["wage"]:,}円<small>（{E(pf["wage_from"])}から）</small></td><td>{nf["wage"]:,}円<small>（平均）</small></td></tr></table>')
        h.append(f'<figure><figcaption>保育士の有効求人倍率（2026年1月）</figcaption>'
                 + bars_h([(pref, pf["hoiku"], True), ("全国", nf["hoiku"], False), ("すべての仕事（全国）", nf["all"], False)], unit="倍", fmt="{:.2f}") + '</figure>')
        hard = pf["hoiku"] / pf["all"]
        h.append(f'<p>{E(pref)}では、保育士をさがす求人がほかの仕事の<b>約{hard:.1f}倍</b>出ています。'
                 + ("全国よりさらに集めにくい地域です。" if pf["hoiku"] > nf["hoiku"] else "全国よりは少しましですが、それでも集めにくい職種です。")
                 + 'いちばんの壁は、事業所に必ず1人置く「児童発達支援管理責任者」です。開く前に、だれがこの役をするかを決めておく必要があります。</p>'
                 f'<p class="note">次の章の人件費は、{E(pref)}の保育士の平均年収（{pf["pay"] / 10:,.0f}万円、全国{nf["pay"] / 10:,.0f}万円）に合わせて調整しています'
                 f'（千葉県の{chiba["pay"] / 10:,.0f}万円のとき年2,000万円）。家賃は{E(city)}の借家の平均家賃（月{cf["rent"]:,}円、全国{nf["rent"]:,}円）に合わせています。</p></section>')

    else:
        who_ = "世話人・生活支援員、夜の支援をする人" if ed == "gh" else "職業指導員・生活支援員"
        h.append(f'<section class="pb"><h2>§人の集めやすさ</h2><p>{E(lname(ED["main"]))}では、{who_}が欠かせません。'
                 '「有効求人倍率」は、仕事をさがしている人1人に何件の求人があるかで、高いほど人を集めにくいことを表します。</p>'
                 f'<table><tr><th>項目</th><th>{E(pref)}</th><th>全国</th></tr>'
                 f'<tr><th>すべての仕事の有効求人倍率（2026年8月）</th><td>{pf["all"]:.2f}倍</td><td>{nf["all"]:.2f}倍</td></tr>'
                 f'<tr><th>保育士の平均月給（2025年、福祉の給料の目安）</th><td>{pf["pay_month"] / 10:,.1f}万円</td><td>{nf["pay_month"] / 10:,.1f}万円</td></tr>'
                 f'<tr><th>最低賃金（時給）</th><td>{pf["wage"]:,}円<small>（{E(pf["wage_from"])}から）</small></td><td>{nf["wage"]:,}円<small>（平均）</small></td></tr></table>'
                 f'<p>{E(pref)}のすべての仕事の求人倍率は全国{"より高く、人を集めにくい地域" if pf["all"] > nf["all"] else "並みかそれより低く、比べれば人を集めやすい地域"}です。'
                 + ('夜の支援をする人は、短い時間・夜だけの働き方で集めることが多く、最低賃金の高さが人件費にひびきます。' if ed == "gh" else
                    '職業指導員は、作業の中身（軽作業、食品、農作業、パソコンの仕事など）に合った経験のある人を集めると、仕事を取りやすくなります。')
                 + 'いちばんの壁は、事業所に必ず置く「サービス管理責任者」です。実務の経験と研修が要るので、開く前に、だれがこの役をするかを決めておく必要があります。</p>'
                 f'<p class="note">次の章の人件費は、{E(pref)}の保育士の平均年収（{pf["pay"] / 10:,.0f}万円、全国{nf["pay"] / 10:,.0f}万円）を福祉の給料の目安として調整しています。'
                 f'家賃は{E(city)}の借家の平均家賃（月{cf["rent"]:,}円、全国{nf["rent"]:,}円）に合わせています。</p></section>')

    # 10. 収支試算
    if child:
        rows_sim = "".join(
            f'<tr><th>{k}</th><td>{u * 100:.0f}%</td><td>{pd:,.0f}円</td><td>{CAP10 * DAYS * u * pd / 1e4:,.0f}万円</td><td>{cost:,}万円</td>'
            f'<td><b>{sim_year(u, pd, cost):+,.0f}万円</b></td></tr>' for k, (u, pd) in per.items())
        if util:
            u2 = min(util, 0.95)
            rows_sim += (f'<tr class="me"><th>この市の利用率</th><td>{u2 * 100:.0f}%</td><td>{per["標準"][1]:,.0f}円</td>'
                         f'<td>{CAP10 * DAYS * u2 * per["標準"][1] / 1e4:,.0f}万円</td><td>{cost:,}万円</td><td><b>{sim_year(u2, per["標準"][1], cost):+,.0f}万円</b></td></tr>')
        grid_u = [0.7, 0.8, 0.89, 0.95]
        grid_p = [11000 * r, 12000 * r, 12500 * r, 13300 * r]
        grid = ("<table class=\"small\"><tr><th>利用率＼1人1日</th>" + "".join(f"<th>{p:,.0f}円</th>" for p in grid_p) + "</tr>"
                + "".join(f"<tr><th>{u * 100:.0f}%</th>" + "".join(f"<td>{sim_year(u, p, cost):+,.0f}</td>" for p in grid_p) + "</tr>" for u in grid_u)
                + "</table>")
        h.append(f'<section class="pb"><h2>§放課後等デイを開くときの収支試算</h2>'
                 f'<p>定員10人の放課後等デイを{E(city)}で開いた場合の、仮の1年間の収支です。{E(city)}は<b>{grade_txt}</b>で、報酬1単位は<b>{price:.2f}円</b>です'
                 f'（全国でいちばん高い東京23区は11.20円、上乗せなしは10.00円）。</p>'
                 f'<table><tr><th></th><th>利用率</th><th>1人1日の報酬</th><th>1年の売上</th><th>1年の費用</th><th>1年の収支</th></tr>{rows_sim}</table>'
                 f'<ul><li>黒字になる利用率は、標準の単価で<b>約{be * 100:.0f}%</b>です。</li>'
                 f'<li>開業費用と立ち上げの赤字、報酬が2か月おくれて入ることを合わせると、<b>手元資金は約{-low:,.0f}万円</b>必要です。'
                 + (f'標準の場合、開業から約{back // 12}年{back % 12}か月で元が取れます。</li>' if back else '標準の場合でも、10年では元が取れません。</li>')
                 + '</ul><h3>利用率と単価でどう変わるか（1年の収支、万円）</h3>' + grid
                 + f'<h3>前提</h3><table class="small txt2"><tr><th>項目</th><th>設定</th></tr>'
                 f'<tr><td>営業日</td><td>年{DAYS}日（経営調査の平均280.4日）</td></tr>'
                 f'<tr><td>1人1日の報酬</td><td>標準{per["標準"][1]:,.0f}円。全国平均12,747円（4級地相当）を{E(city)}の単価に合わせ、2026年6月からの新規事業所の減額を見こんで少し下げた</td></tr>'
                 f'<tr><td>人件費</td><td>職員6人で年{staff:,}万円（管理者兼児童発達支援管理責任者1人、常勤3人、パート2人。社会保険料をふくむ。{E(pref)}の保育士の給料に合わせた）</td></tr>'
                 f'<tr><td>そのほかの費用</td><td>年{other:,}万円（家賃月{rent / 12:.0f}万円、送迎車、光熱費、教材、保険、求人、開業費用の償却。家賃は{E(city)}の家賃の水準に合わせた）</td></tr>'
                 f'<tr><td>開業費用</td><td>{OPEN}万円（内装・備品、敷金、開業前の人件費、申請の準備）</td></tr>'
                 f'<tr><td>開業後の利用率</td><td>3か月ごとに30%→50%→70%→80%、その後半年85%、それからは標準の利用率</td></tr></table>'
                 '<p class="note">この試算は判断のたたき台です。家賃、給与、加算の取り方で大きく変わります。実際に開くときは、税理士や行政書士と作り直してください。税金はふくめていません。'
                 '数字のもと：福祉医療機構「2024年度 放課後等デイサービスの経営状況」（利用率89.1%、1人1日12,747円、赤字の事業所31.8%）。</p></section>')

    else:
        # B型・グループホームの収支試算
        m_ = gh if ed == "gh" else oth["46"]
        mc = ED["main"]
        word = "入居率" if ed == "gh" else "利用率"
        levels = {"慎重": 0.80, "標準": GH_UTIL, "好調": 0.97} if ed == "gh" else {"慎重": 0.70, "標準": m_["util"], "好調": 0.90}

        def rv_at(u):
            return m_["cap"] * m_["days"] * u * m_["pd"] / 1e4
        rows_m = "".join(f'<tr><th>{k}</th><td>{u * 100:.0f}%</td><td>{m_["pd"]:,.0f}円</td><td>{rv_at(u):,.0f}万円</td><td>{m_["ct"]:,}万円</td>'
                         f'<td><b>{rv_at(u) - m_["ct"]:+,.0f}万円</b></td></tr>' for k, u in levels.items())
        grid_u = [0.7, 0.8, 0.9, 0.97] if ed == "gh" else [0.6, 0.7, 0.8, 0.9]
        grid_k = [0.9, 1.0, 1.1]
        grid = ("<table class=\"small\"><tr><th>" + word + "＼1人1日の収益</th>"
                + "".join(f"<th>{m_['pd'] * k:,.0f}円</th>" for k in grid_k) + "</tr>"
                + "".join(f"<tr><th>{u * 100:.0f}%</th>" + "".join(f"<td>{rv_at(u) * k - m_['ct']:+,.0f}</td>" for k in grid_k) + "</tr>" for u in grid_u)
                + "</table>")
        if ed == "gh":
            pre = (f'<tr><td>定員と日数</td><td>定員{m_["cap"]}人（たとえば5人の住まいを2か所）、1年365日</td></tr>'
                   f'<tr><td>1人1日の収益</td><td>{m_["pd"]:,.0f}円。全国平均（定員1人あたり年{GH_REV}万円、入居者が払う家賃・食費などもふくむ）を、{E(city)}の1単位{m_["pr"]:.2f}円に合わせた</td></tr>'
                   f'<tr><td>人件費</td><td>年{m_["st"]:,}万円（世話人・生活支援員・夜の支援・サービス管理責任者。売上に対する給与の割合61.7%を、{E(pref)}の給料の水準に合わせた）</td></tr>'
                   f'<tr><td>そのほかの費用</td><td>年{m_["ct"] - m_["st"]:,}万円（建物の家賃月{m_["rent"] / 12:.0f}万円をふくむ。家賃は{E(city)}の家賃の水準に合わせた）</td></tr>'
                   f'<tr><td>開業費用</td><td>{m_["open"]:,}万円（建物の改修、消防の設備、敷金、開業前の人件費）</td></tr>')
            src = "厚生労働省「令和5年 障害福祉サービス等経営実態調査」（介護サービス包括型、2022年度決算：収益3,496万円、定員15人、収支差率9.1%）、「令和7年 経営概況調査」（給与の割合61.7%）"
            extra = ('<p>グループホームの1人あたりの売上は公表されている数が少なく、この試算は全国の平均的な事業所を定員10人にちぢめた<b>目安</b>です。'
                     '日中サービス支援型や外部サービス利用型では、職員の数も報酬も変わります。</p>')
        else:
            pre = (f'<tr><td>定員と日数</td><td>定員{m_["cap"]}人、年{m_["days"]}日営業</td></tr>'
                   f'<tr><td>1人1日の報酬</td><td>{m_["pd"]:,.0f}円。全国平均9,126円（4級地相当）を{E(city)}の1単位{m_["pr"]:.2f}円に合わせた。報酬は利用者に払う工賃の高さで変わる</td></tr>'
                   f'<tr><td>人件費</td><td>職員7〜8人で年{m_["st"]:,}万円（管理者、サービス管理責任者、職業指導員、生活支援員。社会保険料をふくむ。{E(pref)}の給料の水準に合わせた）</td></tr>'
                   f'<tr><td>そのほかの費用</td><td>年{m_["ct"] - m_["st"]:,}万円（家賃、送迎車、光熱費、保険、求人、開業費用の償却。家賃は{E(city)}の家賃の水準に合わせた）</td></tr>'
                   f'<tr><td>開業費用</td><td>{m_["open"]:,}万円（内装・作業の設備、敷金、開業前の人件費、申請の準備）</td></tr>'
                   '<tr><td>工賃</td><td>利用者に払う工賃は、作業で得たお金から払う前提で、この表の売上・費用には入れていない</td></tr>')
            src = "福祉医療機構「" + OTHERS["46"]["src"] + "」"
            extra = ('<p>B型の報酬は、利用者に払う工賃（平均月23,583円）が高いほど上がります。仕事（作業）を安定して取れるかが、売上と利用者集めの両方を左右します。</p>')
        h.append(f'<section class="pb"><h2>§{E(lname(mc))}を開くときの収支試算</h2>'
                 f'<p>定員{m_["cap"]}人の{E(lname(mc))}を{E(city)}で開いた場合の、仮の1年間の収支です。{E(city)}は<b>{grade_txt}</b>です。</p>'
                 f'<table><tr><th></th><th>{word}</th><th>1人1日</th><th>1年の売上</th><th>1年の費用</th><th>1年の収支</th></tr>{rows_m}</table>'
                 f'<ul><li>黒字になる{word}は<b>約{est["be"] * 100:.0f}%</b>です。</li>'
                 f'<li>開業費用と立ち上げの赤字、報酬が2か月おくれて入ることを合わせると、<b>手元資金は約{-est["low"]:,.0f}万円</b>必要です。'
                 + (f'標準の場合、開業から約{est["back"] // 12}年{est["back"] % 12}か月で元が取れます。</li>' if est["back"] else '標準の場合でも、10年では元が取れません。</li>')
                 + '</ul>' + extra + f'<h3>{word}と1人1日の収益でどう変わるか（1年の収支、万円）</h3>' + grid
                 + '<h3>前提</h3><table class="small txt2"><tr><th>項目</th><th>設定</th></tr>' + pre
                 + f'<tr><td>開業後の{word}</td><td>3か月ごとに30%→50%→70%→80%、その後半年85%、それからは標準の{word}</td></tr></table>'
                 '<p class="note">この試算は判断のたたき台です。家賃、給与、加算の取り方で大きく変わります。実際に開くときは、税理士や行政書士と作り直してください。税金はふくめていません。'
                 f'数字のもと：{E(src)}。</p></section>')


    # 投資の判断（投下資本・ROI・回収）
    if child:
        P_ = dict(cap=CAP10, days=DAYS, cost=cost, staff=staff, rent=rent, open_=OPEN, word="利用率",
                  lv={k: v for k, v in per.items()})
    else:
        P_ = dict(cap=m_["cap"], days=m_["days"], cost=m_["ct"], staff=m_["st"], open_=m_["open"], word=word,
                  rent=m_["rent"] if ed == "gh" else round(OTHERS["46"]["rent"] * rent_r),
                  lv={k: (u, m_["pd"]) for k, u in levels.items()})
    inv = {k: invest(P_["cap"], P_["days"], u, pd_, P_["cost"], P_["open_"]) for k, (u, pd_) in P_["lv"].items()}
    INV.update(std=inv["標準"])
    std_u, std_pd = P_["lv"]["標準"]

    def prof(u=std_u, pd_=std_pd, staff_=0, rent_=0):
        return P_["cap"] * P_["days"] * u * pd_ / 1e4 - (P_["cost"] + staff_ + rent_)
    base_p = prof()
    torn = [(f'{P_["word"]}（±5ポイント）', prof(u=std_u - 0.05) - base_p, prof(u=min(1, std_u + 0.05)) - base_p, "-5pt", "+5pt"),
            ("1人1日の報酬（±5%）", prof(pd_=std_pd * 0.95) - base_p, prof(pd_=std_pd * 1.05) - base_p, "-5%", "+5%"),
            ("人件費（±10%）", -(P_["staff"] * 0.1), P_["staff"] * 0.1, "+10%", "-10%"),
            ("家賃（±30%）", -(P_["rent"] * 0.3), P_["rent"] * 0.3, "+30%", "-30%")]
    torn.sort(key=lambda t: -abs(t[2] - t[1]))
    us = [x / 100 for x in range(50, 101, 5)]
    be_u = P_["cost"] / (P_["cap"] * P_["days"] * std_pd / 1e4)
    months = list(range(0, 61))
    cols = {"慎重": "#d9792b", "標準": "#2d7a45", "好調": "#3b6fb6"}

    def pct(v):
        return f"{v * 100:.0f}%" if v is not None else "—"

    def yrs(m):
        return f"約{m // 12}年{m % 12}か月" if m else "10年では回収できない"
    rows_i = "".join(
        f'<tr{" class=\"me\"" if k == "標準" else ""}><th>{k}</th><td>{u * 100:.0f}%</td><td>{v["need"]:,.0f}万円</td><td>{v["profit"]:+,.0f}万円</td>'
        f'<td><b>{pct(v["roi"])}</b></td><td>{yrs(v["back"])}</td><td>{v["c5"]:+,.0f}万円</td><td>{pct(v["irr"])}</td><td>{v["npv"]:+,.0f}万円</td></tr>'
        for k, v in inv.items() for u in [P_["lv"][k][0]])
    sd = inv["標準"]
    h.append(f'<section class="pb"><h2>§投資の判断：投下資本・ROI・回収</h2>'
             f'<p>{E(lname(ED["main"]))}（定員{P_["cap"]}人）を{E(city)}で開くとき、<b>いくら入れて、いくら返ってくるか</b>を、'
             f'前の章の収支試算と同じ前提で、10年間の月ごとのお金の出入りから計算しました。</p>'
             f'<div class="tiles"><div class="tile"><p class="tl">投下資本（要る手元資金）</p><p class="tv">{sd["need"]:,.0f}<small>万円</small></p></div>'
             f'<div class="tile"><p class="tl">ROI（1年の利益÷投下資本）</p><p class="tv">{pct(sd["roi"])}</p></div>'
             f'<div class="tile"><p class="tl">回収までの期間</p><p class="tv" style="font-size:14pt">{yrs(sd["back"])}</p></div></div>'
             f'<table class="small"><tr><th>見通し</th><th>{P_["word"]}</th><th>投下資本</th><th>落ち着いた後の1年の利益</th><th>ROI</th><th>回収まで</th><th>5年の累計</th><th>IRR（10年）</th><th>NPV（10年、割引率5%）</th></tr>{rows_i}</table>'
             '<ul class="small">'
             '<li><b>投下資本</b>：開業費用に、立ち上げの赤字と、報酬が2か月おくれて入る分を足した、いちばん手元資金が減ったときの額です。融資と自己資金でまかなう額の目安になります。</li>'
             '<li><b>ROI</b>：利用が落ち着いた年の利益を投下資本で割ったものです。何年分の利益で元が取れるかの逆数で、たとえば25%なら4年分です。</li>'
             '<li><b>IRR</b>：10年間のお金の出入りから出した、年あたりの利回りです。10年後の事業の価値（売却や続ける価値）は入れていないので、低めに出ます。</li>'
             '<li><b>NPV</b>：10年間のお金の出入りを、年5%で今の価値に直して足したものです。プラスなら、5%の利回りのほかの投資より有利という意味です。</li></ul>'
             '<h3>手元資金の動き（開業から5年）</h3>'
             + xy_chart([(k, cols[k], [inv[k]["cum"][m] for m in months]) for k in inv], months, "開業からの月数", "累計の手元資金（万円）", every=12,
                        marks=[(sd["back"], "標準で回収")] if sd["back"] and sd["back"] <= 60 else [])
             + f'<p class="note">最初に開業費用{P_["open_"]:,}万円が出ていき、利用が増えるまでの赤字でさらに減ってから、少しずつ戻ってきます（いわゆる「Jカーブ」）。'
             'いちばん下がったところが、用意しておくべき手元資金です。</p>'
             f'<h3>{P_["word"]}と1年の利益</h3>'
             + xy_chart([("1年の利益", "#2d7a45", [prof(u=u) for u in us])], [round(u * 100) for u in us], f'{P_["word"]}（%）', "1年の利益（万円）",
                        marks=[(round(be_u * 100), f"損益分岐 約{be_u * 100:.0f}%")] if 0.5 <= be_u <= 1 else [])
             + f'<p class="note">費用のほとんどが人件費と家賃で、{P_["word"]}にかかわらず出ていきます（固定費）。そのため、{P_["word"]}が5ポイント動くだけで、1年の利益が'
             f'約{abs(torn[0][2] if "率" in torn[0][0] else torn[1][2]):,.0f}万円動きます。</p>'
             '<h3>何が利益を一番動かすか（感応度）</h3>'
             + tornado(torn)
             + f'<p class="note">標準の見通し（1年の利益{base_p:+,.0f}万円）から、ひとつだけ動かしたときの変化です。上にあるものほど、利益への影響が大きい項目です。'
             'お金をかけて確かめる順番、気をつける順番の目安にしてください。</p>'
             '<p class="note">税金・借入の利息はふくめていません。費用には開業費用の償却もふくめているので、手元資金は少し多めに見積もっています。'
             '試算は全国の平均的な事業所の数字を、この市の単価・給料・家賃に合わせたもので、判断のたたき台です。</p></section>')

    # 経営の考え方から見る
    mc = ED["main"]
    sv0 = surv.get(mc, ((0, None, 0, None), (0, None, 0, None)))
    (_, keep_c, born_c, gone_c), (_, _, _, gone_n) = sv0
    lev = torn[0]
    slow = inv["慎重"]
    sm0 = SITE.get(mc)
    top0 = cands[0] if cands else None
    ref = sum(now.get(k, 0) for k in (["70", "52"] if child else ["52"]))
    job = (pf["hoiku"], nf["hoiku"], "保育士") if child else (pf["all"], nf["all"], "すべての仕事")
    th = []

    def card(title, idea, src, here, other):
        th.append(f'<div class="theory"><h3>{title}</h3><p class="idea">{idea}<span class="srcl">（{src}）</span></p>'
                  f'<p><b>{E(city)}にあてはめると：</b>{here}</p><p class="other"><b>別の見方：</b>{other}</p></div>')
    if sm0 and top0:
        here1 = (f'需給の倍率がいちばん高いのは{E(top0[2]["towns"][0])}のあたりで、新しく開いても1席あたりの需要が市の平均の約{top0[2].get("idx", 1):.1f}倍あります。'
                 f'市の中の上位10地区は、平均の{sm0["areas"][9]["idx"] if len(sm0["areas"]) > 9 else sm0["areas"][-1]["idx"]:.1f}〜{sm0["areas"][0]["idx"]:.1f}倍です。')
    else:
        here1 = '町ごとの人口がまだそろっていないので、空白地の地図を目安にしています。'
    card("1. 立地：需要を分け合う（商圏分析・ハフモデル）",
         "お客さんは、近くて大きい（選べる）お店に分かれて流れる、という考え方です。出店の前に「その範囲の需要」と「すでにある受け皿」をくらべるのが基本です。",
         "デイビッド・ハフ、1963年の論文。日本でもスーパー・ドラッグストアの出店調査で使われている",
         here1,
         "障害福祉は送迎があり、口コミや紹介で決まることも多いので、小売ほど距離だけでは決まりません。点数は「見に行く順番」として使ってください。")
    card("2. 1つの地域で一番になる（ランチェスター戦略・ドミナント出店）",
         "力の小さい会社は、広く浅く出るより、地域をしぼって、その地域で一番になる（局地戦・一点集中）のが勝ち方だ、という考え方です。"
         "コンビニのセブン-イレブンが、同じ地域にまとめて出店する「ドミナント戦略」で知られています。",
         "フレデリック・ランチェスターの法則を田岡信夫が販売戦略にまとめたもの。セブン-イレブンの高密度出店",
         f'{E(city)}の事業所のうち約{chain_share * 100:.0f}%は、全国で{CHAIN_MIN}か所以上を運営する大手の法人です。'
         '1か所目の場所は、2か所目・3か所目を近くに出せるか（送迎の車・職員をまわせるか）も考えて選ぶと、あとで強くなります。',
         "空白地をねらうと、競争は少ないものの、地域での知名度づくりに時間がかかります。すでに事業所がある地域は、需要があることが確かめられている、とも言えます（次の5.）。")
    card("3. 売上を最大に、経費を最小に（稲盛和夫）",
         "利益は売上と経費の差でしかないので、売上をできるだけ伸ばし、経費をできるだけおさえる、という単純な原則を毎日の数字で守る、という考え方です。",
         "稲盛和夫『稲盛和夫の実学―経営と会計』日本経済新聞社、1998年",
         f'この事業の費用はほとんどが人件費と家賃で、利用が少なくても出ていきます。前の章の感応度では、利益をいちばん動かすのは<b>{E(lev[0])}</b>で、'
         f'1年に約{max(abs(lev[1]), abs(lev[2])):,.0f}万円動きます。売上の側では{"利用率" if ed != "gh" else "入居率"}を毎月見て、早めに手を打つことが一番効きます。',
         "経費をけずりすぎると、職員が辞めて質が下がり、かえって利用が減ります。人件費は「おさえる」より「定着させる」ほうが、結果として安くつくことが多い事業です。")
    card("4. ダム式経営と安全域（松下幸之助・ベンジャミン・グレアム）",
         "ダムに水をためるように、お金・人・設備に余裕を持っておけば、景気や予想外のことに振り回されない、という考え方です。"
         "投資では「見通しが外れても大丈夫な幅（安全域）」を持って判断する、という考え方が近いです。",
         "松下幸之助『実践経営哲学』PHP研究所、1978年。ベンジャミン・グレアム『賢明なる投資家』1949年",
         f'標準の見通しの投下資本は約{sd["need"]:,.0f}万円ですが、慎重の見通しでは'
         + (f'約{slow["need"]:,.0f}万円' if slow["back"] else f'10年たっても回収できず、手元資金は減りつづけます')
         + '。用意するお金は「標準」ではなく「慎重」の側で考え、運転資金の借入枠も開業前に決めておくのが安全です。',
         "お金を多く借りすぎると、利息と返済が重くなります。手元資金の余裕は、開業後の最初の1年の動きを見て、増やすか減らすかを決めるのが現実的です。")
    card("5. 事業の目的は顧客の創造（ピーター・ドラッカー）",
         "事業は、顧客がだれで、何を価値と感じるかから始まる、という考え方です。",
         "P・F・ドラッカー『現代の経営』1954年、『マネジメント』1973年",
         f'この事業の「顧客」は、利用する本人と家族だけでなく、事業所を紹介する人たちでもあります。{E(city)}には'
         + (f'障害児相談支援・計画相談支援' if child else '計画相談支援')
         + f'の事業所が{ref:,}か所あり、そこの相談支援専門員がサービスの計画を作ります。開業の前にあいさつに回り、'
         '「どんな人を受け入れられるか」をはっきり伝えることが、最初の利用者につながります。',
         "紹介に頼りきると、紹介元の都合で利用が上下します。ホームページや地域の集まりで、家族に直接知ってもらう道も並行して作るのが安全です。")
    card("6. 競争の5つの力（マイケル・ポーター）",
         "業界のもうけやすさは、①同業との競争、②新しく入ってくる会社、③代わりになるもの、④買い手の力、⑤仕入れ先の力、の5つで決まる、という考え方です。",
         "マイケル・E・ポーター『競争の戦略』1980年",
         f'障害福祉では値段（報酬）を国が決めるので、値下げ競争は起きません。かわりに強いのは⑤の「仕入れ先」にあたる<b>人（職員）</b>の市場で、'
         f'{E(pref)}の{job[2]}の求人倍率は{job[0]:.2f}倍（全国{job[1]:.2f}倍）です。②の新しく入ってくる会社は、{E(city)}で半年に{n_new}か所でした。'
         '国が3年ごとに報酬を見直すこと（制度の変更）も、ほかの業界にはない大きな力です。',
         "値段で競えないぶん、質と評判で差がつきやすい業界でもあります。職員が働きやすい事業所は、利用者にも選ばれやすくなります。")
    card("7. 新しい市場か、確かめられた市場か（ブルー・オーシャン戦略）",
         "競争のない新しい市場（ブルー・オーシャン）を作れば、争わずにもうけられる、という考え方です。一方で、競争のある市場は需要があることが確かめられている、という見方もあります。",
         "W・チャン・キム、レネ・モボルニュ『ブルー・オーシャン戦略』2005年",
         (f'{E(city)}で2022年以降に新しく載った{E(short(mc))}は{born_c}か所で、そのうち今は見えないのは{gone_c * 100:.0f}%です（全国は{gone_n * 100:.0f}%）。'
          if born_c and gone_c is not None and gone_n is not None else '')
         + '候補地の表で「近くで開いた事業所が続いている」場所は、需要が確かめられた場所、「最近開いた事業所がない」場所は、まだためされていない場所です。',
         "空白地は「まだだれも気づいていない需要」のこともあれば、「需要がない」こともあります。現地で、近くの事業所の空き状況や待っている人の数を聞くと、どちらかがわかります。")
    card("8. 撤退の基準を先に決める（柳井正『一勝九敗』）",
         "新しいことは失敗するのがふつうで、失敗を早く認めて、傷が浅いうちに次へ移ることが大事だ、という考え方です。",
         "柳井正『一勝九敗』新潮社、2003年",
         f'開業前に「開業から1年で{"利用率" if ed != "gh" else "入居率"}が○%に届かなければ、場所や中身を見直す」という基準を決めておきます。'
         f'目安は、損益分岐の約{be_u * 100:.0f}%です。',
         "福祉の事業は、利用者の生活を支えているので、やめるときは次の事業所へのつなぎが必要です。撤退の基準は「やめる」より「立て直す」の合図として使うのが現実的です。")
    h.append('<section class="pb"><h2>§経営の考え方から見た、この市での開業</h2>'
             '<p>この章では、よく知られた経営の考え方を、このレポートの数字にあてはめて読みます。どれか1つが正しいというより、'
             '<b>同じ数字でも見方によって判断が変わる</b>ことを知っておくためのものです。それぞれに「別の見方」もつけました。</p>'
             + "".join(th)
             + '<div class="method"><b>このレポートでわかること・わからないこと</b><ul>'
             '<li>わかること：市の中のどこに需要と受け皿の差があるか、市全体の数の動き、開くといくら要り、いくら返ってくるかの目安、競争と人の集めやすさ。</li>'
             '<li>わからないこと：物件があるか、近くの事業所の空きと評判、相談支援専門員がどんな事業所を求めているか、あなたの法人の強み。'
             'これは、現地を歩き、人に会って確かめるしかありません。このレポートは、その前の「どこから、何を確かめに行くか」を決めるための資料です。</li></ul>'
             '<p class="note">本の中身は、要点を ジモふく の言葉でまとめたものです。</p></div></section>')

    # ほかのサービスの試算
    allm = {"65": dict(cap=CAP10, util=per["標準"][0], pd=per["標準"][1], rv=CAP10 * DAYS * per["標準"][0] * per["標準"][1] / 1e4,
                       st=staff, ct=cost, bal=sim_year(*per["標準"], cost), be=be, days=DAYS, pr=price),
            "63": oth["63"], "46": oth["46"], "33": gh}
    show = [c for c in ["65", "63", "46", "33"] if c not in (["65"] if child else SV)]
    rows_o = "".join(
        f'<tr><th>{E(short(c))}</th><td>{o["cap"]}人</td><td>{o["util"] * 100:.0f}%</td><td>{o["pd"]:,.0f}円</td><td>{o["rv"]:,.0f}万円</td>'
        f'<td>{o["ct"]:,}万円</td><td><b>{o["bal"]:+,.0f}万円</b></td><td>約{o["be"] * 100:.0f}%</td></tr>' for c in show for o in [allm[c]])
    pre_o = {
        "65": f'年{DAYS}日営業。職員6人で人件費年{staff:,}万円、そのほか年{other:,}万円（家賃をふくむ）。1単位{price:.2f}円',
        "63": f'年{OTHERS["63"]["days"]}日営業。職員6〜7人で人件費年{oth["63"]["st"]:,}万円、そのほか年{oth["63"]["ct"] - oth["63"]["st"]:,}万円（家賃をふくむ）。1単位{oth["63"]["pr"]:.2f}円',
        "46": f'年{OTHERS["46"]["days"]}日営業。職員7〜8人で人件費年{oth["46"]["st"]:,}万円、そのほか年{oth["46"]["ct"] - oth["46"]["st"]:,}万円（家賃をふくむ）。1単位{oth["46"]["pr"]:.2f}円。'
              '工賃（利用者の給料）は売上と別に、作業の収入から払う前提',
        "33": f'1年365日、入居率{GH_UTIL * 100:.0f}%。人件費年{gh["st"]:,}万円、そのほか年{gh["ct"] - gh["st"]:,}万円（建物の家賃をふくむ）。1単位{gh_pr:.2f}円。'
              '売上は入居者が払う家賃・食費などもふくむ全国平均からの目安',
    }
    src_o = {"65": "福祉医療機構「2024年度 放課後等デイサービスの経営状況」", "63": f'福祉医療機構「{OTHERS["63"]["src"]}」',
             "46": f'福祉医療機構「{OTHERS["46"]["src"]}」', "33": "厚生労働省「令和5年 障害福祉サービス等経営実態調査」"}
    h.append('<section class="pb"><h2>§ほかのサービスを開くときの収支試算</h2>'
             f'<p>{E("・".join(lname(c) for c in show))}を{E(city)}で開いた場合の、仮の1年間の収支です。'
             f'全国の平均的な事業所の数字を、{E(city)}の単価・給料・家賃に合わせています。いっしょに開くか、あとで広げるときの目安にしてください。</p>'
             '<table class="small"><tr><th>サービス</th><th>定員</th><th>利用率</th><th>1人1日</th><th>1年の売上</th><th>1年の費用</th><th>1年の収支</th><th>黒字になる利用率</th></tr>'
             + rows_o + '</table>'
             + "".join(f'<p class="contact"><b>注意（{E(short(c_))}）：</b>{E(caps_open[c_]["text"])}</p>' for c_ in show if c_ in caps_open)
             + ('<p class="contact"><b>注意（グループホーム）：</b>2027年4月からは総量規制の対象にできるようになるので、開く前に市の計画を確かめてください。</p>' if "33" in show else '')
             + '<h3>前提</h3><table class="small txt2"><tr><th>サービス</th><th>設定</th></tr>'
             + "".join(f'<tr><td>{E(short(c))}</td><td>{pre_o[c]}</td></tr>' for c in show) + '</table>'
             f'<p class="note">数字のもと：{E("、".join(src_o[c] for c in show))}。この試算は判断のたたき台です。</p></section>')

    # 10年後の需要
    if dem and child:
        h.append('<section class="pb"><h2>§10年後の需要</h2>'
                 f'<p>国立社会保障・人口問題研究所の将来推計人口（2023年推計）から、{E(city)}でサービスを使う年れいの子どもの数を出し、'
                 '使われる量（1か月の人日）と、それを受け止めるのに要る事業所の数を見積もりました。</p>')
        for c, d_ in dem.items():
            h.append(f'<h3>{E(SERVICES[c][0])}（{"6〜17歳" if c == "65" else "0〜5歳"}）</h3>'
                     '<table class="small"><tr><th>年</th><th>子どもの数（推計）</th><th>使われる量（少なめ）</th><th>使われる量（多め）</th>'
                     '<th>要る事業所（少なめ）</th><th>要る事業所（多め）</th></tr>'
                     + "".join(f'<tr><th>{y}年</th><td>{kv:,.0f}人</td><td>{lo:,.0f}人日</td><td>{hi:,.0f}人日</td><td>{nl:,.0f}か所</td><td>{nh:,.0f}か所</td></tr>'
                               for y, kv, lo, hi, nl, nh in d_["rows"]) + '</table>')
            n35l, n35h = d_["rows"][-1][4], d_["rows"][-1][5]
            h.append(f'<p>いまの事業所は<b>{d_["now"]:,}か所</b>です。2035年に要る事業所は<b>{n35l:,.0f}〜{n35h:,.0f}か所</b>の見こみで、'
                     + (f'いまより<b>{n35l - d_["now"]:+,.0f}〜{n35h - d_["now"]:+,.0f}か所</b>。' )
                     + ("子どもがへっても、使う子の割合が増えるかぎり、需要は大きくはへりません。" if d_["k35"] < d_["k25"] and n35h >= d_["now"] else
                        "子どもがへるため、10年後は今より事業所が余る可能性があります。開設するなら、選ばれる理由（場所・中身）がいっそう大事です。" if n35h < d_["now"] else
                        "子どもの数も需要も増える地域です。")
                     + '</p>')
        h.append('<p class="note">少なめ＝サービスを使う子の割合がいまのまま（子どもの数だけ変わる）。'
                 '多め＝使う子の割合が、2021年からの事業所の伸びから子どもの数の変化を引いたペース（年8%まで）で、あと5年伸びてから止まる。'
                 '1事業所が受け止める量は、いまの1事業所あたりの定員×1か月の営業日×89%。'
                 + ('放課後等デイの2025年の量は、市の数字から子どもの数の変化で出しています。' if util else '2025年の量は、いまの事業所が89%埋まっているとして出しています。')
                 + '子どもの数は5歳きざみの推計から、年れいの割合で出したおおよその数です。</p></section>')

    # 11. 地図
    for i, c in enumerate(SV):
        m = maps[c]
        h.append(f'<section class="pb"><h2>{"§近くにない地域の地図" if i == 0 else "§§近くにない地域の地図（つづき）"}</h2>'
                 f'<h3>{E(SERVICES[c][0])}</h3>'
                 f'<p>色は、いちばん近い{E(short(c))}までのまっすぐの距離です。点は{E(short(c))}の事業所（市の外の近くの事業所もふくむ）。'
                 f'数字の丸は、住宅地なのに1km以上はなれている場所のまとまりです。</p>'
                 f'<div class="mapbox">{m["svg"]}</div><p class="legend">'
                 + "".join(f'<span class="lg"><i style="background:{col}"></i>{lab}</span>' for _, col, lab in BANDS)
                 + '</p>')
        h.append(f'<p>1km以上はなれている場所は市の面積の<b>約{m["far"]:.0f}%</b>、住宅地の目安とした場所の<b>約{m["live_far"]:.0f}%</b>です。</p>')
        if m["clusters"]:
            h.append('<table class="small cl"><tr><th></th><th>だいたいの場所</th><th>広さ</th><th>いちばん遠いところ</th></tr>'
                     + "".join(f'<tr><th>{j}</th><td>{E("・".join(cl["towns"]) or "—")}のあたり</td><td>約{cl["a"]:.1f}km²</td><td>約{cl["md"]:.1f}km</td></tr>'
                               for j, cl in enumerate(m["clusters"], 1)) + '</table>')
        else:
            h.append('<p>住宅地で1km以上はなれている大きなまとまりは見つかりませんでした。</p>')
        h.append('</section>')

    # 付録：経営理念・行動規範のひな形
    who_v = {"child": ("子ども", "子どもと家族", "放課後や休みの日"), "gh": ("入居する人", "入居する人と家族", "毎日の暮らし"),
             "b": ("利用する人", "利用する人と家族", "働くこと")}[ed]
    ex = {"child": ["一人ひとりの「できた」を、家族と地域といっしょに育てる。",
                    "どの子も、自分らしく過ごせる放課後を、この町に。",
                    "子どもの今日の安心と、将来の自立を、ていねいに支える。"],
          "gh": ["その人らしい暮らしを、地域の中で、いっしょにつくる。",
                 "「ここが自分の家だ」と思える毎日を支える。",
                 "安心して住み続けられる場所を、この町に増やす。"],
          "b": ["働くよろこびと、地域とのつながりを、仕事を通して届ける。",
                "一人ひとりの「できる仕事」を見つけ、工賃と自信を育てる。",
                "地域に必要とされる仕事で、利用する人の暮らしを豊かにする。"]}[ed]
    h.append('<section class="pb"><h2>§付録：経営理念・行動規範のひな形</h2>'
             '<p>経営理念は、迷ったときに立ち返る「判断の物差し」です。職員の採用や、相談支援専門員・家族に事業所を説明するときにも使います。'
             '下のひな形をもとに、あなたの言葉に直して使ってください。</p>'
             '<h3>理念の3つの層</h3><table class="small txt2"><tr><th>層</th><th>問い</th><th>書き方のこつ</th></tr>'
             f'<tr><td><b>使命（ミッション）</b></td><td>だれの、何のために、この事業所があるのか</td><td>{who_v[1]}を主語にして、1文で。</td></tr>'
             '<tr><td><b>目指す姿（ビジョン）</b></td><td>5〜10年後、この地域で何を実現したいか</td><td>「○○な町」「○○が当たり前」のように、見て確かめられる形で。</td></tr>'
             '<tr><td><b>大切にすること（バリュー）</b></td><td>毎日の仕事で、何を優先するか</td><td>3〜5つ。迷ったときにどちらを選ぶかがわかる言葉で。</td></tr></table>'
             '<h3>使命の例</h3><ul>' + "".join(f"<li>{E(x)}</li>" for x in ex) + '</ul>'
             '<h3>理念を作るときに考えること</h3><ol class="small">'
             f'<li>なぜ、この事業を始めたいのか（きっかけになった{who_v[0]}や家族の顔を思い浮かべる）。</li>'
             f'<li>{E(city)}のこの地域で、まだ足りないものは何か（このレポートの需給の倍率や、ほかとの違いの章が手がかりになる）。</li>'
             '<li>職員に、どんな人でいてほしいか。どんな行動はしてほしくないか。</li>'
             '<li>10年後、地域の人に何と言われていたいか。</li></ol>'
             '<h3>行動規範のひな形（10か条）</h3><ol class="small">'
             f'<li><b>本人の思いを中心に</b>：{who_v[0]}の気持ちと選択を大切にし、本人の意思を確かめながら支援します。</li>'
             '<li><b>権利を守る</b>：虐待、不適切な関わり、身体拘束をしません。気づいたら、ためらわずに報告します。</li>'
             '<li><b>安全を最優先に</b>：ヒヤリとしたことも記録して共有し、事故を防ぎます。'
             + ('送迎では、車内の置き去りがないよう、必ず確認します。' if ed != "gh" else '夜間や災害のときの連絡と避難の手順を、全員が知っておきます。') + '</li>'
             '<li><b>秘密を守る</b>：知り得た個人の情報は、本人の同意なく外に出しません。SNS に利用者のことを書きません。</li>'
             '<li><b>正直に記録し、正しく請求する</b>：支援の記録は事実のとおりに書き、報酬は決まりのとおりに請求します。</li>'
             f'<li><b>{who_v[1]}とていねいに話す</b>：説明はわかりやすい言葉で行い、苦情や要望は改善の機会として受け止めます。</li>'
             '<li><b>チームで支える</b>：一人で抱えこまず、相談し、助け合います。ハラスメントを許しません。</li>'
             '<li><b>学び続ける</b>：研修や事例の振り返りで、支援の質を高め続けます。</li>'
             '<li><b>地域とつながる</b>：相談支援事業所、学校・医療・行政、近所の人と連携し、地域の一員として行動します。</li>'
             f'<li><b>続けられる経営をする</b>：{who_v[2]}を長く支えるために、むだを省き、職員が働き続けられる職場をつくります。</li></ol>'
             '<p class="note">このひな形は自由に使い、変えてかまいません。権利擁護・虐待防止・身体拘束の適正化・個人情報の取り扱いは、法律や国の基準で決まりがあるので、'
             '運営規程や研修の計画とあわせて整えてください。</p></section>')

    # 9. 出典
    h.append(f'''<section class="pb"><h2>§出典と注意</h2><ul class="src">
<li>事業所：独立行政法人福祉医療機構 WAM NET「障害福祉サービス等情報公表システムデータのオープンデータ」（{l0}〜{lp}、半年ごと）をもとに ジモふくが集計。</li>
<li>{E(B.POP_SOURCE)}（{E(B.POP_ASOF[0])}）。</li>
<li>地図の境界：国土交通省「国土数値情報（行政区域データ）」をもとに作成。</li>
<li>事業所の数は、WAM NET に情報が載っている数です。休んでいる事業所や、届け出がまだの事業所があると、実際の数とずれることがあります。</li>
<li>1つの事業所が、いくつかの場所でサービスをしているときは、それぞれの市区町村で1件と数えています。</li>
<li>地図の距離はまっすぐの距離で、道のりではありません。「住宅地の目安」は、ほかのサービスの事業所が500m以内にある場所としています。</li>
<li>法人の種類は、法人名にふくまれる言葉（株式会社・社会福祉法人など）で分けたおおよその数です。</li>
<li>地域区分：{E(FACTS["grade_source"])}。単価：{E(FACTS["unit_price_source"])}。</li>
<li>求人倍率：{E(FACTS["hoiku_source"])}、{E(FACTS["all_source"])}。最低賃金：{E(FACTS["wage_source"])}。</li>
<li>保育士の給料：{E(FACTS["pay_source"])}。家賃：{E(FACTS["rent_source"])}。</li>
<li>学校と駅の位置：国土交通省「国土数値情報（学校 2021年、鉄道 2023年）」。</li>
{"<li>放課後等デイの利用量：" + (E(hd["src"]) + "（" + E(hd["url"]) + "）" if hd else "市の公表資料で確認できませんでした") + "。</li>" if child else ""}
{"<li>グループホームの収支：厚生労働省「令和5年 障害福祉サービス等経営実態調査」「令和7年 障害福祉サービス等経営概況調査」をもとに ジモふくが試算。</li>" if ed == "gh" else ""}
<li>収支試算：{E(FACTS["other_source"])}をもとに ジモふくが試算。</li>
<li>将来推計人口：国立社会保障・人口問題研究所『日本の地域別将来推計人口（令和5年推計）』。</li>
</ul><p class="contact">このレポートの数字は自由に引用できます（「ジモふく調べ」と書いてください）。ほかのサービスの試算や、もっとくわしい調査のご相談は https://jimofuku.jp/ から。</p></section>''')
    return number_sections("\n".join(h))


def number_sections(doc):
    """見出しの「§」に順に番号をふり、表紙の目次を作る。「§§」は前の章のつづき。"""
    n, toc = 0, []

    def num(m):
        nonlocal n
        if m.group(1) == "§§":
            return f"<h2>{n}. "
        n += 1
        toc.append(f"<li>{n}. {m.group(2)}</li>")
        return f"<h2>{n}. "
    doc = re.sub(r"<h2>(§§?)([^<]*)", lambda m: num(m) + m.group(2), doc)
    return doc.replace("<!--TOC-->", '<ul class="toc">' + "".join(toc) + "</ul>")


CSS = """
@page { size: A4; margin: 16mm 14mm; }
* { box-sizing: border-box; }
body { font-family: "BIZ UDPGothic", "IPAGothic", sans-serif; color: #1c2a21; font-size: 10.5pt; line-height: 1.7; margin: 0; }
h1, h2, h3 { font-family: "Zen Maru Gothic", "BIZ UDPGothic", sans-serif; color: #1f4d3a; line-height: 1.35; }
h2 { font-size: 17pt; border-bottom: 3px solid #3d9a58; padding-bottom: 4px; margin: 0 0 10px; }
h3 { font-size: 12pt; margin: 16px 0 6px; }
section.pb { break-before: page; }
.cover { height: 255mm; display: flex; flex-direction: column; justify-content: center; padding: 0 8mm;
  background: #1f4d3a; color: #f4faf5; margin: -16mm -14mm 0; padding: 0 22mm; }
.cover h1 { color: #fff; font-size: 34pt; margin: 6px 0 14px; }
.cover .eyebrow { color: #a6e8b4; font-weight: 700; letter-spacing: .1em; margin: 0; }
.cover .edition { display: inline-block; align-self: flex-start; background: #a6e8b4; color: #1f4d3a; font-weight: 700; font-size: 13pt; padding: 4px 14px; border-radius: 6px; margin: 0 0 10px; }
.cover .sub { color: #b9d6c4; font-size: 13pt; margin: 0 0 30px; }
.cover .toc { list-style: none; padding: 0; columns: 2; font-size: 11pt; color: #e5ede7; }
.cover .by { margin-top: 40px; color: #b9d6c4; }
.points li { margin-bottom: 6px; }
table { border-collapse: collapse; width: 100%; margin: 6px 0 10px; font-size: 9.5pt; }
th, td { border-bottom: 1px solid #dfe8db; padding: 4px 6px; text-align: right; }
th:first-child, td:first-child { text-align: left; }
table.txt td { text-align: left; }
table.txt td:nth-child(2) { text-align: right; }
table.cl td:nth-child(2) { text-align: left; }
table.txt2 td { text-align: left; }
table.rank td { text-align: left; } table.rank td:nth-child(4) { text-align: right; }
ul li { margin-bottom: 4px; }
table.list td { text-align: left; font-size: 8.5pt; }
table.list td:last-child { text-align: right; }
tr:first-child th { background: #e2f2e2; color: #1f4d3a; font-size: 8.5pt; }
tr.me th, tr.me td { background: #fff2d6; font-weight: 700; }
table.small { font-size: 8.5pt; } small { color: #5b6c60; }
.note { font-size: 8.5pt; color: #5b6c60; }
.chart { width: 100%; height: auto; display: block; }
.chart .lab, .chart .val { font-size: 11px; fill: #1c2a21; } .chart .ax { font-size: 10px; fill: #5b6c60; }
.chart.big { max-width: 560px; }
.chart.big svg .b { fill: #b2dbb9; } .chart.big svg .b.last { fill: #2d7a45; }
.chart.big svg text { font-size: 12px; fill: #1c2a21; } .chart.big svg .base { stroke: #dfe8db; }
div.chart svg .b { fill: #b2dbb9; } div.chart svg .b.last { fill: #2d7a45; } div.chart svg text { font-size: 12px; fill: #1c2a21; } div.chart svg .base { stroke: #dfe8db; }
.grid3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px 14px; }
.grid2 { display: grid; grid-template-columns: repeat(2, 1fr); gap: 4px 18px; }
figure { margin: 6px 0; break-inside: avoid; } figcaption { font-weight: 700; font-size: 9.5pt; color: #1f4d3a; }
.legend { font-size: 8.5pt; } .lg { display: inline-block; margin-right: 12px; }
.lg i { display: inline-block; width: 10px; height: 10px; margin-right: 4px; vertical-align: -1px; border-radius: 2px; }
.mapbox { text-align: center; } .mapbox svg { max-width: 100%; max-height: 150mm; height: auto; width: auto; }
.src li { margin-bottom: 4px; font-size: 9.5pt; }
.method { border: 1px solid #cfe2d2; background: #f6faf5; border-radius: 8px; padding: 8px 14px; font-size: 9.5pt; margin: 10px 0; break-inside: avoid; }
.theory { border-left: 4px solid #2d7a45; padding: 2px 0 2px 12px; margin: 12px 0; break-inside: avoid; }
.theory h3 { margin: 0 0 4px; } .theory p { margin: 3px 0; font-size: 10pt; } .theory .idea { background: #f6faf5; padding: 4px 8px; border-radius: 4px; }
.theory .srcl { font-size: 8.5pt; color: #5b6c60; } .theory .other { color: #4a5a4f; }
.method ul { margin: 4px 0 0; padding-left: 1.2em; }
.lead { font-size: 13pt; font-weight: 700; color: #1f4d3a; background: #fff2d6; padding: 10px 14px; border-radius: 8px; }
.tiles { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin: 12px 0; }
.tile { border: 1px solid #cfe2d2; border-radius: 10px; padding: 8px 12px; break-inside: avoid; }
.tile p { margin: 0; } .tl { font-size: 8.5pt; color: #5b6c60; } .tv { font-size: 18pt; font-weight: 700; color: #1f4d3a; font-family: "Zen Maru Gothic", sans-serif; }
.ts { font-size: 8pt; color: #5b6c60; }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; vertical-align: -1px; }
.dot.green { background: #3d9a58; } .dot.yellow { background: #e7b33c; } .dot.red { background: #c9472f; } .dot.plain { background: #b8c4bb; }
table.sig td:nth-child(2) { text-align: left; }
.contact { margin-top: 20px; padding: 10px 14px; background: #e2f2e2; border-radius: 8px; }
"""

FONTS = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=BIZ+UDPGothic:wght@400;700'
         '&family=Zen+Maru+Gothic:wght@700&display=block">')


def main():
    out, geo = pathlib.Path(sys.argv[1]), sys.argv[2]
    want = [tuple(a.split(":")) for a in sys.argv[3:]] or TARGETS
    out.mkdir(parents=True, exist_ok=True)
    ctx = collect()
    periods, latest, prev, S, codes, rows = ctx
    # 主要都市の中での順位（人口1万人あたり）
    ranks = {}
    for c in MAIN:
        v = []
        for pref, city in TARGETS:
            pi = PREFS.index(pref)
            pop = B.POP_MUNI[pi].get(city)
            v.append(((city_counts(S, c, latest, pi, city) or 0) / pop, (pi, city)))
        ranks[c] = {k: i for i, (_, k) in enumerate(sorted(v, reverse=True), 1)}
    for c in codes:
        for o in S[c][latest]["offices"].values():
            CORP_N[o["corp_key"] or o["corp"]] += 1
    for pref, city in want:
        pi = PREFS.index(pref)
        for ed in EDITIONS:
            ED = EDITIONS[ed]
            body = build_one(ctx, pi, city, geo, ranks, ed)
            f = out / f"{latest}_{PREFS[pi]}_{city}_{ED['short']}.html"
            f.write_text(f'<!doctype html><html lang="ja"><head><meta charset="utf-8"><title>{E(city)}の障害福祉 事業所レポート {E(ED["name"])}（{B.label(latest)}）</title>'
                         f'{FONTS}<style>{CSS}</style></head><body>{body}</body></html>', encoding="utf-8")
            print("書き出し:", f.name)


if __name__ == "__main__":
    main()
