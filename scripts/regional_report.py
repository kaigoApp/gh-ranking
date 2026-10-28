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
MAIN = ["65", "63", "46", "33", "22", "52", "60", "45", "11", "24"]  # 詳しく見るサービス
MAPS = ["65", "63", "46", "33"]  # 近くにない地域の地図を作るサービス

# サービス別の版。svcs は「狙い目」と地図を出すサービス、main は収支試算の中心のサービス
EDITIONS = {
    "child": dict(name="放課後等デイ・児童発達支援版", short="放デイ・児発版", svcs=["65", "63"], main="65", mgr="児童発達支援管理責任者"),
    "gh": dict(name="グループホーム版", short="グループホーム版", svcs=["33"], main="33", mgr="サービス管理責任者"),
    "b": dict(name="就労継続支援B型版", short="就労B型版", svcs=["46"], main="46", mgr="サービス管理責任者"),
}
MAPCACHE = {}
INV = {}  # いま作っている版の投資の判断（まとめで使う）
MSG = {}  # 章ごとの結論の1文（章の見出しのすぐ下に出す）
SITE = {}  # いま作っている版の立地の点数
LABELS = {}  # 地図に書く区・町の名前
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
        raise SystemExit(f"{city} の境界が {f.name} にない")
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
    svg = None
    args = (rings, bb, kx, ky, step, cells, [r for r in svc], clusters)
    return dict(n=n, area=n * step * step, far=len(far) / n * 100 if n else 0,
                live_far=len(live_far) / len(live) * 100 if live else 0, clusters=clusters, svg=svg, args=args,
                inside=sum(1 for r in svc if inside(rings, r[0], r[1])))


BANDS = [(0.5, "#cfe9d3", "0.5km以内"), (1.0, "#8fcf9c", "0.5〜1km"), (2.0, "#f6c36b", "1〜2km"), (99, "#d9792b", "2km より遠い")]


def map_labels(geo_dir, pi, city, rings):
    """地図に書く名前：政令市は区、ほかは人の多い町（重ならないように間をあける）。"""
    f = pathlib.Path(geo_dir) / f"N03_{pi + 1:02d}.json"
    g = json.loads(f.read_text(encoding="utf-8"))
    wards = collections.defaultdict(list)
    for ft in g["features"]:
        pr = ft["properties"]
        if pr["N03_003"] == city and pr["N03_004"]:
            gm = ft["geometry"]
            for poly in (gm["coordinates"] if gm["type"] == "MultiPolygon" else [gm["coordinates"]]):
                wards[pr["N03_004"]].append(poly[0])
    out = []
    for w, rs in wards.items():
        r = max(rs, key=len)
        out.append((sum(p[0] for p in r) / len(r), sum(p[1] for p in r) / len(r), w, "ward"))
    load_small()
    areas = collections.defaultdict(lambda: [0, 0.0, 0.0])
    for t in SMALL:
        if t["muni"].startswith(city) and t["v"].get("0～4歳", 0) >= 0:
            n = sum(t["v"].values())
            nm = area_name(t["muni"], t["town"], city)
            nm = nm[len(t["muni"]) - len(city):] if t["muni"] != city else nm  # 区の名前は外す
            a = areas[nm]
            a[0] += n
            a[1] += t["x"] * n
            a[2] += t["y"] * n
    xs = [p[0] for r, _ in rings for p in r]
    ys = [p[1] for r, _ in rings for p in r]
    span = max(km(min(xs), min(ys), max(xs), min(ys)), km(min(xs), min(ys), min(xs), max(ys)))
    gap = span / (9 if out else 6)
    taken = [(x, y) for x, y, _, _ in out]
    n_town = 0
    for nm, (n, sx, sy) in sorted(areas.items(), key=lambda kv: -kv[1][0]):
        if not n or not nm or n_town >= (10 if out else 16):
            continue
        x, y = sx / n, sy / n
        if all(km(x, y, a, b) > gap for a, b in taken):
            out.append((x, y, nm, "town"))
            taken.append((x, y))
            n_town += 1
    return out


LAND = []


def load_land():
    p = B.ROOT / "data" / "report" / "landprice.csv"
    if LAND or not p.exists():
        return
    with p.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            LAND.append(dict(use=r["use"], price=int(r["price"]), x=float(r["lng"]), y=float(r["lat"]), addr=r["addr"]))


def land_factor(city, x, y, r=1.5):
    """候補地の周辺の地価が、市の真ん中の値の何倍か。商業地を優先し、なければ住宅地。
    家賃は地価ほど大きくは変わらないので、家賃の倍率は地価の倍率の平方根（0.6〜1.6倍に抑える）とする。
    (家賃の倍率, 使った用途, 周辺の地価の真ん中の値, 市の真ん中の値, 地点の数)"""
    load_land()
    mine = [p for p in LAND if re.search(r"(^|[\s　])" + re.escape(city), p["addr"])]  # 「東大阪市」「東広島市」を外す
    for use in ("商業地", "住宅地"):
        allp = sorted(p["price"] for p in mine if p["use"] == use)
        near = sorted(p["price"] for p in mine if p["use"] == use and km(x, y, p["x"], p["y"]) <= r)
        if len(near) >= 2 and allp:
            med, cm = statistics.median(near), statistics.median(allp)
            return max(0.6, min(1.6, (med / cm) ** 0.5)), use, med, cm, len(near)
    return 1.0, None, None, None, 0


def map_svg(rings, bb, kx, ky, step, cells, svc, clusters, labels=(), picks=()):
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
    for x, y, t, kind in labels:  # 区・町の名前（白いふちどりで読みやすく）
        px, py = P(x, y)
        if not (0 < px < W and 0 < py < H):
            continue
        fs, fw, col = (15, 800, "#1f4d3a") if kind == "ward" else (10.5, 600, "#33443a")
        out.append(f'<text x="{px:.1f}" y="{py:.1f}" text-anchor="middle" font-size="{fs}" font-weight="{fw}" fill="{col}" '
                   f'stroke="#fff" stroke-width="3" paint-order="stroke" stroke-linejoin="round">{E(t)}</text>')
    for i, c in enumerate(clusters):
        px, py = P(c["x"], c["y"])
        lab = "ABCDEFGH"[i]
        out.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="9" fill="#fff" stroke="#5b6c60" stroke-width="1.5" stroke-dasharray="3 2"/>'
                   f'<text x="{px:.1f}" y="{py + 4:.1f}" text-anchor="middle" font-size="11" font-weight="700" fill="#5b6c60">{lab}</text>')
    for j, c in picks:  # 「開設するならどこが良いか」の順位
        px, py = P(c["x"], c["y"])
        out.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="11" fill="#b3392c" stroke="#fff" stroke-width="2"/>'
                   f'<text x="{px:.1f}" y="{py + 4.5:.1f}" text-anchor="middle" font-size="13" font-weight="800" fill="#fff">{j}</text>')
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
    """月ごとの手元資金の出入り（万円）。0か月目に開業費用、報酬は2か月遅れて入る。"""
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
    fl = month_flows(cap, days, util, perday, cost, open_, months=360)  # 回収の月は30年まで探す
    cum, c = [], 0.0
    for v in fl:
        c += v
        cum.append(c)
    fl = fl[:121]
    need = -min(cum[:121])
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
RADIUS = {"65": 2.0, "63": 2.0, "46": 3.0, "33": 3.0}  # 通える範囲（km）。放課後等デイ・児発は送迎、B型・GHは電車・バスも使う
NEWCAP = {"65": 10, "63": 10, "46": 20, "33": 1}  # 新しく開設する受け皿（グループホームは定員のデータがないので1か所＝1）
SMALL = []
ADJ = set()   # 令和7年国勢調査で補正した市区町村コード


def load_r7():
    """令和7年国勢調査の市区町村別・年齢5歳階級別人口（年齢不詳を按分）。{市区町村コード: {年齢: 人}}"""
    p = B.ROOT / "data" / "report" / "raw" / "r7_age5.txt"
    out = {}
    if not p.exists():
        return out
    head = None
    for line in p.read_text(encoding="utf-8").splitlines():
        c = line.split("\t")
        if len(c) > 30 and c[8] == "項目名":
            head = [re.sub(r"^\d+_", "", x) for x in c]
        if head is None or len(c) < 30 or not c[0].startswith("0_") or not c[1].startswith("0_") or c[2] == "a":
            continue
        v = {head[i]: int(c[i]) for i in range(9, len(c)) if c[i].isdigit() and "再掲" not in head[i]}
        known = v.get("総数", 0) - v.get("年齢「不詳」", 0)
        if not known:
            continue
        k = v["総数"] / known
        ages = {a: v[a] * k for a in v if a.endswith("歳") and a != "年齢「不詳」" and not a.startswith(("75", "80", "85", "90", "95"))}
        ages["75歳以上"] = sum(v.get(a, 0) for a in ("75～79歳", "80～84歳", "85～89歳", "90～94歳", "95～99歳", "100歳以上")) * k
        out[c[7]] = ages
    return out


def load_small():
    """町丁別・年齢別人口。町丁ごとの割合は令和2年国勢調査、市区町村の年齢別の合計は令和7年国勢調査に合わせる。"""
    p = B.ROOT / "data" / "report" / "smallarea.csv"
    if SMALL or not p.exists():
        return
    with p.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            SMALL.append(dict(code=r["key"][:5], muni=r["muni"], town=r["town"], x=float(r["lng"]), y=float(r["lat"]),
                              v={k: int(r[k]) for k in r if k.endswith("歳") or k.endswith("以上")}))
    r7 = load_r7()
    tot = collections.defaultdict(lambda: collections.Counter())
    for t in SMALL:
        tot[t["code"]].update(t["v"])
    for t in SMALL:
        new, old = r7.get(t["code"]), tot[t["code"]]
        if new:
            t["v"] = {a: n * new[a] / old[a] if old[a] and a in new else n for a, n in t["v"].items()}
            ADJ.add(t["code"])


def area_name(muni, town, city):
    ward = muni[len(city):] if muni.startswith(city) else ""
    t = re.sub(r"（.*?）$", "", town)
    t = re.sub(r"[0-9０-９一二三四五六七八九十]+(丁目|丁|番町|条.*|線.*)$", "", t)
    return ward + t


def decay(d, R):
    """距離による重み（ガウス型。Luo & Qi 2009 の E2SFCA などで使われる形）。R km で 0 になる。"""
    if d > R:
        return 0.0
    e = math.exp(-0.5)
    return (math.exp(-0.5 * (d / R) ** 2) - e) / (1 - e)


class Bucket:
    """近くの点をすばやく探すための、およそ1km四方のます目。"""
    def __init__(self, pts):
        self.g = collections.defaultdict(list)
        for p in pts:
            self.g[(int(p[0] * 80), int(p[1] * 110))].append(p)

    def near(self, x, y, R):
        n = int(R) + 1
        kx, ky = int(x * 80), int(y * 110)
        for i in range(kx - n, kx + n + 1):
            for j in range(ky - n, ky + n + 1):
                yield from self.g.get((i, j), ())


def site_model(c, city, rings, rows, periods):
    """町ごとの需要（サービスを使う年齢の人口）を、周辺の事業所が定員と距離に応じて分け合うと考えて
    （ハフモデル。重みは E2SFCA と同じガウス型の距離減衰）、新しく開設された事業所に来ると見込める利用を出す。
    倍率＝見込める利用 ÷ 新しい定員。1.0 は市の平均並みに埋まる、という意味。"""
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
    u = s_city / d_city  # 市の中で、需要1人あたりの席の数（市の平均の使われ方）
    fb = Bucket([(r[0], r[1], seat(r)) for r in svc])
    tb = Bucket([(t["x"], t["y"], t["d"]) for t in towns])
    for t in towns:  # 町ごとに、周辺の事業所の「引きつける力」の合計
        t["pull"] = sum(sv * decay(km(t["x"], t["y"], fx, fy), R) for fx, fy, sv in fb.near(t["x"], t["y"], R))
    tpull = {(t["x"], t["y"]): t["pull"] for t in towns}
    # 最近開設された事業所と、それが続いているか（最初の時点より後に登録され、最後の1年より前に登録されたもの）
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
        users = use_r = dem_r = 0.0
        for tx, ty, td in tb.near(cx, cy, R):
            wd = decay(km(cx, cy, tx, ty), R)
            if wd <= 0:
                continue
            dem_r += td
            use_r += td * u
            users += td * u * new * wd / (tpull[(tx, ty)] + new * wd)
        near_s = [r for r in fb.near(cx, cy, R) if km(cx, cy, r[0], r[1]) <= R]
        s_r = sum(r[2] for r in near_s)
        idx = users / new
        cap_rate = new / use_r if use_r else None
        nb = [g for (x, y), g in born if abs(y - cy) < R / 110 and abs(x - cx) < R / 80 and km(cx, cy, x, y) <= R]
        g_r = sum(nb) / len(nb) if nb else None
        sc = idx * (1 - 0.5 * max(0.0, (g_r or 0) - g_city)) if len(nb) >= 3 else idx
        nd = min((km(cx, cy, r[0], r[1]) for r in svc), default=9.9)
        out.append(dict(towns=[name], x=cx, y=cy, dem=dem_r, seats=s_r, comp=len(near_s), compcap=s_r if c != "33" else 0,
                        idx=idx, sc=sc, users=users, use_r=use_r, cap_rate=cap_rate, short=use_r - s_r,
                        born=len(nb), gone=sum(nb), g=g_r, avgd=nd, md=nd, a=0, inner=1, own=dd))
    out.sort(key=lambda a: -a["sc"])
    return dict(areas=out, base=base, u=u, d_city=d_city, s_city=s_city, g_city=g_city, n_born=len(born_city), R=R, new=new, med=med)

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
OTHER = 680  # 家賃・送迎車・光熱費など＋開業費用の償却（万円、船橋市のとき）
RENT = 240  # そのうち家賃（月20万円、船橋市のとき）
# 人件費は職種ごとに積み上げる。給与は厚生労働省「令和6年度 障害福祉サービス等従事者処遇状況等調査」（2024年9月、全国、
# 処遇改善加算を取っている事業所）。月額は基本給＋手当＋賞与（4〜9月分の1/6）なので、12倍で年収のおよそになる。
PAY_SAB = 405480  # サービス管理責任者等（常勤、第86表）。管理者を兼ねる前提
PAY_FT = {"65": 286110, "63": 293940, "46": 289130, "33": 291050}  # 福祉・介護職員（常勤、サービス別、第90表）
PAY_PT = {"65": 94760 / 64.1, "63": 98840 / 70.3, "46": 99680 / 74.6, "33": 99420 / 65.9}  # 非常勤の時給（第91表の月額÷実労働時間）
FT_RATE = {"65": 0.79, "63": 0.81, "46": 0.71, "33": 0.56}  # 直接支援の職員のうち常勤の割合（令和7年 経営概況調査）
DIRECT = {"65": 3.5, "63": 3.8, "46": 3.5}  # 直接支援の職員（常勤換算）。経営概況調査の全国平均を試算の定員に合わせた
# B型は、経営概況調査の給与費（定員1人あたり123万円）が基準の職員だけでは説明できないので、目標工賃達成指導員・送迎・事務などを足す
DIRECT_OTHER = {"46": 1.0}
FTE_H = 1930  # 常勤1人の1年の労働時間（処遇状況等調査の常勤の実労働 月160.7時間×12）
RAISE = 1.06  # 2024年9月から2026年度までの賃上げ（年3%×2年と仮定）
SOCIAL = 0.164  # 社会保険料などの会社負担（厚生年金9.15%、健康保険・介護保険・子ども子育て支援金 約5.8%、雇用保険0.85%、労災0.3%、子ども・子育て拠出金0.36%）
LEAD = {"65": "管理者兼児童発達支援管理責任者", "63": "管理者兼児童発達支援管理責任者", "46": "管理者兼サービス管理責任者", "33": "管理者兼サービス管理責任者"}
# グループホーム（介護サービス包括型）。売上は報酬の単位から、人件費は職種ごとに積み上げる
GH_HOUSE = 10  # 1住居の定員
GH_HOUSES = 1  # 試算の住居の数（定員10人の1住居が基本。2住居・3住居は広げた場合として示す）
GH_UTIL = 0.9  # 入居率
GH_UNITS = {"6": 600, "5": 456, "4": 372, "3": 297, "2": 188, "1": 171}  # 基本報酬（2024年度改定後、世話人6:1、1人1日の単位）
GH_SUP = {"6": 2.5, "5": 4, "4": 6, "3": 9}  # 生活支援員の配置（入居者何人に1人、指定の基準）
GH_NIGHT = {"6": 1344, "5": 1344, "4": 1344, "3": 1120, "2": 896, "1": 896}  # 夜間支援等体制加算(Ⅰ)の1住居1晩あたり（1人あたりの単位×人数。4人なら区分4以上336単位×4）
GH_SHOGU = 0.163  # 福祉・介護職員等処遇改善加算(Ⅰ)の率（2026年6月から、介護サービス包括型）
NIGHT_SHIFT = 2.0  # 夜勤1回の給料（万円、千葉県のとき。首都圏の求人は1回1.6〜2.0万円）
SHIFT_DAY = 9  # 夜勤（16時〜翌9時）のうち、夕方と朝の世話人・生活支援員として数える時間
GH_OTHER = 27  # 家賃のほかの費用（定員1人あたり年、万円。経営概況調査の「その他」と委託費から、入居者が払う利用料の分を引いた）
GH_RENT = 360  # 1住居の建物の家賃（年、万円。月30万円、全国平均の家賃のとき）。入居者が払う家賃で回収する
GH_OPEN = 600  # 1住居の開業費用（改修・消防設備・敷金・開業前の人件費）
GH_MIX = [("区分の軽い入居者が中心", {"1": .3, "2": .4, "3": .3}), ("区分3〜4が中心（試算の前提）", {"2": .2, "3": .4, "4": .4}),
          ("区分の重い入居者が中心", {"4": .3, "5": .4, "6": .3})]
# グループホーム（日中サービス支援型）。日中も職員を置き、夜勤は必須、短期入所の併設が要る
GHD_CAP = 20  # 定員（定員10人の住居×2を1つの建物で運営する前提）
GHD_UNITS = {"6": 997, "5": 860, "4": 771, "3": 524}  # 基本報酬（2024年度改定後、世話人5:1、1人1日の単位）
GHD_MIX = {"3": .2, "4": .4, "5": .25, "6": .15}  # 入居者の区分（重い人が中心）
GHD_DIRECT = (4.13 + 3.09 + 0.79) / 15  # 生活支援員・世話人・夜間支援従事者の常勤換算（定員1人あたり。令和7年 経営概況調査 第18表、定員15人で8.01人）
GHD_OTHER = 48  # 家賃のほかの費用（定員1人あたり年、万円。経営概況調査のその他・委託費・減価償却から利用料の分を引いた）
GHD_OPEN = 1500  # 開業費用（2住居分の改修・消防設備・敷金・短期入所の部屋・開業前の人件費）
GHD_NAT = dict(rv=3952, st=38375 / 15, bal=0.051, cap=15, staff=9.28, n=236)  # 全国平均（定員1人あたり千円、収支差率）
OPEN = 900  # 開業費用（万円）
RAMP = [.3] * 3 + [.5] * 3 + [.7] * 3 + [.8] * 3 + [.85] * 6  # 開業からの利用率の伸び（その後は標準の利用率）


def fy_label(fy):
    return f"{fy}年度"


def sim_year(util, perday, cost):
    """1年の収支（万円）"""
    return CAP10 * DAYS * util * perday / 1e4 - cost


def cash_path(util, perday, cost, months=360, cap=CAP10, days=DAYS, open_=OPEN):
    """手元資金の動き。報酬は2か月遅れて入る。最も減ったところ（10年まで）と、元が取れる月（30年まで）。"""
    cash, low, back = -open_, -open_, None
    rev = []
    for m in range(months):
        u = min(RAMP[m], util) if m < len(RAMP) else util
        rev.append(cap * days / 12 * u * perday / 1e4)
        cash += (rev[m - 2] if m >= 2 else 0) - cost / 12
        if m < 120:
            low = min(low, cash)
        if back is None and m > 12 and cash >= 0:
            back = m + 1
    return low, back


SURV = ["65", "63", "46", "33", "22"]  # 続いているかを見るサービス
HIST = collections.defaultdict(dict)  # HIST[サービス][時点] = {事業所番号: (県, 市区町村)}
POS = collections.defaultdict(dict)  # POS[サービス][事業所番号] = (経度, 緯度)


def survival(periods, c, keep):
    """keep(県, 市区町村) に入る事業所について、
    (最初の時点にあった数, そのうち今もある割合, 途中で新規登録の数, そのうち今は登録がない割合)"""
    first, last = periods[0], periods[-1]
    H = HIST[c]
    a = [no for no, v in H[first].items() if keep(*v)]
    a_keep = sum(1 for no in a if no in H[last]) / len(a) if a else None
    seen = set(H[first])
    born = []
    for p in periods[1:-2]:  # 最後の1年に登録された分は、まだ早いので入れない
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
    "63": dict(cap=10, util=0.827, perday=15786, base=10.72, days=266, other=780, rent=240, open=900, price="unit_price",
               src="2024年度 児童発達支援の経営状況（利用率82.7%、1人1日15,786円、営業日266日、利益率8.5%、赤字の事業所28.4%）"),
    "46": dict(cap=20, util=0.817, perday=9126, base=10.68, days=260, other=930, rent=300, open=1100, price="unit_price_b",
               src="2024年度 就労継続支援B型の経営状況（利用率81.7%、1人1日9,126円、利益率9.1%、赤字の事業所30.4%、平均工賃月23,583円）"),
}


def staff_plan(c, w, direct):
    """職種ごとに1年の人件費を積み上げる（万円、社会保険料の会社負担を含む）。
    w は地域の賃金の水準（全国＝1）。戻り値は合計と、(職種, 常勤換算の人数, 給料の合計) の行。"""
    lead = PAY_SAB * 12 * RAISE * w / 1e4
    ft = PAY_FT[c] * 12 * RAISE * w / 1e4
    pt = PAY_PT[c] * FTE_H * RAISE * w / 1e4
    per = ft * FT_RATE[c] + pt * (1 - FT_RATE[c])
    rows = [(LEAD[c], 1, lead), ("直接支援の職員", direct, per * direct)]
    if c in DIRECT_OTHER:
        rows.append(("目標工賃達成指導員・送迎・事務など", DIRECT_OTHER[c], per * DIRECT_OTHER[c]))
    return round(sum(r[2] for r in rows) * (1 + SOCIAL)), rows, per


def staff_html(rows, total, w):
    """人件費の内訳の表。"""
    body = "".join(f'<tr><td>{E(n)}</td><td>{f:.1f}人</td><td>{v:,.0f}万円</td></tr>' for n, f, v in rows)
    sub = sum(v for _, _, v in rows)
    return ('<table class="small"><tr><th>職種</th><th>常勤換算</th><th>年間給与</th></tr>' + body
            + f'<tr><td>社会保険料などの事業主負担（給与の{SOCIAL * 100:.1f}%）</td><td></td><td>{sub * SOCIAL:,.0f}万円</td></tr>'
            f'<tr><th>人件費の合計</th><th></th><th>{total:,}万円</th></tr></table>'
            f'<p class="note">給与は、厚生労働省「令和6年度 障害福祉サービス等従事者処遇状況等調査」の職種別・サービス別の平均（2024年9月、全国、賞与を含む）に、'
            f'2026年度までの賃上げ（年3%×2年）と地域の給与水準（介護職員の年収の全国比 {w:.2f}倍）を乗じて算出した。'
            '直接支援の職員の常勤・非常勤（時給）の比率は、令和7年 障害福祉サービス等経営概況調査の全国平均に合わせた。</p>')


def gh_model(houses, mix, util, pr, w, w_chiba, rent_house):
    """グループホーム（介護サービス包括型）の1年の収支を積み上げる（万円）。
    売上＝基本報酬＋夜間支援等体制加算(Ⅰ)、それに処遇改善加算(Ⅰ)＋入居者が払う家賃。
    人件費＝管理者兼サービス管理責任者＋毎晩の夜勤＋足りない分の世話人・生活支援員。"""
    cap = GH_HOUSE * houses
    res = cap * util
    base = sum(v * GH_UNITS[k] for k, v in mix.items()) * res * 365
    night = houses * sum(v * GH_NIGHT[k] for k, v in mix.items()) * 365
    fee = (base + night) * pr / 1e4 * (1 + GH_SHOGU)
    rent_in = rent_house * houses * util
    lead = PAY_SAB * 12 * RAISE * w / 1e4 * (1 if cap * util <= 30 else 2)
    ft = PAY_FT["33"] * 12 * RAISE * w / 1e4
    pt = PAY_PT["33"] * FTE_H * RAISE * w / 1e4
    per = ft * FT_RATE["33"] + pt * (1 - FT_RATE["33"])
    shifts = 365 * houses * NIGHT_SHIFT * w / w_chiba
    need = res / 6 + sum(v * res / GH_SUP[k] for k, v in mix.items() if k in GH_SUP)  # 世話人6:1＋生活支援員（常勤換算）
    by_shift = houses * SHIFT_DAY * 365 / FTE_H  # 夜勤の人が夕方・朝に世話人・生活支援員として働く分
    n_lead = 1 if res <= 30 else 2
    spare = max(0.0, n_lead - res / 30)  # サービス管理責任者は入居者30人に1人。残りの時間は世話人として働く
    extra = max(0.0, need - by_shift - spare)
    rows = [(f"管理者兼サービス管理責任者（うち{spare:.1f}人分は世話人として勤務）", n_lead, lead), (f"夜勤（毎晩{houses}人、1回{NIGHT_SHIFT * w / w_chiba:.1f}万円）", round(by_shift, 1), shifts),
            ("世話人・生活支援員（夜勤で足りない分）", round(extra, 1), extra * per)]
    st = round(sum(r[2] for r in rows) * (1 + SOCIAL))
    ct = st + round(GH_OTHER * cap) + round(rent_house * houses)
    rv = fee + rent_in
    return dict(cap=cap, util=util, days=365, rv=rv, fee=fee, st=st, ct=ct, rent=round(rent_house * houses), bal=rv - ct,
                pd=rv * 1e4 / (cap * 365 * util), be=ct / (rv / util), open=GH_OPEN * houses, pr=pr, rows=rows, per=per,
                need=need, by_shift=by_shift, spare=spare, houses=houses, units=base / (res * 365), night_u=night / (houses * 365),
                lead=lead, shifts=shifts, night_fee=night * pr / 1e4 * (1 + GH_SHOGU))


def gh_day_model(util, pr, w, rent_house):
    """グループホーム（日中サービス支援型）の1年の収支（万円）。売上は報酬の単位から、職員は経営概況調査の全国平均の配置から。"""
    cap = GHD_CAP
    res = cap * util
    units = sum(v * GHD_UNITS[k] for k, v in GHD_MIX.items())
    fee = units * res * 365 * pr / 1e4 * (1 + GH_SHOGU)
    houses = cap // GH_HOUSE
    rent_in = rent_house * houses * util
    lead = PAY_SAB * 12 * RAISE * w / 1e4
    ft = PAY_FT["33"] * 12 * RAISE * w / 1e4
    pt = PAY_PT["33"] * FTE_H * RAISE * w / 1e4
    per = ft * FT_RATE["33"] + pt * (1 - FT_RATE["33"])
    direct = GHD_DIRECT * cap
    rows = [("管理者兼サービス管理責任者", 1, lead), ("生活支援員・世話人・夜勤者（日中の常駐と毎晩の夜勤を含む）", round(direct, 1), direct * per)]
    st = round(sum(r[2] for r in rows) * (1 + SOCIAL))
    ct = st + round(GHD_OTHER * cap) + round(rent_house * houses)
    rv = fee + rent_in
    return dict(cap=cap, util=util, days=365, rv=rv, fee=fee, st=st, ct=ct, rent=round(rent_house * houses), bal=rv - ct,
                pd=rv * 1e4 / (cap * 365 * util), be=ct / (rv / util), open=GHD_OPEN, pr=pr, rows=rows, houses=houses, units=units, direct=direct)


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


def rivals(S, periods, c, x, y, r):
    """(距離, 事業所番号, 事業所, 初めて登録された時点) を近い順に。r km 以内だけ。"""
    latest, out = periods[-1], []
    for no, o in S[c][latest]["offices"].items():
        for hs in o["houses"]:
            try:
                hy, hx = float(hs[3]), float(hs[4])
            except (ValueError, IndexError):
                continue
            if abs(hy - y) < r / 110 and abs(hx - x) < r / 80 and km(x, y, hx, hy) <= r:
                since = next((p for p in periods if no in HIST[c].get(p, {})), periods[-1])
                out.append((km(x, y, hx, hy), no, o, since))
                break
    return sorted(out, key=lambda t: t[0])


def weekend(closed):
    """休みの書き方から、(土曜に開いている, 日曜に開いている)。書いていなければ None。"""
    cl_ = (closed or "").strip()
    if not cl_:
        return None, None
    return "土" not in cl_, not re.search(r"日曜|土日|(^|[、・,，\s/])日($|[、・,，\s/祝])", cl_)


def keypts(items):
    return '<div class="keyp"><b>この章のポイント</b><ul>' + "".join(f"<li>{x}</li>" for x in items) + "</ul></div>"


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
    """5歳きざみの人数から、サービスを使う年齢の子どもの数のおよそを出す。"""
    a0, a5, a10, a15 = t
    return a5 * 0.8 + a10 + a15 * 0.6 if c == "65" else a0 + a5 * 0.2  # 放課後等デイ6〜17歳、児発0〜5歳


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
        return 0, "市が新しい指定を止めている"
    if r["level"] == "watch":
        return 0.85, "市の計画の量に届くと断られることがある"
    if "wards" in r and cl is not None:
        w = ward_of(cl)
        n = r["wards"].get(w, 0)
        if not n:
            return 0, f"{w or 'この区'}は募集なし（総量規制）"
        return 0.7, f"{w}の募集枠は{n}か所（総量規制、選ばれる必要あり）"
    return 0.6, "市の募集で選ばれる必要がある（総量規制）"


def city_counts(S, c, p, pi, city):
    if p not in S[c]:
        return None
    return sum(v for mk, v in S[c][p]["muni"].items() if in_city(mk, pi, city))


def build_one(ctx, pi, city, geo_dir, ranks, ed="child"):
    periods, latest, prev, S, codes, rows = ctx
    ED = EDITIONS[ed]
    SV = ED["svcs"]
    MSG.clear()
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

    # 新しく開設された・なくなった（前の時点と比べて）
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
    if (pi, city) not in LABELS:
        LABELS[(pi, city)] = map_labels(geo_dir, pi, city, rings)
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
    wage_r = pf["care"] / nf["care"]  # 介護職員の年収の、全国との比較（地域の福祉の給料の水準）
    wage_chiba = chiba["care"] / nf["care"]
    rent_r = cf["rent"] / FACTS["city"]["船橋市"]["rent"]  # 家賃の、船橋市との比較
    rent = round(RENT * rent_r)
    staff, staff_rows, staff_per = staff_plan("65", wage_r, DIRECT["65"])
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


    # 狙い目の場所・サービス
    caps_open = cf.get("open", {}).get("caps", {})
    svc_view = {}
    for c in MAIN:
        a, b, _ = comp[c]
        ratio = a / b if b else 1
        g = (now[c] - ser[c][0]) / ser[c][0] if ser[c][0] else None
        if ratio < 0.85:
            verdict = "少ない（有望）" if (g or 0) > 0.2 else "少ない"
        elif ratio > 1.2:
            verdict = "多い（競争が激しい）"
        else:
            verdict = "普通"
        rule = caps_open.get(c)
        if rule:
            verdict = {"stop": "新規指定の停止中", "limit": "公募で選ばれれば開設可（総量規制）",
                       "watch": "計画量によっては指定されない"}[rule["level"]]
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
        st, rows_, _ = staff_plan(c, wage_r, DIRECT[c])
        ct = st + m["other"] - m["rent"] + round(m["rent"] * rent_r)
        rv = m["cap"] * m["days"] * m["util"] * pd / 1e4
        oth[c] = dict(m, pr=pr, pd=pd, st=st, ct=ct, rv=rv, bal=rv - ct, be=ct / (m["cap"] * m["days"] * pd / 1e4), rows=rows_)
    gh_pr = FACTS["unit_price_gh"][grade]
    gh_rent_h = round(GH_RENT * cf["rent"] / nf["rent"])
    gh = gh_model(GH_HOUSES, GH_MIX[1][1], GH_UTIL, gh_pr, wage_r, wage_chiba, gh_rent_h)
    ghd = gh_day_model(GH_UTIL, gh_pr, wage_r, gh_rent_h)
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
        f"{lp}の時点で、{city}にある障害福祉の事業所は合わせて<b>{total[-1]:,}件</b>である。{l0}の{total[0]:,}件から"
        f"<b>{(total[-1] - total[0]) / total[0] * 100:+.0f}%</b>{'増えた' if total[-1] >= total[0] else '減った'}。",
    ] + [
        f"{lname(c)}は{ser[c][0]:,}件から<b>{now[c]:,}件</b>に増えた（{(now[c] - ser[c][0]) / ser[c][0] * 100:+.0f}%）。"
        f"人口1万人あたりでは{pref}の平均の<b>{comp[c][0] / comp[c][1]:.2f}倍</b>である。"
        for c in SV if ser[c][0] and comp[c][1]
    ] + [
        f"{short(ED['main'])}から1km以上離れている場所は、市の面積の<b>約{maps[ED['main']]['far']:.0f}%</b>、"
        f"住宅地の目安とした場所に限ると<b>約{maps[ED['main']]['live_far']:.0f}%</b>である。",
        f"{lq}から{lp}までの半年で、障害福祉の事業所は<b>{n_new:,}件</b>が新規に登録され、<b>{n_gone:,}件</b>の登録がなくなった。",
    ]
    if util and child:
        pts.append(f"放課後等デイの利用率（定員がどれくらい埋まっているか）の目安は<b>約{min(util, 9.99) * 100:.0f}%</b>である"
                   f"（{fy_label(hd['fy'])}の市の数字から計算。全国の事業所の平均は89%）。")
    pts.append(f"定員{est['cap']}人の{lname(ED['main'])}を開設すると、平均的な事業所並みに運営できた場合の収支は<b>年{est['bal']:+,.0f}万円</b>、"
               f"黒字になる{'入居率' if ed == 'gh' else '利用率'}は<b>約{est['be'] * 100:.0f}%</b>である（{grade_txt}で試算）。")
    if cands:
        sc, c, cl, cs, _ = cands[0]
        pts.insert(0, f"<b>結論：開設するなら、{E('・'.join(short(x) for x in cs))}を{E('・'.join(cl['towns'][:2]))}のあたりで</b>が第一候補である。"
                      + (f"通える範囲の{DEM_WHO[c]}の数に比べて{E(short(c))}の受け皿が少なく、新しく開設しても{'1か所' if c == '33' else '1席'}あたりの需要が市の平均の約{cl['idx']:.1f}倍ある"
                         if "idx" in cl else f"近くに{E(short(c))}が少なく（最も近いところまで平均{cl['avgd']:.1f}km）、人の住む地域である")
                      + "（詳しくは「候補地の評価」の章）。")
    for c_, r_ in caps_open.items():
        if c_ in SV or (child and c_ in ("65", "63")):
            pts.append(f"<b>注意（{E(short(c_))}）：</b>{E(r_['text'])}")
    if ed == "gh":
        pts.append("<b>注意：</b>グループホームは、2027年4月1日から、市の計画の量を超えると新しい指定をしないこと（総量規制）ができるようになる。")
    if "65" in dem and child:
        d_ = dem["65"]
        pts.append(f"放課後等デイを使う年齢（6〜17歳）の子どもは、2025年から2035年に<b>{(d_['k35'] / d_['k25'] - 1) * 100:+.0f}%</b>"
                   f"{'減る' if d_['k35'] < d_['k25'] else '増える'}見込みである（社人研の推計）。")
    pts.append(f"{E(city)}の事業所のうち<b>約{chain_share * 100:.0f}%</b>は、全国で{CHAIN_MIN}か所以上を運営する大手の法人である"
               f"（半年で新規に登録された事業所では約{new_chain_share * 100:.0f}%）。")
    if zero and False:
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

    # 1枚で分かるまとめ
    def tile(label, val, sub=""):
        return f'<div class="tile"><p class="tl">{label}</p><p class="tv">{val}</p><p class="ts">{sub}</p></div>'
    mc = ED["main"]
    tiles = [
        tile("障害福祉サービス事業所", f"{total[-1]:,}件", f"{l0}比 {(total[-1] - total[0]) / total[0] * 100:+.0f}%"),
        tile(f"{short(mc)}の事業所", f"{now[mc]:,}件", f"人口当たり県平均の{svc_view[mc][0]:.2f}倍／2021年比 {svc_view[mc][1] * 100:+.0f}%" if svc_view[mc][1] is not None else ""),
        tile(f"年間収支（定員{est['cap']}人）", f"{est['bal']:+,.0f}万円", f"損益分岐の{'入居率' if ed == 'gh' else '利用率'} 約{est['be'] * 100:.0f}%"),
        tile("必要資金", f"約{-est['low']:,.0f}万円", f"ROI 約{est['bal'] / -est['low'] * 100:.0f}%／回収 約{est['back'] // 12}年{est['back'] % 12}か月" if est["back"] else "赤字のため回収できない"),
        tile("有効求人倍率（全職種）", f"{pf['all']:.2f}倍", f"{E(pref)}（全国 {nf['all']:.2f}倍）"),
        tile("借家の平均家賃", f"月{cf['rent'] / 1e4:.1f}万円", f"全国 月{nf['rent'] / 1e4:.1f}万円（住宅・土地統計調査）"),
    ] if not child else [
        tile("障害福祉サービス事業所", f"{total[-1]:,}件", f"{l0}比 {(total[-1] - total[0]) / total[0] * 100:+.0f}%"),
        tile("放課後等デイの利用率", f"約{util * 100:.0f}%" if util else "—", ("定員を上回る計算（利用率の章を参照）" if util > 1.2 else "全国平均 89%") if util else "市の公表値なし（全国平均89%で試算）"),
        tile("年間収支（放課後等デイ 定員10人）", f"{sim_year(*per['標準'], cost):+,.0f}万円", f"損益分岐の利用率 約{be * 100:.0f}%"),
        tile("必要資金", f"約{-low:,.0f}万円", f"ROI 約{sim_year(*per['標準'], cost) / -low * 100:.0f}%／回収 約{back // 12}年{back % 12}か月" if back else "赤字のため回収できない"),
        tile("保育士の有効求人倍率", f"{pf['hoiku']:.2f}倍", f"{E(pref)}（全国 {nf['hoiku']:.2f}倍）"),
        tile("6〜17歳人口（2035年）", f"{(dem['65']['k35'] / dem['65']['k25'] - 1) * 100:+.0f}%" if "65" in dem else "—", "2025年比（社人研推計）"),
    ]
    sig = []
    for c in SV:
        ratio, g, half, verdict = svc_view[c]
        bal = sim_year(*per["標準"], cost) if c == "65" else gh["bal"] if c == "33" else oth[c]["bal"]
        rule = caps_open.get(c)
        col = "red" if (rule and rule["level"] == "stop") else "yellow" if rule or "激しい" in verdict else "green" if "少ない" in verdict else "plain"
        sig.append(f'<tr><th><span class="dot {col}"></span>{E(short(c))}</th><td style="text-align:left">{E(verdict)}</td>'
                   f'<td>{ratio:.2f}倍</td><td>年{bal:+,.0f}万円</td></tr>')
    first = (f'{E(city)}は、<b>{E("・".join(cands[0][2]["towns"][:2]))}</b>周辺での{E("・".join(short(x) for x in cands[0][3]))}の開設が最適と考える' if cands
             else f'{E(city)}では、立地よりもサービス内容での差別化を優先すべきと考える')
    h.append('<section class="pb"><h2>§結論</h2>'
             f'<p class="lead">結論：{first}</p><div class="tiles">' + "".join(tiles) + '</div>'
             '<h3>サービス別の評価</h3><table class="small sig"><tr><th>サービス</th><th>評価</th><th>人口当たり（県平均比）</th><th>年間収支（標準ケース）</th></tr>'
             + "".join(sig) + '</table><p class="note">色の意味は、緑が「事業所が少なく有望」、黄が「競争が激しい、または市の規制に注意が必要」、赤が「市が新規指定を停止中」である。'
             + (f'収支は、定員{"10人" if ed == "child" else "20人"}で開設し、全国平均並みに運営した場合の年間の試算である。</p>' if ed != "gh" else
              f'収支は、定員{GH_HOUSE}人の住居を{GH_HOUSES}か所（介護サービス包括型）開設し、毎晩夜勤者を配置した場合の年間の試算である。</p>')
             + '</section>')
    h.append('<!--EXEC-->')

    h.append('<section class="pb"><h2>§要点</h2><ol class="points">' + "".join(f"<li>{x}</li>" for x in pts) + "</ol>")
    h.append(f'<h3>事業所の数</h3><table><tr><th>サービス</th><th>{E(l0)}</th><th>{E(lp)}</th><th>増えた数</th>'
             f'<th>人口1万人あたり</th><th>主要{len(TARGETS)}市区の中で</th></tr>')
    for c in SV:
        a, f = now[c], ser[c][0]
        h.append(f'<tr><th>{E(short(c))}</th><td>{f if f is not None else "—"}</td><td>{a:,}</td>'
                 f'<td>{(a - f) if f is not None else "—":+}</td><td>{comp[c][0]:.2f}</td><td>{ranks[c].get((pi, city), "—")}位</td></tr>'
                 if f is not None else
                 f'<tr><th>{E(short(c))}</th><td>—</td><td>{a:,}</td><td>—</td><td>{comp[c][0]:.2f}</td><td>{ranks[c].get((pi, city), "—")}位</td></tr>')
    h.append(f'</table><p class="note">「主要{len(TARGETS)}市区の中で」は、人口1万人あたりの数が多い順の順位である（1位が最も多い）。</p></section>')


    # 結論
    h.append('<section class="pb"><h2>§候補地の評価</h2>'
             + ('<p>町丁ごとに、商圏内に住む対象年齢の人口（<b>需要</b>）と、同じサービスの定員（<b>供給</b>）を比較した。</p>'
                '<p><b>需給指数</b>は、新たに開設した場合に、定員1人分当たりの需要が市平均の何倍になるかを示す。'
                '市平均を1.0とし、値が大きいほど利用者を集めやすい。</p>'
                '<p>算出には、医療・福祉施設の立地研究で広く用いられるE2SFCA法とハフモデルを使った。方法と出典は章末に記載した。</p>' if SITE else
                '<p>空白地域の地図（後の章）、人口当たりの事業所数、利用率を組み合わせて、開設候補地を点数化した。'
                '点数が高いほど、近隣に同じサービスが少なく、住宅が多く、競争が緩やかな場所である。</p>'))
    if cands:
        c0_, cl0_ = cands[0][1], cands[0][2]
        h.append(keypts(([] if "idx" in cl0_ else [f'第一候補は<b>{E(cl0_["towns"][0])}周辺</b>での{E(short(c0_))}の開設である。']) + [
                         '点数は、学術研究で用いられる方法（ハフモデル・E2SFCA法）で、町丁ごとの需要と供給の差を数値化したものである。',
                         '点数は現地調査の優先順位を示す目安であり、最終判断には現地での確認が欠かせない（「現地確認チェックリスト」の章）。']))
        h.append('<h3>有望な候補地（上位5か所）</h3><table class="small rank"><tr><th>順位</th><th>サービス</th><th>場所</th><th>点数</th><th>理由</th></tr>')
        for j, (sc, c, cl, cs, note) in enumerate(cands, 1):
            if "idx" in cl:
                sm = SITE[c]
                unit = "か所" if c == "33" else "人分"
                need = cl["use_r"]
                uu = "か所" if c == "33" else "人分"
                why = (f"<b>需要：</b>半径{sm['R']:.0f}kmに{DEM_WHO[c]}約{cl['dem']:,.0f}人<br>"
                       f"<b>供給：</b>{E(short(c))}{cl['comp']}か所" + (f"（定員{cl['seats']:,.0f}人）" if c != "33" else "")
                       + (f"。約{cl['short']:,.0f}{uu}不足" if cl["short"] >= 0.5 else "。現状の供給で充足") + "<br>"
                       f"<b>需給指数：</b>新規開設時の{'1か所' if c == '33' else '定員1人分'}当たりの需要は市平均の<b>{cl['idx']:.2f}倍</b><br>")
                if cl["born"] >= 3:
                    why += f"<b>継続状況：</b>2022年以降に開設された{cl['born']}か所のうち{cl['born'] - cl['gone']}か所が継続"
                elif cl["born"]:
                    why += f"<b>継続状況：</b>2022年以降の開設は{cl['born']}か所"
                else:
                    why += "<b>継続状況：</b>近年の開設なし（需要は未検証）"
            else:
                why = (f"最寄りの{E(short(c))}まで平均{cl['avgd']:.1f}km（最大{cl['md']:.1f}km）。"
                       f"半径2km以内の{E(short(c))}は{cl['comp']}か所" + (f"（定員計{cl['compcap']}人）" if cl['compcap'] else "") + "。"
                       f"他の福祉事業所が{cl['inner']}か所ある住宅地。面積約{cl['a']:.1f}km²。")
            if len(cs) > 1:
                why += f"<br>{'・'.join(short(x) for x in cs[1:])}も同様に少ない"
            if note:
                why += f"<br>【注意】{E(note)}"
            h.append(f'<tr><th>{j}</th><td>{E("・".join(short(x) for x in cs))}</td><td style="white-space:nowrap">{E("・".join(cl["towns"]))}<br>周辺</td>'
                     f'<td><b>{sc / top_sc * 100:.0f}</b></td><td style="text-align:left">{why}</td></tr>')
        h.append('</table>')
        for c_, sm in SITE.items():
            top = sm["areas"][:10]
            h.append(f'<h3>{E(short(c_))}：需給指数の高い地区（上位10）</h3>'
                     + bars_h([(a["towns"][0], a["idx"], j < 3) for j, a in enumerate(top)], unit="倍", fmt="{:.2f}")
                     + f'<p class="note">市全体では、{DEM_WHO[c_]}約{sm["d_city"]:,.0f}人に対して、{E(short(c_))}の供給は'
                     + (f'{sm["s_city"]:,.0f}か所' if c_ == "33" else f'定員{sm["s_city"]:,.0f}人分')
                     + f'（{"1か所" if c_ == "33" else "定員1人分"}当たり約{sm["base"]:,.0f}人）である。これを1.0とし、半径{sm["R"]:.0f}kmまでの距離で重み付けして比較した。'
                     + (f'市内で2022年以降に開設された{E(short(c_))}{sm["n_born"]}か所のうち、廃止・休止等は{sm["g_city"] * 100:.0f}%である。'
                        'この割合を上回る地区は減点した。' if sm["n_born"] else '') + '</p>')
        if SITE:
            h.append('<div class="method"><b>点数の算出方法と限界</b><ul>'
                     '<li><b>基礎とした研究：</b>地域の需要を周辺の事業所が分け合うという考え方は、ハフ（1963年）の小売商圏の研究に始まる。'
                     'その後、医療機関へのアクセスを測る2SFCA法（Luo and Wang 2003年）と、距離による重み付けを加えた改良版（E2SFCA、Luo and Qi 2009年）が開発され、'
                     '病院・保育所・高齢者施設の配置研究で世界的に用いられている。本レポートでは、E2SFCA法と同じガウス型の距離減衰（近いほど重く、'
                     f'{"・".join(sorted({f"{v:.0f}km" for v in (RADIUS[k] for k in SITE)}))}で0）を用い、新規事業所が「定員×距離の重み」の比率で需要を獲得すると仮定した。</li>'
                     '<li><b>計算手順：</b>①町丁ごとに、周辺事業所の「定員×距離の重み」を合計する。②候補地に新規の定員を置き、周辺の町丁の需要のうち新規事業所の獲得分を合算する。'
                     '③獲得見込みを新規の定員で割り、市全体の「需要÷定員」を1.0として指数化する。④近隣で最近開設された事業所の廃止・休止率が市平均を上回る場合は減点する。</li>'
                     '<li><b>需要：</b>国勢調査の町丁別・年齢別人口を用いた。町丁別の数値は令和2年（2020年）調査が最新のため、町丁ごとの割合は令和2年、市区町村の年齢別の合計は令和7年（2025年）調査に合わせて補正した（年齢不詳は年齢別に按分）。障害のある人の割合は地域で異なるが、町丁別の数値は公表されていないため、市内では一律とした。</li>'
                     + ('<li><b>グループホーム：</b>公表データに定員がないため、供給は事業所数で数えた。また、住まいであるため市外や遠方からの入居も多く、他のサービスに比べて距離の影響は小さい。</li>' if "33" in SITE else '')
                     + '<li><b>供給：</b>WAM NETに登録された事業所の定員で、市外の近隣事業所も含む。一方、市外に住む人の需要は含まないため、市境に近い地区は低めに出る。</li>'
                     '<li><b>限界：</b>実際の利用は、送迎範囲、事業所の評判、相談支援専門員や学校からの紹介によっても大きく変わる。'
                     '点数は現地調査の優先順位として用い、最終的には現地を歩き、近隣事業所の空き状況や相談支援事業所の意見を確認したうえで判断する必要がある。</li></ul></div>')
        if child:
            h.append('<h3>候補地周辺の交通と学校</h3><table class="small txt2"><tr><th>順位</th><th>最寄り駅</th><th>小学校</th><th>最寄りの特別支援学校</th></tr>')
        else:
            h.append('<h3>候補地周辺の交通</h3><table class="small txt2"><tr><th>順位</th><th>最寄り駅</th><th>1km以内の駅</th><th>2km以内の同じサービス</th></tr>')
        for j, (sc, c, cl, cs, note) in enumerate(cands, 1):
            st = nearby(STATIONS, cl["x"], cl["y"], 5)
            sta = f'{E(st[0][1][2])}駅（{E(st[0][1][3])}）約{st[0][0]:.1f}km' if st else "5km以内になし"
            if child:
                el = nearby(SCHOOLS["小学校"], cl["x"], cl["y"], 2)
                sp = nearby(SCHOOLS["特別支援学校"], cl["x"], cl["y"], 15)
                h.append(f'<tr><td>{j}</td><td>{sta}</td>'
                         f'<td>{f"2km以内に{len(el)}校（最寄り約{el[0][0]:.1f}km）" if el else "2km以内になし"}</td>'
                         f'<td>{E(sp[0][1][2]) + f" 約{sp[0][0]:.1f}km" if sp else "15km以内になし"}</td></tr>')
            else:
                n1 = len({q[1][2] for q in st if q[0] <= 1})
                h.append(f'<tr><td>{j}</td><td>{sta}</td><td>{n1}駅</td><td>{cl["comp"]}か所</td></tr>')
        h.append('</table><p class="note">距離は候補地の中心からの直線距離である。'
                 + ('放課後等デイは毎日学校へ迎えに行くため、小学校・特別支援学校から車で10〜15分（約3〜5km）以内であれば送迎を組みやすい。' if child else
                    '就労B型は電車・バスで通う利用者が多いため、駅から徒歩圏（1km以内）であれば利用者を集めやすい。' if ed == "b" else
                    'グループホームは住まいであるため、駅や買い物の利便性、日中活動先（生活介護・就労B型など）への通いやすさが選ばれる理由になる。')
                 + '学校と駅の位置：国土交通省「国土数値情報（学校 2021年、鉄道 2023年）」。</p>')
    else:
        h.append('<p>住宅地の中に、同じサービスから1km以上離れた大きな空白地域は見られなかった。立地よりも、サービス内容で差別化を図る必要がある地域である（「差別化のポイント」の章）。</p>')
    if blocked:
        bl = collections.OrderedDict()
        for c, cl, note in blocked:
            bl.setdefault((short(c), note), []).append("・".join(cl["towns"][:2]))
        h.append('<p class="contact"><b>候補から除外した場所：</b>' + "".join(
            f'{E(s_)}の{E("、".join(v[:3]))}周辺（{E(n_)}）。' for (s_, n_), v in bl.items()) + 'いずれも空白地域だが、市の規制により新規開設ができない。</p>')
    h.append('<h3>サービス別の評価</h3><table class="small"><tr><th>サービス</th><th>人口当たり（県平均比）</th><th>2021年比の増加率</th><th>直近半年の増減</th><th>評価</th></tr>')
    for c in SV:
        ratio, g, half, verdict = svc_view[c]
        h.append(f'<tr><th>{E(short(c))}</th><td>{ratio:.2f}倍</td><td>{f"{g * 100:+.0f}%" if g is not None else "—"}</td><td>{half:+d}</td>'
                 f'<td style="text-align:left">{"<b>" + verdict + "</b>" if "有望" in verdict else verdict}</td></tr>')
    h.append('</table><p class="note">' + ('「有望な候補地」の点数は、需給指数に、近隣の新規事業所の継続状況と市の規制（総量規制）を加味したものである（1位＝100）。'
             if SITE else '点数は、空白地域の広さ、住宅の多さ（他の福祉事業所の数）、最寄り事業所までの距離、半径2km以内の同じサービスの数、'
             '県平均との比較、利用率（放課後等デイのみ）から算出した相対的な目安である（1位＝100）。')
             + 'この表の「評価」は市全体の事業所数の多寡によるもので、地区ごとの差は上の表に示した。物件を探す際は、駅や学校からの距離、送迎のしやすさも併せて確認する必要がある。</p></section>')

    # 候補地の周辺の同じサービス（競合）
    if cands:
        comp_h, kp = [], []
        for j, (sc, c, cl, cs, note) in enumerate(cands[:3], 1):
            R = RADIUS.get(c, 2.0)
            rv = rivals(S, periods, c, cl["x"], cl["y"], R)
            if not rv:
                comp_h.append(f'<h3>{j}位 {E(cl["towns"][0])}周辺（{E(short(c))}）</h3><p>半径{R:.0f}km以内に{E(short(c))}はない。</p>')
                continue
            wk = [weekend(o["closed"]) for _, _, o, _ in rv]
            sat = sum(1 for a, _ in wk if a) / max(1, sum(1 for a, _ in wk if a is not None))
            sun = sum(1 for _, b in wk if b) / max(1, sum(1 for _, b in wk if b is not None))
            capv = [int(o["cap"]) for _, _, o, _ in rv if str(o["cap"]).isdigit()]
            new_n = sum(1 for *_, since in rv if since != periods[0])
            big = sum(1 for _, _, o, _ in rv if CORP_N[o["corp_key"] or o["corp"]] >= CHAIN_MIN)
            live = c == "33"  # 住む場所なので、曜日・時間はない
            if j == 1:
                MSG["候補地周辺の競合"] = (f'第一候補の{E(cl["towns"][0])}周辺には競合が{len(rv)}か所あり、うち{new_n}か所は{l0}以降の新規参入である。'
                                                   + ("競争は激しくなっている" if new_n >= len(rv) / 2 else "競合の顔ぶれは比較的安定している"))
            kp.append(f'<b>{j}位 {E(cl["towns"][0])}周辺：</b>半径{R:.0f}km以内に{len(rv)}か所（うち{l0}以降の新規参入{new_n}か所、大手{big}か所）。'
                      + ('' if live else f'土曜営業は{sat * 100:.0f}%、日曜営業は{sun * 100:.0f}%。'))
            trs = []
            for d, no, o, since in rv[:10]:
                a, b = weekend(o["closed"])
                yn = {True: "○", False: "休", None: "—"}
                trs.append(f'<tr><td style="text-align:left">{E(o["name"][:30])}</td><td style="text-align:left">{E(o["corp"][:22])}</td><td>{d:.1f}km</td>'
                           + ('' if live else f'<td>{E(str(o["cap"])) if str(o["cap"]).isdigit() else "—"}</td>')
                           + ('' if live else f'<td>{yn[a]}</td><td>{yn[b]}</td><td>{E(o["hours"][:13]) or "—"}</td>')
                           + f'<td>{"〜" + E(l0) if since == periods[0] else E(B.label(since))}</td></tr>')
            comp_h.append(f'<h3>{j}位 {E(cl["towns"][0])}周辺：半径{R:.0f}km以内の{E(short(c))}（{len(rv)}か所、近い順）</h3>'
                          '<table class="small rv"><tr><th>事業所</th><th>運営法人</th><th>距離</th>'
                          + ('' if live else '<th>定員</th><th>土曜</th><th>日曜</th><th>営業時間</th>') + '<th>登録時期</th></tr>'
                          + "".join(trs) + '</table>'
                          + (f'<p class="note">このほかに{len(rv) - 10}か所ある。' if len(rv) > 10 else '<p class="note">')
                          + (f'平均定員は{sum(capv) / len(capv):.1f}人。' if capv else '') + '</p>')
        h.append('<section class="pb"><h2>§候補地周辺の競合</h2>'
                 '<p>上位3候補地について、商圏内にある同じサービスの事業所を、WAM NETの公表データをもとに一覧にした。'
                 '登録時期の新しい事業所が多い地域ほど、近年競争が激しくなっていると考えられる。'
                 + ('グループホームは定員や入居状況が公表されていないため、周辺の事業所数と開設時期から判断する必要がある。</p>' if ed == "gh" else
                    '土日に営業する事業所が少なければ、そこに差別化の余地がある。</p>')
                 + keypts(kp) + "".join(comp_h)
                 + '<p class="note">事業所情報は、各事業所がWAM NETに届け出た内容による（' + E(lp) + '時点）。'
                 + ('' if ed == "gh" else '土曜・日曜の営業は定休日の記載から判定したもので、祝日や月ごとの営業日は反映していない。')
                 + '「大手」は全国で' + f'{CHAIN_MIN}' + 'か所以上の事業所を運営する法人とした。</p></section>')

    # 特別支援学校と周辺の放課後等デイ
    sps = [p_ for p_ in SCHOOLS["特別支援学校"] if p_[3] == pref and inside(rings, p_[0], p_[1])]
    if sps and child:
        r65 = rows["65"]
        tab = []
        for x, y, nm, _ in sps:
            nb = nearby(r65, x, y, 2)
            tab.append((len(nb), sum(q[1][5] for q in nb), nearby(r65, x, y, 8)[:1], nm, x, y))
        tab.sort(key=lambda t: (t[0], -(t[2][0][0] if t[2] else 99)))
        MSG["特別支援学校と周辺の放課後等デイ"] = (f'半径2km以内の放課後等デイが最も少ないのは{E(tab[0][3])}（{tab[0][0]}か所）。'
                                          '学校に近く競合が少ない場所は、送迎面で選ばれやすい')
        h.append(f'<section class="pb"><h2>§特別支援学校と周辺の放課後等デイ</h2><p>{E(city)}内の特別支援学校（{len(sps)}校）ごとに、'
                 '半径2km以内の放課後等デイの数を示した。放課後等デイの利用児の多くは特別支援学校・特別支援学級に通っており、毎日学校への迎えが発生する。'
                 'そのため、周辺に放課後等デイが少ない学校の近くは、利用者を集めやすい。</p>'
                 '<table class="small txt2"><tr><th>特別支援学校</th><th>2km以内の放課後等デイ</th><th>その定員</th><th>最寄りの放課後等デイ</th><th>所在地</th></tr>')
        for n_, cap_, nr, nm, x, y in tab:
            town = ""
            for _, rr_ in nearby([q for qs in rows.values() for q in qs], x, y, 0.6)[:5]:
                town = town_of(rr_[2], rr_[3], city)
                if town:
                    break
            h.append(f'<tr><td>{E(nm)}</td><td>{n_}か所</td><td>{cap_ if cap_ else "—"}{"人" if cap_ else ""}</td>'
                     f'<td>{f"約{nr[0][0]:.1f}km" if nr else "8km以内になし"}</td><td>{E(town) or "—"}</td></tr>')
        h.append('</table><p class="note">学校の位置：国土交通省「国土数値情報（学校）」2021年。名称に「特別支援学校」「支援学校」「養護学校」を含む学校を対象とした（分校・分教室を含む）。'
                 '特別支援学級（一般の小中学校内の学級）に通う子どもの利用も多いため、小学校の多い住宅地も併せて確認する必要がある。</p></section>')

    # ほかとの違い
    if ed != "gh":
        h.append('<section class="pb"><h2>§差別化のポイント</h2><p>同じサービスの事業所の営業日と営業時間を集計した。'
                 '他の事業所が営業していない曜日や時間帯に営業すれば、それ自体が選ばれる理由になる。</p>'
                 f'<table class="small"><tr><th>サービス</th><th>土曜営業</th><th>日曜営業</th><th>平日18時30分以降も営業</th><th>定員（中央値）</th></tr>')
        tips = []
        for c in (SV if child else ["46"]):
            (sat, sun, late, n_c, n_h), (nsat, nsun, nlate, _, _), capv = diff[c]

            def cell(v, nv):
                return f"{v * 100:.0f}%<small>（全国{nv * 100:.0f}%）</small>" if v is not None else "—"
            md = statistics.median(capv) if capv else None
            h.append(f'<tr><th>{E(short(c))}</th><td>{cell(sat, nsat)}</td><td>{cell(sun, nsun)}</td><td>{cell(late, nlate)}</td>'
                     f'<td>{f"{md:g}人" if md else "—"}</td></tr>')
            kid = c in ("65", "63")
            who = "共働き世帯" if kid else "平日に通いにくい人や、平日に別の仕事をしている人"
            if sat is not None and n_c >= 5:
                if sat < nsat - 0.05:
                    tips.append(f"{short(c)}の土曜営業は{sat * 100:.0f}%で、全国（{nsat * 100:.0f}%）を下回る。土曜に営業すれば、{who}に選ばれやすくなる。")
                if sun < nsun - 0.03:
                    tips.append(f"{short(c)}の日曜営業は{sun * 100:.0f}%で、全国（{nsun * 100:.0f}%）を下回る。日曜にも営業すれば、{who}に選ばれやすくなる。")
            if kid and late is not None and n_h >= 5 and late < nlate - 0.02:
                tips.append(f"{short(c)}で平日18時30分以降も営業している事業所は{late * 100:.0f}%で、全国（{nlate * 100:.0f}%）を下回る。夕方遅くまで預かれれば、働く保護者のニーズに応えられる。")
        h.append('</table>')
        if tips:
            h.append('<h3>差別化できる点</h3><ul>' + "".join(f"<li>{E(t)}</li>" for t in tips) + '</ul>')
        else:
            h.append(('<p>土日や夕方に営業する事業所は全国並みか、それ以上に多い。受け入れる子ども（重症心身障害児、医療的ケア児など）や支援プログラムの内容で差別化を図る必要がある。</p>' if child else '<p>土日に営業する事業所は全国並みか、それ以上に多い。作業内容（工賃水準、在宅利用、送迎の有無）で差別化を図る必要がある。</p>'))
        h.append('<p class="note">各事業所がWAM NETに届け出た「定休日」「平日の営業時間」をもとに集計した。記載のない事業所は除いた。'
                 + ('重症心身障害児や医療的ケア児の受け入れの有無は、このデータからは区別できない。</p></section>' if child else '工賃の金額はこのデータに含まれていない。</p></section>'))

    # 2. 推移
    h.append(f'<section class="pb"><h2>§事業所数の推移</h2><p>{l0}から{lp}までの事業所数を、半年ごとに示した。</p>')
    h.append(B.trend_chart(periods, total).replace('class="chart"', 'class="chart big"'))
    h.append('<div class="grid3">')
    for c in SV:
        h.append(f'<figure><figcaption>{E(short(c))}</figcaption>{line_chart(periods, ser[c])}</figure>')
    h.append('</div><p class="note">WAM NETに情報が登録されている事業所の数である。グラフ両端の数字は、最初と最新の時点の件数を示す。</p></section>')

    # 3. 人口あたり
    h.append(f'<section class="pb"><h2>§人口当たりの事業所数</h2><p>人口1万人当たりの事業所数を、{E(pref)}平均・全国平均と比較した。'
             '人口規模の異なる地域を同じ基準で比べるための指標である。</p>')
    h.append('<table><tr><th>サービス</th><th>' + E(city) + '</th><th>' + E(pref) + '</th><th>全国</th><th>県平均比</th></tr>')
    for c in SV:
        a, b, n = comp[c]
        h.append(f'<tr><th>{E(short(c))}</th><td>{a:.2f}</td><td>{b:.2f}</td><td>{n:.2f}</td><td>{a / b:.1f}倍</td></tr>' if b else "")
    h.append('</table><div class="grid2">')
    for c in SV:
        a, b, n = comp[c]
        h.append(f'<figure><figcaption>{E(short(c))}（人口1万人当たり）</figcaption>'
                 + bars_h([(city, a, True), (pref, b, False), ("全国", n, False)], fmt="{:.2f}") + '</figure>')
    h.append('</div></section>')

    # 4. 区ごと／近く
    if wards:
        h.append(f'<section class="pb"><h2>§区別の事業所数</h2><p>主なサービスの事業所数を区別に示した。かっこ内は人口1万人当たりの数である。</p><table class="small"><tr><th>区</th><th>人口</th>'
                 + "".join(f"<th>{E(short(c))}</th>" for c in SV) + "</tr>")
        for wd in wards:
            wp = B.POP_MUNI[pi].get(wd)
            cells = "".join(
                f'<td>{S[c][latest]["muni"].get((pi, wd), 0)}<small>（{rate(S[c][latest]["muni"].get((pi, wd), 0), wp):.1f}）</small></td>'
                for c in SV)
            h.append(f'<tr><th>{E(wd[len(city):])}</th><td>{wp:,}</td>{cells}</tr>' if wp else "")
        h.append('</table>')
        wv = [(wd[len(city):], rate(S[ED["main"]][latest]["muni"].get((pi, wd), 0), B.POP_MUNI[pi].get(wd)), False)
              for wd in wards if B.POP_MUNI[pi].get(wd)]
        h.append(f'<figure><figcaption>{E(lname(ED["main"]))}（区別、人口1万人当たり）</figcaption>'
                 + bars_h(sorted(wv, key=lambda x: -x[1]), fmt="{:.2f}") + '</figure></section>')
    else:
        h.append(f'<section class="pb"><h2>§近隣市区町村との比較</h2><p>{E(pref)}内で{E(city)}に近い市区町村と比較した。かっこ内は人口1万人当たりの数である。</p>'
                 '<table class="small"><tr><th>市区町村</th><th>人口</th>' + "".join(f"<th>{E(short(c))}</th>" for c in SV) + "</tr>")
        for k in [(pi, city)] + near:
            kp = B.POP_MUNI[pi].get(k[1])
            cells = "".join(f'<td>{unit_count(c, k)}<small>（{rate(unit_count(c, k), kp):.1f}）</small></td>' for c in SV)
            h.append(f'<tr{" class=me" if k == (pi, city) else ""}><th>{E(k[1])}</th><td>{kp:,}</td>{cells}</tr>' if kp else "")
        h.append('</table></section>')

    # 5. 新規登録
    h.append(f'<section class="pb"><h2>§直近半年の新規事業所</h2><p>{lq}時点では登録がなく、{lp}時点で登録されている事業所を示した（主なサービスのみ）。'
             '新たに開設された事業所のほか、届出の再提出で事業所番号が変わった事業所も含む。</p>')
    h.append('<table class="small"><tr><th>サービス</th><th>新規登録</th><th>登録抹消（廃止・休止等）</th></tr>'
             + "".join(f"<tr><th>{E(short(c))}</th><td>{len(newl[c])}</td><td>{len(gone[c])}</td></tr>" for c in SV)
             + f"<tr><th>障害福祉サービス全体</th><td>{n_new}</td><td>{n_gone}</td></tr></table>")
    for c in SV:
        if not newl[c]:
            continue
        h.append(f'<h3>{E(short(c))}（{len(newl[c])}件）</h3><table class="list"><tr><th>事業所</th><th>運営法人</th><th>所在地</th><th>定員</th></tr>')
        for o in sorted(newl[c], key=lambda o: o["name"]):
            place = next((v[1] for mk, v in o["munis"].items() if in_city(mk, pi, city)), "")
            ward = next((mk[1][len(city):] for mk in o["munis"] if in_city(mk, pi, city) and mk[1] != city), "")
            h.append(f'<tr><td>{E(o["name"])}</td><td>{E(o["corp"])}</td><td>{E(ward + re.split(r"[0-9０-９]", place)[0])}</td><td>{E(o["cap"] or "—")}</td></tr>')
        h.append("</table>")
    h.append('</section>')

    # 続いているか
    h.append(f'<section class="pb"><h2>§新規事業所の継続状況</h2><p>{E(city)}の事業所の継続状況を調べた。新規参入した事業所が撤退しやすい地域かどうかの目安になる。'
             f'{l0}時点の事業所のうち{lp}時点でも登録されている割合（継続率）と、'
             f'{B.label(periods[1])}〜{B.label(periods[-3])}に新規登録された事業所のうち{lp}時点で登録がない割合（廃止・休止率）を示した。</p>'
             '<table class="small"><tr><th>サービス</th><th>' + E(l0) + '時点の事業所</th><th>継続率</th><th>その後の新規登録</th>'
             '<th>新規の廃止・休止率</th><th>全国（新規の廃止・休止率）</th></tr>')
    sv_notes = []
    for c, ((na, ak, nb, bg), (_, nak, _, nbg)) in surv.items():
        if c not in SV:
            continue
        h.append(f'<tr><th>{E(short(c))}</th><td>{na:,}</td><td>{f"{ak * 100:.0f}%" if ak is not None else "—"}<small>（全国{nak * 100:.0f}%）</small></td>'
                 f'<td>{nb:,}</td><td><b>{f"{bg * 100:.0f}%" if bg is not None else "—"}</b></td><td>{nbg * 100:.0f}%</td></tr>')
        if bg is not None and nb >= 10:
            if bg > nbg + 0.05:
                sv_notes.append(f"{short(c)}は、新規事業所の廃止・休止率が{bg * 100:.0f}%と全国（{nbg * 100:.0f}%）を上回る。競争が激しいか、事業を続けにくい地域と考えられる。")
            elif bg < nbg - 0.05:
                sv_notes.append(f"{short(c)}は、新規事業所の廃止・休止率が{bg * 100:.0f}%と全国（{nbg * 100:.0f}%）を下回り、事業を続けやすい地域と考えられる。")
    h.append('</table>' + ('<ul>' + "".join(f"<li>{E(x)}</li>" for x in sv_notes) + '</ul>' if sv_notes else
                           '<p>いずれのサービスも、全国と大きな差はない。</p>')
             + '<p class="note">WAM NETの事業所番号で照合した。廃止・休止のほか、移転や法人変更で番号が変わった事業所も含むため、実際の廃止率より高めに出る。'
             '直近1年以内に新規登録された事業所は、評価するには期間が短いため除外した。</p></section>')

    # 6. 法人
    h.append(f'<section class="pb"><h2>§運営法人</h2><p>{E(city)}で事業所を運営する法人は<b>{n_corp:,}</b>法人である。'
             '1法人が複数のサービスを運営している場合があるため、割合は事業所数で集計した。</p>')
    h.append('<h3>法人種別の構成（事業所数ベース）</h3>' + stack_bar(ctype.most_common()))
    h.append('<h3>事業所数の多い法人（上位10）</h3><table class="small txt"><tr><th>法人</th><th>事業所数</th><th>運営サービス</th></tr>')
    for k, v in corps.most_common(10):
        h.append(f'<tr><td>{E(corp_name[k])}</td><td>{v}</td><td>{E("、".join(sorted(corp_svc[k])))}</td></tr>')
    top10 = sum(v for _, v in corps.most_common(10)) / max(len(offs), 1) * 100
    h.append(f'</table><p class="note">上位10法人が、市内の事業所の約{top10:.0f}%を運営している。</p>')
    h.append(f'<h3>大手法人の比率</h3><p>全国で{CHAIN_MIN}か所以上の事業所を運営する法人を「大手」とした。'
             f'{E(city)}の事業所のうち大手は<b>約{chain_share * 100:.0f}%</b>、{E(lname(ED["main"]))}に限ると<b>約{share65 * 100:.0f}%</b>である。'
             f'直近半年の新規登録では、大手が<b>約{new_chain_share * 100:.0f}%</b>'
             + ("を占め、大手の出店が目立つ。" if new_chain_share > chain_share + 0.05 else "を占める。") + '</p>')
    if chains:
        h.append('<table class="small"><tr><th>大手法人</th><th>市内の事業所数</th><th>全国の事業所数</th></tr>'
                 + "".join(f'<tr><td>{E(v[0][1]["corp"])}</td><td>{len(v)}</td><td>{CORP_N[k]:,}</td></tr>'
                           for k, v in sorted(chains.items(), key=lambda kv: -len(kv[1]))[:8]) + '</table>')
    h.append(f'<h3>直近半年の参入法人・撤退法人</h3><p>{lq}から{lp}までの半年で、{E(city)}に新たに参入した法人は<b>{len(c_new)}</b>、'
             f'事業所の登録がなくなった法人は<b>{len(c_gone)}</b>である。</p>')
    if c_new:
        h.append('<table class="small txt"><tr><th>参入法人</th><th>事業所数</th><th>サービス</th></tr>'
                 + "".join(f'<tr><td>{E(cin_now[k][0][1]["corp"])}</td><td>{len(cin_now[k])}</td><td>{E("、".join(sorted({short(c) for c, _ in cin_now[k]})))}</td></tr>'
                           for k in sorted(c_new, key=lambda k: -len(cin_now[k]))[:10]) + '</table>')
    h.append('<p class="note">法人番号（ない場合は法人名）で集計した。届出の再提出により、同じ法人が入れ替わったように見える場合がある。</p></section>')

    # 7. 定員
    if ED["main"] in caps:
        h.append('<section class="pb"><h2>§定員</h2><p>定員は、各事業所が1日に受け入れられる人数として届け出ている数である。'
             '定員の記載がある事業所のみを集計した。</p><table><tr><th>サービス</th><th>事業所数</th><th>うち定員の記載あり</th>'
             '<th>定員の合計</th><th>1事業所の定員（中央値）</th><th>人口1万人当たりの定員</th></tr>')
        for c, (n, nc, sm, md) in caps.items():
            if c not in SV:
                continue
            h.append(f'<tr><th>{E(short(c))}</th><td>{n}</td><td>{nc}</td><td>{sm:,}人</td><td>{md:g}人</td><td>{rate(sm, pop):.1f}人</td></tr>')
        h.append('</table>' + ('<p class="note">定員に対する利用の割合（利用率）は、次の「放課後等デイの利用率」の章で示す。</p>' if child else '') + '</section>')


    # 8. 利用率
    if child:
        h.append('<section class="pb"><h2>§放課後等デイの利用率</h2><p>利用率は、定員のうち実際に利用されている割合である。'
                 '市の障害児福祉計画にある月間の延べ利用日数（人日）を、同時期の定員合計と月間営業日数（約23日）で割って算出した。</p>')
        if util:
            users = f"{hd['users']:,}人" if hd.get("users") else "—"
            h.append(f'<table><tr><th>項目</th><th>数</th><th>説明</th></tr>'
                     f'<tr><th>月間の延べ利用日数</th><td>{hd["nd"]:,}人日</td><td style="text-align:left">{fy_label(hd["fy"])}・{E(hd["kind"])}</td></tr>'
                     f'<tr><th>月間の利用児童数</th><td>{users}</td><td style="text-align:left">同上</td></tr>'
                     f'<tr><th>同時期の定員合計（推計）</th><td>約{cap_then:,.0f}人</td><td style="text-align:left">同時期の事業所数×現在の平均定員（{avg_cap:.1f}人）</td></tr>'
                     f'<tr><th><b>利用率の目安</b></th><td><b>約{util * 100:.0f}%</b></td><td style="text-align:left">全国平均は89.1%（福祉医療機構、2024年度）</td></tr></table>')
            msg = ("計算上、利用が定員を大きく上回る。WAM NETに未登録の事業所がある、または市外の事業所に通う子どもが多いと考えられる。数値そのものより、供給不足の傾向を示す目安として扱う必要がある。" if util > 1.2 else
                   "計算上、利用が定員を上回る。市外の事業所に通う子どもが多い、または定員を超えて受け入れる事業所が多いと考えられる。供給が不足している可能性が高い。" if util > 1.0 else
                   "全国平均を上回り、空きの少ない地域である。新規事業所にも利用者が集まりやすいと考えられる。" if util >= 0.9 else
                   "全国平均並みの地域である。立地やサービス内容で選ばれる事業所になることが重要である。" if util >= 0.8 else
                   "全国平均を下回り、空きのある地域である。新規に開設する場合は、空白地域を選ぶか、他にない特色を打ち出す必要がある。")
            h.append(f'<p>{msg}</p>')
            if hd.get("users"):
                h.append(f'<p class="note">利用児1人当たりの月間利用日数は平均{hd["nd"] / hd["users"]:.1f}日である。</p>')
            h.append(f'<p class="note">出典：<a href="{E(hd["url"])}">{E(hd["src"])}</a>。市の数値は市内に住む子どもの利用量で、市外の事業所の利用分も含む。'
                     '一方、定員は市内の事業所の数値であるため、利用率は概算である。</p>')
        else:
            h.append('<p>市の公表資料では、放課後等デイの月間延べ利用日数（人日）の実績を確認できなかった。'
                     'そのため収支試算では全国平均（89%）を用いた。市の担当課で障害児福祉計画の実績値を確認すれば、より正確な試算ができる。</p>')
        h.append('</section>')

    # 9. 人の集めやすさ
    if child:
        h.append(f'<section class="pb"><h2>§人材確保</h2><p>放課後等デイや児童発達支援の運営には、保育士や児童指導員の確保が欠かせない。'
                 f'有効求人倍率は求職者1人当たりの求人数で、高いほど採用が難しいことを示す。</p>'
                 f'<table><tr><th>項目</th><th>{E(pref)}</th><th>全国</th></tr>'
                 f'<tr><th>保育士の有効求人倍率（2026年1月）</th><td>{pf["hoiku"]:.2f}倍</td><td>{nf["hoiku"]:.2f}倍</td></tr>'
                 f'<tr><th>全職種の有効求人倍率（2026年8月）</th><td>{pf["all"]:.2f}倍</td><td>{nf["all"]:.2f}倍</td></tr>'
                 f'<tr><th>保育士の平均月給（2025年）</th><td>{pf["pay_month"] / 10:,.1f}万円<small>（賞与込み年収{pf["pay"] / 10:,.0f}万円）</small></td><td>{nf["pay_month"] / 10:,.1f}万円<small>（年収{nf["pay"] / 10:,.0f}万円）</small></td></tr>'
                 f'<tr><th>最低賃金（時給）</th><td>{pf["wage"]:,}円<small>（{E(pf["wage_from"])}から）</small></td><td>{nf["wage"]:,}円<small>（平均）</small></td></tr></table>')
        h.append(f'<figure><figcaption>保育士の有効求人倍率（2026年1月）</figcaption>'
                 + bars_h([(pref, pf["hoiku"], True), ("全国", nf["hoiku"], False), ("全職種（全国）", nf["all"], False)], unit="倍", fmt="{:.2f}") + '</figure>')
        hard = pf["hoiku"] / pf["all"]
        h.append(f'<p>{E(pref)}の保育士の有効求人倍率は、全職種の<b>約{hard:.1f}倍</b>である。'
                 + ("全国と比べても採用が難しい地域である。" if pf["hoiku"] > nf["hoiku"] else "全国よりやや低いものの、採用が難しい職種であることに変わりはない。")
                 + '最大の課題は、各事業所に1人以上の配置が必要な児童発達支援管理責任者の確保である。開設前に担当者を決めておく必要がある。</p>'
                 f'<p class="note">収支試算の人件費は、障害福祉分野の職種別給与（厚生労働省「処遇状況等調査」）を、{E(pref)}の介護職員の平均年収'
                 f'（{pf["care"] / 10:,.0f}万円、全国{nf["care"] / 10:,.0f}万円）で地域補正して積み上げた。家賃は{E(city)}の借家の平均家賃（月{cf["rent"]:,}円、全国{nf["rent"]:,}円）で補正した。</p></section>')

    else:
        who_ = "世話人・生活支援員・夜勤者" if ed == "gh" else "職業指導員・生活支援員"
        h.append(f'<section class="pb"><h2>§人材確保</h2><p>{E(lname(ED["main"]))}の運営には、{who_}の確保が欠かせない。'
                 '有効求人倍率は求職者1人当たりの求人数で、高いほど採用が難しいことを示す。</p>'
                 f'<table><tr><th>項目</th><th>{E(pref)}</th><th>全国</th></tr>'
                 f'<tr><th>全職種の有効求人倍率（2026年8月）</th><td>{pf["all"]:.2f}倍</td><td>{nf["all"]:.2f}倍</td></tr>'
                 f'<tr><th>介護職員の平均年収（2025年、福祉職の給与水準の目安）</th><td>{pf["care"] / 10:,.0f}万円</td><td>{nf["care"] / 10:,.0f}万円</td></tr>'
                 f'<tr><th>最低賃金（時給）</th><td>{pf["wage"]:,}円<small>（{E(pf["wage_from"])}から）</small></td><td>{nf["wage"]:,}円<small>（平均）</small></td></tr></table>'
                 f'<p>{E(pref)}の全職種の有効求人倍率は全国{"を上回り、採用が難しい地域" if pf["all"] > nf["all"] else "並みかそれ以下で、比較的採用しやすい地域"}である。'
                 + ('夜勤者は夜間のみのパート勤務で確保することが多く、地域の賃金水準が人件費に直結する。' if ed == "gh" else
                    '職業指導員は、作業内容（軽作業、食品加工、農作業、PC業務など）に応じた経験者を採用すると、仕事の受注につながりやすい。')
                 + '最大の課題は、各事業所に配置が必要なサービス管理責任者の確保である。実務経験と研修修了が要件となるため、開設前に担当者を決めておく必要がある。</p>'
                 f'<p class="note">収支試算の人件費は、障害福祉分野の職種別給与（厚生労働省「処遇状況等調査」）を、{E(pref)}の介護職員の平均年収'
                 f'（{pf["care"] / 10:,.0f}万円、全国{nf["care"] / 10:,.0f}万円）で地域補正して積み上げた。'
                 f'家賃は{E(city)}の借家の平均家賃（月{cf["rent"]:,}円、全国{nf["rent"]:,}円）で補正した。</p></section>')

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
        grid = ("<table class=\"small\"><tr><th>利用率＼1人1日当たり報酬</th>" + "".join(f"<th>{p:,.0f}円</th>" for p in grid_p) + "</tr>"
                + "".join(f"<tr><th>{u * 100:.0f}%</th>" + "".join(f"<td>{sim_year(u, p, cost):+,.0f}</td>" for p in grid_p) + "</tr>" for u in grid_u)
                + "</table>")
        h.append(f'<section class="pb"><h2>§放課後等デイの収支試算</h2>'
                 f'<p>定員10人の放課後等デイを{E(city)}で開設した場合の年間収支を試算した。{E(city)}の地域区分は<b>{grade_txt}</b>で、1単位の単価は<b>{price:.2f}円</b>である'
                 f'（最も高い東京23区は11.20円、上乗せなしの地域は10.00円）。</p>'
                 f'<table><tr><th>ケース</th><th>利用率</th><th>1人1日当たり報酬</th><th>年間売上</th><th>年間費用</th><th>年間収支</th></tr>{rows_sim}</table>'
                 f'<ul><li>損益分岐点の利用率は、標準単価で<b>約{be * 100:.0f}%</b>である。</li>'
                 f'<li>開業費用、立ち上げ期の赤字、報酬の入金が2か月遅れる分を合わせると、<b>必要資金は約{-low:,.0f}万円</b>である。'
                 + (f'標準ケースでは、開業から約{back // 12}年{back % 12}か月で投資を回収できる。</li>' if back else '標準ケースでは年間収支が赤字のため、投資を回収できない。</li>')
                 + '</ul><h3>利用率・単価別の年間収支（万円）</h3>' + grid
                 + f'<h3>前提</h3><table class="small txt2"><tr><th>項目</th><th>設定</th></tr>'
                 f'<tr><td>営業日数</td><td>年{DAYS}日（福祉医療機構の経営分析の平均280.4日）</td></tr>'
                 f'<tr><td>1人1日当たり報酬</td><td>標準{per["標準"][1]:,.0f}円。全国平均12,747円（4級地相当）を{E(city)}の単価で補正し、2026年6月からの新規事業所の減算を見込んでやや低めに設定した</td></tr>'
                 f'<tr><td>人件費</td><td>年{staff:,}万円。管理者兼児童発達支援管理責任者1人、児童指導員・保育士等 常勤換算{DIRECT["65"]}人（全国平均は定員11人で5.1人）。内訳は下表のとおり</td></tr>'
                 f'<tr><td>その他の費用</td><td>年{other:,}万円（家賃月{rent / 12:.0f}万円、送迎車両費、水道光熱費、教材費、保険料、求人費、開業費用の償却。家賃は{E(city)}の水準で補正）</td></tr>'
                 f'<tr><td>開業費用</td><td>{OPEN}万円（内装・備品、敷金、開業前の人件費、指定申請の準備費用）</td></tr>'
                 f'<tr><td>開業後の利用率</td><td>3か月ごとに30%→50%→70%→80%と推移し、その後の半年は85%、以降は標準の利用率</td></tr></table>'
                 + '<h3>人件費の内訳</h3>' + staff_html(staff_rows, staff, wage_r)
                 + '<p class="contact"><b>試算に含まれていない要素：</b>障害児には障害支援区分がない。放課後等デイの基本報酬は、2024年度改定で<b>利用時間</b>'
                 '（30分以上1時間30分以下／1時間30分超3時間以下／3時間超5時間以下）によって区分され、<b>重症心身障害児</b>を主に受け入れる事業所は別の報酬体系となる。'
                 '医療的ケア児を受け入れる場合の加算もある。本試算は全国平均の1人1日当たり報酬を用いているため、受け入れる子どもと利用時間の組み合わせによって上下する。</p>'
                 '<p class="note">本試算は検討のたたき台であり、家賃・給与水準・加算の取得状況によって大きく変わる。実際の開設にあたっては、税理士や行政書士とともに精査する必要がある。税金は含まない。'
                 '出典：福祉医療機構「2024年度 放課後等デイサービスの経営状況」（利用率89.1%、1人1日当たり12,747円、赤字事業所の割合31.8%）。</p></section>')

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
        grid = ("<table class=\"small\"><tr><th>" + word + "＼1人1日当たり収入</th>"
                + "".join(f"<th>{m_['pd'] * k:,.0f}円</th>" for k in grid_k) + "</tr>"
                + "".join(f"<tr><th>{u * 100:.0f}%</th>" + "".join(f"<td>{rv_at(u) * k - m_['ct']:+,.0f}</td>" for k in grid_k) + "</tr>" for u in grid_u)
                + "</table>")
        if ed == "gh":
            nshift = NIGHT_SHIFT * wage_r / wage_chiba
            pre = (f'<tr><td>類型・定員・営業日数</td><td>介護サービス包括型。定員{m_["cap"]}人（' + (f'1住居' if m_["houses"] == 1 else f'定員{GH_HOUSE}人の住居×{m_["houses"]}か所') + '）、年365日</td></tr>'
                   f'<tr><td>報酬</td><td>基本報酬（区分3〜4中心、1人1日平均{m_["units"]:.0f}単位）と夜間支援等体制加算(Ⅰ)（1住居1晩約{m_["night_u"]:,.0f}単位）に'
                   f'{E(city)}の単価{m_["pr"]:.2f}円を乗じ、福祉・介護職員等処遇改善加算(Ⅰ)（{GH_SHOGU * 100:.1f}%）を加えた。年{m_["fee"]:,.0f}万円</td></tr>'
                   f'<tr><td>家賃</td><td>建物の賃料は1住居月{m_["rent"] / m_["houses"] / 12:.0f}万円（{E(city)}の家賃水準）。同額を入居者から家賃として受け取る前提で、収入と費用の双方に計上した。'
                   f'空室分は事業者の負担となる</td></tr>'
                   f'<tr><td>1人1日当たり収入</td><td>{m_["pd"]:,.0f}円（報酬と入居者の家賃の合計）</td></tr>'
                   f'<tr><td>人件費</td><td>年{m_["st"]:,}万円。管理者兼サービス管理責任者1人（サービス管理責任者の配置基準は入居者30人に1人のため、残りの時間は世話人として勤務する前提）、'
                   f'毎晩の夜勤（1回{nshift:.1f}万円' + (f'×{m_["houses"]}住居' if m_["houses"] > 1 else '') + '）、不足分の世話人・生活支援員。内訳は下表のとおり</td></tr>'
                   f'<tr><td>その他の費用</td><td>年{m_["ct"] - m_["st"]:,}万円（建物の賃料のほか、水道光熱費・消耗品・保険・修繕・車両など定員1人当たり年{GH_OTHER}万円）</td></tr>'
                   f'<tr><td>開業費用</td><td>{m_["open"]:,}万円（1住居{GH_OPEN}万円：改修工事、消防設備、敷金、開業前の人件費）</td></tr>')
            src = ("厚生労働省「令和7年 障害福祉サービス等経営概況調査」（介護サービス包括型、2024年度決算）、同「令和6年度 障害福祉サービス等従事者処遇状況等調査」、"
                   "2024年度報酬改定の単位数、夜勤の給与は首都圏の求人（1回1.6〜2.0万円）")
            nat_rv, nat_st, nat_bal = 2482, 1538, 0.069  # 経営概況調査の定員1人あたり収入・給与費（千円）と収支差率
            g1 = gh_model(1, GH_MIX[1][1], GH_UTIL, gh_pr, wage_r, wage_chiba, gh_rent_h)
            base_fee = g1["fee"] - g1["night_fee"]
            n_loss = g1["shifts"] * (1 + SOCIAL) - g1["night_fee"]
            res1 = GH_HOUSE * GH_UTIL
            oth1 = g1["ct"] - g1["st"] - g1["rent"]
            why = ('<h3>利益が出にくい理由</h3>'
                   f'<p>1住居（定員{GH_HOUSE}人、入居率{GH_UTIL * 100:.0f}%）の年間の収入と費用は次のとおりである。</p>'
                   '<table class="small"><tr><th>収入</th><th>年額</th><th>費用</th><th>年額</th></tr>'
                   f'<tr><td>基本報酬（処遇改善加算を含む）</td><td>{base_fee:,.0f}万円</td><td>管理者兼サービス管理責任者</td><td>{g1["lead"] * (1 + SOCIAL):,.0f}万円</td></tr>'
                   f'<tr><td>夜間支援等体制加算（同）</td><td>{g1["night_fee"]:,.0f}万円</td><td>夜勤（毎晩1人）</td><td>{g1["shifts"] * (1 + SOCIAL):,.0f}万円</td></tr>'
                   f'<tr><td>入居者の家賃</td><td>{g1["rent"] * GH_UTIL:,.0f}万円</td><td>世話人・生活支援員（不足分）</td><td>{(g1["st"] - (g1["lead"] + g1["shifts"]) * (1 + SOCIAL)):,.0f}万円</td></tr>'
                   f'<tr><td></td><td></td><td>建物の賃料</td><td>{g1["rent"]:,}万円</td></tr>'
                   f'<tr><td></td><td></td><td>その他の費用</td><td>{oth1:,}万円</td></tr>'
                   f'<tr><th>収入の合計</th><th>{g1["rv"]:,.0f}万円</th><th>費用の合計</th><th>{g1["ct"]:,}万円</th></tr></table>'
                   '<p class="note">人件費は社会保険料などの事業主負担を含む。</p><ul>'
                   f'<li><b>夜勤の費用が加算を上回る：</b>夜勤の費用は年{g1["shifts"] * (1 + SOCIAL):,.0f}万円かかる一方、夜間支援等体制加算は年{g1["night_fee"]:,.0f}万円にとどまり、'
                   f'1住居当たり年約{n_loss:,.0f}万円の持ち出しとなる。</li>'
                   f'<li><b>サービス管理責任者の給与が固定費になる：</b>年約{g1["lead"] * (1 + SOCIAL):,.0f}万円で、入居者1人当たり月約{g1["lead"] * (1 + SOCIAL) / res1 / 12:.1f}万円の負担となる。'
                   '住居を増やしても1人で兼務できる（入居者30人まで）ため、住居数が多いほど負担は軽くなる。</li>'
                   f'<li><b>給与水準が高い：</b>{E(pref)}の介護職員の年収は全国平均の{wage_r:.2f}倍で、その分、人件費が全国平均より重くなる。</li>'
                   '<li><b>区分の軽い入居者が多いと報酬が低い：</b>区分3〜4中心の入居者構成では、基本報酬は1人1日約3,300円（区分6なら約6,600円）にとどまる。</li></ul>'
                   '<p>このため、包括型で利益を確保するには、住居を2か所以上に増やす、区分の重い入居者を受け入れる、加算を取得する、のいずれかが必要となる（「投資判断」の章の住居数・区分別の試算を参照）。</p>')
            dmx = "・".join(f"区分{k} {int(v * 100)}%" for k, v in GHD_MIX.items())
            day = ('<h3>日中サービス支援型との比較</h3>'
                   '<p>グループホームには、主に「介護サービス包括型」と「日中サービス支援型」がある。日中サービス支援型は、重度の入居者を対象に、日中も職員が常駐し、夜勤者を必ず配置する類型で、'
                   '短期入所（ショートステイ）の併設が義務付けられている。基本報酬は包括型より高いが、職員の配置も手厚くなる。</p>'
                   '<table class="small"><tr><th>項目</th><th>介護サービス包括型</th><th>日中サービス支援型</th></tr>'
                   f'<tr><td>定員</td><td>{m_["cap"]}人（1住居）</td><td>{ghd["cap"]}人（定員{GH_HOUSE}人の住居×{ghd["houses"]}）</td></tr>'
                   f'<tr><td>入居者の区分（想定）</td><td>区分2 20%・区分3 40%・区分4 40%</td><td>{dmx}</td></tr>'
                   f'<tr><td>基本報酬（1人1日の平均）</td><td>{m_["units"]:.0f}単位</td><td>{ghd["units"]:.0f}単位</td></tr>'
                   f'<tr><td>年間売上（家賃を含む）</td><td>{m_["rv"]:,.0f}万円</td><td>{ghd["rv"]:,.0f}万円</td></tr>'
                   f'<tr><td>人件費</td><td>{m_["st"]:,}万円</td><td>{ghd["st"]:,}万円（常勤換算{ghd["direct"] + 1:.1f}人）</td></tr>'
                   f'<tr><td>その他の費用（建物の賃料を含む）</td><td>{m_["ct"] - m_["st"]:,}万円</td><td>{ghd["ct"] - ghd["st"]:,}万円</td></tr>'
                   f'<tr><td><b>年間収支</b></td><td><b>{m_["bal"]:+,.0f}万円</b>（{m_["bal"] / m_["rv"] * 100:+.1f}%）</td><td><b>{ghd["bal"]:+,.0f}万円</b>（{ghd["bal"] / ghd["rv"] * 100:+.1f}%）</td></tr>'
                   f'<tr><td>損益分岐点の入居率</td><td>約{m_["be"] * 100:.0f}%</td><td>約{ghd["be"] * 100:.0f}%</td></tr>'
                   f'<tr><td>開業費用</td><td>{m_["open"]:,}万円</td><td>{ghd["open"]:,}万円（短期入所の部屋を含む）</td></tr>'
                   f'<tr><td>全国平均の収支差率</td><td>+6.9%（定員16人）</td><td>+{GHD_NAT["bal"] * 100:.1f}%（定員{GHD_NAT["cap"]}人、常勤換算{GHD_NAT["staff"]}人）</td></tr></table>'
                   f'<p class="note">日中サービス支援型の職員数は、令和7年 障害福祉サービス等経営概況調査（第18表、2024年度決算、{GHD_NAT["n"]}事業所）の定員1人当たりの常勤換算'
                   f'（生活支援員・世話人・夜間支援従事者で{GHD_DIRECT:.2f}人）を定員{ghd["cap"]}人に当てはめた。基本報酬は2024年度改定後の日中サービス支援型サービス費(Ⅰ)（世話人5:1）。'
                   '併設する短期入所の収入と費用、夜勤職員加配加算などの加算は含めていない。日中サービス支援型の指定には、市の協議会への報告など独自の手続きがあるため、事前に市に確認する必要がある。</p>')
            extra = why + (f'<p>夜勤者は各住居に毎晩1人配置する前提とした。夜勤（16時〜翌9時）のうち、夕方と朝の{SHIFT_DAY}時間は世話人・生活支援員の勤務として数え、'
                     f'残りの夜間の時間を夜間支援等体制加算の対象とした。夜勤1回の費用は社会保険料込みで約{nshift * (1 + SOCIAL):.1f}万円、'
                     f'加算収入は1住居1晩当たり約{m_["night_u"] * m_["pr"] * (1 + GH_SHOGU) / 1e4:.1f}万円である。</p>'
                     '<h3>全国平均との比較（定員1人当たり年額）</h3>'
                     '<table class="small"><tr><th></th><th>本試算</th><th>全国平均（介護サービス包括型）</th></tr>'
                     f'<tr><td>収入（報酬＋入居者の利用料）</td><td>{m_["rv"] / m_["cap"]:,.0f}万円</td><td>{nat_rv / 10:,.0f}万円</td></tr>'
                     f'<tr><td>人件費</td><td>{m_["st"] / m_["cap"]:,.0f}万円</td><td>{nat_st / 10:,.0f}万円</td></tr>'
                     f'<tr><td>収支差率</td><td>{m_["bal"] / m_["rv"] * 100:+.1f}%</td><td>+{nat_bal * 100:.1f}%（赤字事業所の割合39.0%）</td></tr></table>'
                     '<p class="note">全国平均は令和7年 障害福祉サービス等経営概況調査による（2024年度決算、1事業所当たり定員16人、常勤換算職員6.2人、集計341事業所）。'
                     '全国平均では夜間支援従事者が常勤換算0.41人にとどまり、毎晩の夜勤を置かない事業所（宿直や連絡体制のみの事業所）が多く含まれるとみられる。'
                     '本試算は毎晩夜勤者を置く分だけ人件費が重く、全国平均より厳しめの見積もりである。</p>' + day)
        else:
            pre = (f'<tr><td>定員・営業日数</td><td>定員{m_["cap"]}人、年{m_["days"]}日</td></tr>'
                   f'<tr><td>1人1日当たり報酬</td><td>{m_["pd"]:,.0f}円。全国平均9,126円（4級地相当）を{E(city)}の単価{m_["pr"]:.2f}円で補正した。報酬は平均工賃の水準によって変わる</td></tr>'
                   f'<tr><td>人件費</td><td>年{m_["st"]:,}万円。管理者兼サービス管理責任者1人、職業指導員・生活支援員 常勤換算{DIRECT["46"]}人（全国平均は定員21人で3.6人）、目標工賃達成指導員・送迎・事務等{DIRECT_OTHER["46"]}人。内訳は下表のとおり</td></tr>'
                   f'<tr><td>その他の費用</td><td>年{m_["ct"] - m_["st"]:,}万円（家賃、送迎車両費、水道光熱費、保険料、求人費、開業費用の償却。家賃は{E(city)}の水準で補正）</td></tr>'
                   f'<tr><td>開業費用</td><td>{m_["open"]:,}万円（内装・作業設備、敷金、開業前の人件費、指定申請の準備費用）</td></tr>'
                   '<tr><td>工賃</td><td>利用者への工賃は生産活動の収入から支払う前提とし、本表の収入・費用には含めていない</td></tr>')
            src = "福祉医療機構「" + OTHERS["46"]["src"] + "」"
            extra = ('<p>B型の基本報酬は、利用者に支払う平均工賃（全国平均 月23,583円）が高いほど上がる。安定した仕事を確保できるかどうかが、収入と利用者確保の両方を左右する。</p>'
                     '<p class="contact"><b>試算に含まれていない要素：</b>B型の基本報酬は、障害支援区分ではなく<b>平均工賃の水準</b>と<b>人員配置（7.5:1または6:1）</b>で決まる。'
                     '本試算は全国平均の1人1日当たり報酬を用いているため、工賃を引き上げられる仕事の有無や人員配置の厚さによって、収入は上下する。</p>')
        h.append(f'<section class="pb"><h2>§{E(lname(mc))}の収支試算</h2>'
                 f'<p>定員{m_["cap"]}人の' + (f'{E(short(mc))}（介護サービス包括型、1住居）' if ed == "gh" else E(lname(mc))) + f'を{E(city)}で開設した場合の年間収支を試算した。{E(city)}の地域区分は<b>{grade_txt}</b>である。</p>'
                 f'<table><tr><th>ケース</th><th>{word}</th><th>1人1日当たり収入</th><th>年間売上</th><th>年間費用</th><th>年間収支</th></tr>{rows_m}</table>'
                 f'<ul><li>損益分岐点の{word}は<b>約{est["be"] * 100:.0f}%</b>である。</li>'
                 f'<li>開業費用、立ち上げ期の赤字、報酬の入金が2か月遅れる分を合わせると、<b>必要資金は約{-est["low"]:,.0f}万円</b>である。'
                 + (f'標準ケースでは、開業から約{est["back"] // 12}年{est["back"] % 12}か月で投資を回収できる。</li>' if est["back"] else '標準ケースでは年間収支が赤字のため、投資を回収できない。</li>')
                 + '</ul>' + extra + f'<h3>{word}・1人1日当たり収入別の年間収支（万円）</h3>' + grid
                 + '<h3>前提</h3><table class="small txt2"><tr><th>項目</th><th>設定</th></tr>' + pre
                 + f'<tr><td>開業後の{word}</td><td>3か月ごとに30%→50%→70%→80%と推移し、その後の半年は85%、以降は標準の{word}</td></tr></table>'
                 + '<h3>人件費の内訳</h3>' + staff_html(m_["rows"], m_["st"], wage_r)
                 + '<p class="note">本試算は検討のたたき台であり、家賃・給与水準・加算の取得状況によって大きく変わる。実際の開設にあたっては、税理士や行政書士とともに精査する必要がある。税金は含まない。'
                 f'出典：{E(src)}。</p></section>')


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
            ("1人1日当たり報酬（±5%）", prof(pd_=std_pd * 0.95) - base_p, prof(pd_=std_pd * 1.05) - base_p, "-5%", "+5%"),
            ("人件費（±10%）", -(P_["staff"] * 0.1), P_["staff"] * 0.1, "+10%", "-10%"),
            ("家賃（±30%）", -(P_["rent"] * 0.3 * (1 - std_u if ed == "gh" else 1)), P_["rent"] * 0.3 * (1 - std_u if ed == "gh" else 1), "+30%", "-30%")]
    torn.sort(key=lambda t: -abs(t[2] - t[1]))
    us = [x / 100 for x in range(50, 101, 5)]
    be_u = P_["cost"] / (P_["cap"] * P_["days"] * std_pd / 1e4)
    months = list(range(0, 61))
    cols = {"慎重": "#d9792b", "標準": "#2d7a45", "好調": "#3b6fb6"}

    def pct(v):
        return f"{v * 100:.0f}%" if v is not None else "—"

    def yrs(m):
        return f"約{m // 12}年{m % 12}か月" if m else "回収できない（赤字）"
    rows_i = "".join(
        f'<tr{" class=\"me\"" if k == "標準" else ""}><th>{k}</th><td>{u * 100:.0f}%</td><td>{v["need"]:,.0f}万円</td><td>{v["profit"]:+,.0f}万円</td>'
        f'<td><b>{pct(v["roi"])}</b></td><td>{yrs(v["back"])}</td><td>{v["c5"]:+,.0f}万円</td><td>{pct(v["irr"])}</td><td>{v["npv"]:+,.0f}万円</td></tr>'
        for k, v in inv.items() for u in [P_["lv"][k][0]])
    sd = inv["標準"]

    def kubun_table():
        """グループホームで、入居する人の障害支援区分と住居の数で、収支がどう変わるか。"""
        if ed != "gh":
            return ""

        def one(houses, mx):
            g = gh_model(houses, mx, std_u, gh_pr, wage_r, wage_chiba, gh_rent_h)
            v = invest(g["cap"], 365, std_u, g["pd"], g["ct"], g["open"])
            return g, v

        def roi_s(v):
            return f'{v["roi"] * 100:.0f}%' if v["roi"] is not None else "—"
        rows_ = []
        for name, mx in GH_MIX:
            g, v = one(GH_HOUSES, mx)
            mix_s = "・".join(f"区分{k if k != '1' else '1以下'} {int(x * 100)}%" for k, x in mx.items())
            rows_.append(f'<tr{" class=\"me\"" if mx is GH_MIX[1][1] else ""}><th>{E(name)}<br><small>{mix_s}</small></th>'
                         f'<td>{g["units"] * gh_pr:,.0f}円</td><td>{g["rv"]:,.0f}万円</td><td>{g["st"]:,}万円</td><td><b>{g["bal"]:+,.0f}万円</b></td><td>{roi_s(v)}</td></tr>')
        rows_h = []
        for hs in (1, 2, 3):
            g, v = one(hs, GH_MIX[1][1])
            rows_h.append(f'<tr{" class=\"me\"" if hs == GH_HOUSES else ""}><th>{hs}住居（定員{g["cap"]}人）</th><td>{g["rv"]:,.0f}万円</td><td>{g["st"]:,}万円</td>'
                          f'<td><b>{g["bal"]:+,.0f}万円</b></td><td>{g["bal"] / g["rv"] * 100:+.1f}%</td><td>{g["open"]:,}万円</td></tr>')
        return ('<h3>入居者の障害支援区分による違い</h3>'
                '<p>グループホームの基本報酬は、入居者の障害支援区分によって大きく異なる（区分6は1日600単位、区分2は188単位）。'
                '区分の重い入居者が多いほど売上は増えるが、指定基準上、生活支援員を多く配置する必要があるため、人件費も増える。</p>'
                f'<table class="small"><tr><th>入居者の区分構成（{GH_HOUSES}住居）</th><th>基本報酬（1人1日）</th><th>年間売上</th><th>人件費</th><th>年間利益</th><th>ROI</th></tr>'
                + "".join(rows_) + '</table>'
                '<h3>住居数による違い（区分3〜4中心）</h3>'
                '<p>管理者兼サービス管理責任者は、入居者30人までは1人で兼務できる。一方、夜勤者は住居ごとに毎晩必要となる。'
                'このため1住居では固定的な人件費を賄えず、住居数を増やすほど収支は改善する。</p>'
                '<table class="small"><tr><th>住居数</th><th>年間売上</th><th>人件費</th><th>年間利益</th><th>利益率</th><th>開業費用</th></tr>'
                + "".join(rows_h) + '</table>'
                f'<p class="note">基本報酬は、2024年度改定後の介護サービス包括型（世話人6:1）の単位数に、{E(city)}の1単位当たり単価{gh_pr:.2f}円を乗じて算出した。'
                '生活支援員は指定基準に従い、区分3は入居者9人に1人、区分4は6人に1人、区分5は4人に1人、区分6は2.5人に1人とした。'
                '重度障害者支援加算、医療連携体制加算、日中支援加算などは含めていない。これらを算定できれば、利益はその分増える。</p>')

    def site_money():
        """候補地ごとの家賃の目安と、そこで開設したときの見通し（標準）。"""
        rows_ = []
        for j, (_, c0, cl, cs0, _) in enumerate(cands, 1):
            if ED["main"] not in cs0:
                continue
            f, use, med, cm, nn = land_factor(city, cl["x"], cl["y"])
            d_rent = P_["rent"] * (f - 1) * (1 - std_u if ed == "gh" else 1)  # グループホームの家賃は入居者が払うので、空き部屋の分だけ
            v = invest(P_["cap"], P_["days"], std_u, std_pd, P_["cost"] + d_rent, P_["open_"])
            land = f'{use}の地価 {med / 1e4:,.1f}万円/㎡（市の中央値 {cm / 1e4:,.1f}万円、{nn}地点）' if use else "周辺に地価公示の地点が少ない（市の平均で計算）"
            roi_s = f'{v["roi"] * 100:.0f}%' if v["roi"] is not None else "—"
            rows_.append(f'<tr><th>{j}</th><td>{E(cl["towns"][0])}周辺</td><td>{land}</td><td>月{P_["rent"] * f / 12 / (GH_HOUSES if ed == "gh" else 1):,.0f}万円</td>'
                         f'<td>{v["profit"]:+,.0f}万円</td><td>{v["need"]:,.0f}万円</td><td>{roi_s}</td></tr>')
        if not rows_:
            return ""
        return ('<h3>候補地ごとの家賃と収支の見通し（' + E(short(ED["main"])) + '、標準ケース）</h3><table class="small"><tr><th>順位</th><th>場所</th><th>周辺1.5kmの地価</th>'
                '<th>家賃の目安</th><th>年間利益</th><th>投下資本</th><th>ROI</th></tr>' + "".join(rows_) + '</table>'
                f'<p class="note">家賃の目安は、{E(city)}の借家の平均家賃（総務省「住宅・土地統計調査」2023年）をもとに設定した家賃を、'
                '候補地周辺の地価（国土交通省「地価公示」）と市全体の地価の比で補正したものである。家賃は地価ほど変動しないため、補正には地価の比の平方根を用いた。'
                '実際の募集家賃とは異なるため、物件探しの際は、候補地近くの物件の募集家賃を不動産サイトで3件程度確認し、この表と比較されたい。'
                + ('グループホームの家賃は1住居当たりの額である。入居者から家賃を受け取る前提のため、利益に影響するのは空室分のみである。' if ed == "gh" else '') + '</p>')
    h.append(f'<section class="pb"><h2>§投資判断：投下資本・ROI・回収期間</h2>'
             f'<p>{E(city)}で{E(short(ED["main"]))}（定員{P_["cap"]}人）を開設する場合の<b>必要資金と投資の回収見通し</b>を、'
             f'収支試算の章と同じ前提で、10年間の月次の資金収支から算出した。</p>'
             + keypts([f'必要資金は、標準ケースで約{sd["need"]:,.0f}万円、慎重ケースで約{inv["慎重"]["need"]:,.0f}万円である。',
                       (f'標準ケースのROIは約{sd["roi"] * 100:.0f}%で、{yrs(sd["back"])}で回収できる。' if sd["back"] else '標準ケースでは年間収支が赤字のため、回収できない。'),
                       f'利益への影響が最も大きい要因は「{E(torn[0][0])}」である。'])
             + f'<div class="tiles"><div class="tile"><p class="tl">投下資本（必要資金）</p><p class="tv">{sd["need"]:,.0f}<small>万円</small></p></div>'
             f'<div class="tile"><p class="tl">ROI（年間利益÷投下資本）</p><p class="tv">{pct(sd["roi"])}</p></div>'
             f'<div class="tile"><p class="tl">回収期間</p><p class="tv" style="font-size:14pt">{yrs(sd["back"])}</p></div></div>'
             f'<table class="small"><tr><th>ケース</th><th>{P_["word"]}</th><th>投下資本</th><th>安定期の年間利益</th><th>ROI</th><th>回収期間</th><th>5年間の累計</th><th>IRR（10年）</th><th>NPV（10年、割引率5%）</th></tr>{rows_i}</table>'
             '<ul class="small">'
             '<li><b>投下資本</b>：開業費用、立ち上げ期の赤字、報酬の入金が2か月遅れる分の合計で、累計の資金収支が最も落ち込んだ時点の額である。融資と自己資金で用意すべき額の目安となる。</li>'
             '<li><b>ROI</b>：利用が安定した年の利益を投下資本で割った値である。逆数が回収年数の目安となり、例えば25%であれば約4年で回収できる。</li>'
             '<li><b>IRR</b>：10年間の資金収支から求めた年利回りである。10年後の事業価値（売却額や継続価値）を含まないため、低めに出る。</li>'
             '<li><b>NPV</b>：10年間の資金収支を割引率5%で現在価値に換算し、合計した値である。プラスであれば、利回り5%の投資より有利であることを示す。</li></ul>'
             '<h3>累計資金収支の推移（開業から5年）</h3>'
             + xy_chart([(k, cols[k], [inv[k]["cum"][m] for m in months]) for k in inv], months, "開業からの月数", "累計資金収支（万円）", every=12,
                        marks=[(sd["back"], "標準で回収")] if sd["back"] and sd["back"] <= 60 else [])
             + site_money() + kubun_table()
             + f'<p class="note">開業時に開業費用{P_["open_"]:,}万円を支出し、利用が増えるまでの赤字でさらに落ち込んだ後、徐々に回復する（いわゆる「Jカーブ」）。'
             '最も落ち込んだ時点の額が、事前に用意すべき資金である。</p>'
             f'<h3>{P_["word"]}と年間利益</h3>'
             + xy_chart([("年間利益", "#2d7a45", [prof(u=u) for u in us])], [round(u * 100) for u in us], f'{P_["word"]}（%）', "年間利益（万円）",
                        marks=[(round(be_u * 100), f"損益分岐 約{be_u * 100:.0f}%")] if 0.5 <= be_u <= 1 else [])
             + f'<p class="note">費用の大半は人件費と家賃で、{P_["word"]}にかかわらず発生する固定費である。このため、{P_["word"]}が5ポイント変わるだけで、年間利益は'
             f'約{abs(torn[0][2] if "率" in torn[0][0] else torn[1][2]):,.0f}万円変わる。</p>'
             '<h3>利益に影響する要因（感応度分析）</h3>'
             + tornado(torn)
             + f'<p class="note">標準ケース（年間利益{base_p:+,.0f}万円）から、1つの条件だけを変えた場合の利益の変化である。上にある項目ほど影響が大きい。'
             '開業前に確認する項目や、運営で注意すべき項目の優先順位の目安となる。</p>'
             '<p class="note">税金と借入金の利息は含めていない。費用に開業費用の償却を含めているため、必要資金はやや多めに見積もっている。'
             '本試算は、全国の平均的な事業所の数値を、この市の単価・給与・家賃の水準に合わせて補正したものであり、検討のたたき台である。</p></section>')

    # 5年間の損益計画
    us_m = [min(RAMP[m], std_u) if m < len(RAMP) else std_u for m in range(60)]
    oth_c = P_["cost"] - P_["staff"] - P_["rent"]
    pl = []
    for y in range(5):
        um = us_m[12 * y:12 * y + 12]
        rv_y = sum(P_["cap"] * P_["days"] / 12 * u * std_pd / 1e4 for u in um)
        pl.append(dict(u=sum(um) / 12, rv=rv_y, pr=rv_y - P_["cost"], cash=sd["cum"][12 * (y + 1)]))
    plr = [("平均の" + P_["word"], [f'{r["u"] * 100:.0f}%' for r in pl]),
           ("売上（報酬）", [f'{r["rv"]:,.0f}' for r in pl]),
           ("人件費", [f'{P_["staff"]:,.0f}' for r in pl]),
           ("家賃", [f'{P_["rent"]:,.0f}' for r in pl]),
           ("その他の費用（償却含む）", [f'{oth_c:,.0f}' for r in pl]),
           ("<b>利益</b>", [f'<b>{r["pr"]:+,.0f}</b>' for r in pl]),
           ("利益率", [f'{r["pr"] / r["rv"] * 100:.0f}%' if r["rv"] else "—" for r in pl]),
           ("年末の累計資金収支", [f'{r["cash"]:+,.0f}' for r in pl])]
    first_black = next((y + 1 for y, r in enumerate(pl) if r["pr"] > 0), None)
    h.append('<section class="pb"><h2>§§5年間の損益計画</h2>'
             + keypts([f'1年目の平均{P_["word"]}は{pl[0]["u"] * 100:.0f}%で、利益は{pl[0]["pr"]:+,.0f}万円となる。'
                      + (f'{first_black}年目に黒字化する見込みである。' if first_black else '5年以内には黒字化しない。'),
                      f'5年目の年間利益は{pl[4]["pr"]:+,.0f}万円、5年目末の累計資金収支は{pl[4]["cash"]:+,.0f}万円である。',
                      'この表は、融資申込時に提出する事業計画書の収支計画のたたき台として使える。'])
             + '<table class="small pl"><tr><th>（万円）</th>' + "".join(f'<th>{y}年目</th>' for y in range(1, 6)) + '</tr>'
             + "".join(f'<tr><th>{k}</th>' + "".join(f'<td>{v}</td>' for v in vs) + '</tr>' for k, vs in plr) + '</table>'
             + f'<p class="note">標準ケースの計画である。{P_["word"]}は、開業から3か月ごとに30%、50%、70%、80%と上がり、その後の半年は85%、'
             f'2年目の途中から{std_u * 100:.0f}%で安定すると仮定した。人件費は開業時から全員を採用する前提である（利用の増加に合わせて採用時期をずらせば、1年目の赤字は縮小する）。'
             f'「年末の累計資金収支」には、開業費用{P_["open_"]:,}万円と、報酬の入金が2か月遅れる分を含む。税金と借入金の返済は含めていない。</p></section>')

    # 候補地ごとの判断（冒頭のまとめに入れる）
    def cand_money(c, cl):
        f = land_factor(city, cl["x"], cl["y"])[0]
        if c == mc or (child and c != "63"):
            v = invest(P_["cap"], P_["days"], std_u, std_pd, P_["cost"] + P_["rent"] * (f - 1) * (1 - std_u if ed == "gh" else 1), P_["open_"])
            return v, P_["rent"] * f
        o = oth["63"]
        r0 = round(OTHERS["63"]["rent"] * rent_r)
        v = invest(o["cap"], o["days"], o["util"], o["pd"], o["ct"] + r0 * (f - 1), OTHERS["63"]["open"])
        return v, r0 * f
    ex_rows, verdicts = [], []
    for j, (sc, c, cl, cs, note) in enumerate(cands, 1):
        v, rent_y = cand_money(c, cl)
        idx = cl.get("idx")
        roi = v["roi"]
        surv_ok = cl.get("born", 0) < 3 or (cl["born"] - cl["gone"]) / cl["born"] >= 0.75
        if note or roi is None or roi <= 0:
            vd, col = "慎重", "red"
        elif (idx or 0) >= 1.2 and roi >= 0.15 and surv_ok:
            vd, col = "有望", "green"
        elif (idx or 0) >= 1.0 or roi >= 0.15:
            vd, col = "条件付き", "yellow"
        else:
            vd, col = "慎重", "red"
        verdicts.append((vd, j, cl, c, v))
        unit = "か所" if c == "33" else "人分"
        short_s = "—" if "short" not in cl else f'{cl["short"]:,.0f}{unit}' if cl["short"] >= 0.5 else "不足なし"
        ex_rows.append(f'<tr><th>{j}</th><td>{E("・".join(short(x) for x in cs))}</td><td>{E(cl["towns"][0])}周辺</td>'
                       f'<td>{f"{idx:.2f}倍" if idx else "—"}</td>'
                       f'<td>{short_s}</td>'
                       f'<td>月{rent_y / 12 / (GH_HOUSES if c == "33" and c == mc else 1):,.0f}万円</td><td>{v["need"]:,.0f}万円</td><td>{pct(roi)}</td><td>{yrs(v["back"])}</td>'
                       f'<td><span class="dot {col}"></span><b>{vd}</b></td></tr>')
    best = next((x for x in verdicts if x[0] == "有望"), next((x for x in verdicts if x[0] == "条件付き"), None))
    ex = ['<section class="pb"><h2>§総合判断</h2>']
    concl = []
    if best:
        vd, j, cl, c, v = best
        concl.append(f'<b>立地：</b>{E(short(c))}は<b>{E(cl["towns"][0])}周辺</b>での開設が最も有望である（{j}位、判断は「{vd}」）。'
                     + (f'新規開設後も、需給指数は市平均の{cl["idx"]:.2f}倍となる。' if cl.get("idx") else ''))
    elif cands and all(x[4] for x in cands):
        concl.append(f'<b>立地：</b>需給の面では{E(cands[0][2]["towns"][0])}周辺が最も有望だが、いずれの候補地にも市の規制があるため「慎重」とした（{E(cands[0][4])}）。'
                     'まず市の公募や事前協議の予定を確認する必要がある。')
    elif cands:
        concl.append('<b>立地：</b>標準ケースでは、いずれの候補地も「慎重」の判断となる。立地の選定より先に、受け入れ対象・加算・家賃などの収支の前提を見直す必要がある。')
    else:
        concl.append('<b>立地：</b>市の規制により新規開設ができないか、需要に対して供給が明らかに不足している地区がない。他のサービスや近隣の市区町村での開設も検討の余地がある。')
    concl.append(f'<b>資金：</b>{E(short(mc))}（定員{P_["cap"]}人）の投下資本は、標準ケースで約{sd["need"]:,.0f}万円である。'
                 + (f'ROIは約{sd["roi"] * 100:.0f}%、回収期間は{yrs(sd["back"])}である。' if sd["back"] else '年間収支が赤字のため、回収できない。')
                 + (f'慎重ケースでは約{inv["慎重"]["need"]:,.0f}万円が必要となる。' if inv["慎重"]["back"] else '慎重ケースでは回収できないため、資金には十分な余裕を持たせる必要がある。'))
    concl.append(f'<b>最大のリスク：</b>利益への影響が最も大きいのは「{E(torn[0][0])}」で、年間約{max(abs(torn[0][1]), abs(torn[0][2])):,.0f}万円の差が出る。'
                 f'損益分岐点の{P_["word"]}は約{be_u * 100:.0f}%である。')
    concl.append('<b>次の手順：</b>' + ('下表の上位の候補地から順に、「現地確認チェックリスト」の10項目を確認する。' if cands else 'まず市の障害福祉課で、新規指定の受付再開の時期を確認する。'))
    ex.append('<div class="exec"><ul>' + "".join("<li>" + re.sub(r"^(<b>[^<]*</b>)(.*)$", r'\1<span class="ev">\2</span>', x, flags=re.S) + "</li>"
                                              for x in concl) + '</ul></div>')
    if ex_rows:
        ex.append('<h3>候補地ごとの判断</h3><table class="small exec-t"><tr><th>順位</th><th>サービス</th><th>場所</th><th>需給<br>指数</th><th>不足数</th>'
                  '<th>家賃の<br>目安</th><th>投下資本</th><th>ROI</th><th>回収期間</th><th>判断</th></tr>' + "".join(ex_rows) + '</table>')
    ex.append('<p class="note">判断の基準：<b>有望</b>＝需給指数1.2倍以上、ROI 15%以上、かつ周辺で最近開設された事業所の4分の3以上が継続している。'
              '<b>条件付き</b>＝需給指数1.0倍以上またはROI 15%以上（現地確認を条件とする）。<b>慎重</b>＝上記以外、標準ケースで利益が出ない、または市の規制がある。'
              '家賃の目安・投下資本・ROIは、候補地周辺の地価で家賃を補正し、標準ケースで算出した（詳細は「候補地の評価」「投資判断」の章）。'
              '「不足数」は、市平均と同じ水準で利用されると仮定した場合に、半径内で不足する定員（グループホームは事業所数）である。</p></section>')
    EXEC = "".join(ex)

    # 章ごとの結論の1文（章の見出しのすぐ下に出す）
    ratio_m = svc_view[mc][0]
    word_m = "入居率" if ed == "gh" else "利用率"
    jobr0 = (pf["hoiku"], nf["hoiku"], "保育士") if child else (pf["all"], nf["all"], "全職種")
    if ser[mc][0]:
        MSG["事業所数の推移"] = (f'{lname(mc)}は{l0}の{ser[mc][0]:,}件から{lp}には{now[mc]:,}件となり、'
                            f'{abs(now[mc] - ser[mc][0]) / ser[mc][0] * 100:.0f}%{"増加" if now[mc] >= ser[mc][0] else "減少"}した。'
                           + ("新規参入は現在も続いている" if now[mc] > ser[mc][0] else "事業所数は減少傾向にある"))
    MSG["人口当たりの事業所数"] = (f'{lname(mc)}の人口当たり事業所数は{pref}平均の{ratio_m:.2f}倍であり、'
                             + ("県平均より少なく、参入の余地がある" if ratio_m < 0.95 else "県平均とほぼ同じ水準である" if ratio_m <= 1.1 else "県平均より多く、競争は厳しい"))
    if mc in caps:
        n_, nc_, sm_, md_ = caps[mc]
        MSG["定員"] = f'{short(mc)}の定員は合計{sm_:,}人（人口1万人当たり{rate(sm_, pop):.1f}人）で、1か所当たりの定員は{md_:g}人が標準である'
    if child and util:
        MSG["放課後等デイの利用率"] = (f'利用率は約{min(util, 9.99) * 100:.0f}%で、全国平均の89%'
                                 + ("を上回る。定員はまだ不足している" if util > 0.95 else "を下回る。空きのある事業所が多い" if util < 0.85 else "とほぼ同じである"))
    if child and "65" in dem:
        d_ = dem["65"]
        MSG["10年後の需要見通し"] = (f'6〜17歳人口は2035年までに{abs(d_["k35"] / d_["k25"] - 1) * 100:.0f}%{"減少" if d_["k35"] < d_["k25"] else "増加"}する見込みで、'
                              f'必要な事業所数は{d_["rows"][-1][4]:,.0f}〜{d_["rows"][-1][5]:,.0f}か所（現在{d_["now"]:,}か所）と推計される')
    if cands and "idx" in cands[0][2]:
        cl0 = cands[0][2]
        MSG["候補地の評価"] = (f'第一候補は{E(cl0["towns"][0])}周辺である。新規に開設した後も、需給指数は市平均の{cl0["idx"]:.2f}倍となる')
    MSG["事業所の空白地域"] = (f'{short(mc)}から1km以上離れた住宅地は約{maps[mc]["live_far"]:.0f}%'
                          + ("あり、空白地域はまだ残っている" if maps[mc]["live_far"] >= 10 else "で、空白地域はほとんどない"))
    if ed != "gh" and mc in diff:
        (sat_, sun_, late_, _, _), (nsat_, nsun_, nlate_, _, _), _ = diff[mc]
        if sat_ is not None and sun_ is not None:
            gap_ = min([("土曜", sat_ - nsat_), ("日曜", sun_ - nsun_)] + ([("夕方", late_ - nlate_)] if late_ is not None else []), key=lambda t: t[1])
            MSG["差別化のポイント"] = (f'土曜に営業する事業所は{sat_ * 100:.0f}%、日曜は{sun_ * 100:.0f}%（全国{nsat_ * 100:.0f}%・{nsun_ * 100:.0f}%）。'
                                 + (f'{gap_[0]}の営業で差別化できる余地がある' if gap_[1] < -0.03 else '営業日よりも、受け入れ対象や支援内容で差別化を図る地域である'))
    MSG["直近半年の新規事業所"] = f'直近半年で、{short(mc)}は{len(newl[mc])}件が新規登録され、{len(gone[mc])}件が登録抹消となった'
    if mc in surv:
        (_, _, nb_, bg_), (_, _, _, nbg_) = surv[mc]
        if bg_ is not None:
            MSG["新規事業所の継続状況"] = (f'新規開設された{short(mc)}のうち{bg_ * 100:.0f}%が廃止・休止等となっている（全国{nbg_ * 100:.0f}%）。'
                                    + ("事業の継続が難しい地域である" if bg_ > nbg_ + 0.05 else "事業を継続しやすい地域である" if bg_ < nbg_ - 0.05 else "全国並みの水準である"))
    MSG["運営法人"] = (f'大手法人（全国{CHAIN_MIN}か所以上）の割合は約{chain_share * 100:.0f}%で、'
                   + ("地域の中小法人が中心の市である" if chain_share < 0.3 else "大手法人の存在感が大きい市である"))
    MSG["人材確保"] = (f'{pref}の{jobr0[2]}の有効求人倍率は{jobr0[0]:.2f}倍（全国{jobr0[1]:.2f}倍）である。'
                   + ("採用活動は開設の半年前から始める必要がある" if jobr0[0] >= 2 else "採用環境は比較的良好である"))
    MSG["の収支試算"] = (f'定員{P_["cap"]}人の場合、年間収支は{base_p:+,.0f}万円、損益分岐点の{word_m}は約{be_u * 100:.0f}%である'
                     + ('。収支は住居数と入居者の障害支援区分によって大きく変わる' if ed == "gh" else ''))
    if child:
        MSG["児童発達支援を併設する場合の収支試算"] = (f'児童発達支援（定員10人）の年間収支は{oth["63"]["bal"]:+,.0f}万円、'
                                          f'損益分岐点の利用率は約{oth["63"]["be"] * 100:.0f}%である')
    MSG["投資判断"] = (f'必要資金は約{sd["need"]:,.0f}万円である。'
                   + (f'ROIは約{sd["roi"] * 100:.0f}%で、約{sd["back"] // 12}年で回収できる' if sd["back"] else '標準ケースでは赤字のため回収できない'))
    MSG["5年間の損益計画"] = (f'1年目の利益は{pl[0]["pr"]:+,.0f}万円で、' + (f'{first_black}年目に黒字化する見込みである' if first_black else '5年以内には黒字化しない'))
    MSG["リスクと対策"] = f'最大のリスクは「{E(torn[0][0].split("（")[0])}」である。多くのリスクは開設前の確認で軽減できる'
    MSG["現地確認チェックリスト"] = 'データだけでは分からない点を、開設を決める前に10項目で確認する'
    vd0 = best[0] if best else "慎重"
    MSG["経営理論から見た開業判断"] = f'代表的な8つの経営理論で検証しても、結論（{vd0}）は変わらない。ただし、理論ごとに注意すべき点は異なる'

    # 4つのポイント（イシューツリー）
    def mark(v):
        return {"○": '<span class="dot green"></span>○', "△": '<span class="dot yellow"></span>△', "×": '<span class="dot red"></span>×'}[v]
    q1 = "○" if ratio_m < 0.95 or (child and util and util > 0.95) else "△" if ratio_m <= 1.1 else "×"
    a1 = (f'{lname(mc)}の人口当たり事業所数は{pref}平均の{ratio_m:.2f}倍'
          + (f'、利用率は約{min(util, 9.99) * 100:.0f}%（全国89%）' if child and util else '') + '。'
          + (f'6〜17歳人口は2035年までに{abs(dem["65"]["k35"] / dem["65"]["k25"] - 1) * 100:.0f}%{"減少" if dem["65"]["k35"] < dem["65"]["k25"] else "増加"}する見込み。' if child and "65" in dem else ''))
    if cands and "idx" in cands[0][2]:
        q2 = "○" if best and best[0] == "有望" else "△"
        a2 = (f'第一候補は{E(cands[0][2]["towns"][0])}周辺（需給指数{cands[0][2]["idx"]:.2f}倍）。'
              + (MSG.get("差別化のポイント", "").split("。")[-1] + "。" if MSG.get("差別化のポイント") else ""))
    else:
        q2, a2 = "×", "需要に対して供給が明らかに不足している地区はない。サービス内容での差別化が必要である。"
    q3 = "○" if sd["back"] and sd["roi"] >= 0.15 else "△" if sd["back"] else "×"
    a3 = (f'必要資金は約{sd["need"]:,.0f}万円。' + (f'ROIは約{sd["roi"] * 100:.0f}%、回収期間は{yrs(sd["back"])}。' if sd["back"] else '標準ケースでは赤字のため回収できない。')
          + ('慎重ケースでは回収の見込みが立たない。' if not inv["慎重"]["back"] else ''))
    rule0 = caps_open.get(mc)
    q4 = "×" if rule0 and rule0["level"] == "stop" else "△" if rule0 or jobr0[0] >= 2 or ed == "gh" else "○"
    a4 = (f'{jobr0[2]}の有効求人倍率は{jobr0[0]:.2f}倍。' + (f'市の規制あり（{E(rule0["text"])}）。' if rule0 else
          '2027年4月から、グループホームは総量規制の対象となり得る。' if ed == "gh" else '現時点で市の規制はない。'))
    rows_q = [("① 需要", "人口当たり事業所数・利用率・将来人口", q1, a1, "第1部"),
              ("② 立地と競合", "需給指数・競合・差別化・継続状況", q2, a2, "第2部"),
              ("③ 収支と資金", "収支・投下資本・ROI・回収期間", q3, a3, "第3部"),
              ("④ 人材とリスク", "人材・市の規制・リスク", q4, a4, "第4部")]
    ISSUE = ('<section class="pb"><h2>§4つのポイント</h2>'
             f'<p class="msg">「{E(city)}で{E(lname(mc))}をどこに開設すべきか」を、4つのポイントにまとめた</p>'
             f'<p>本レポートの目的は、{E(city)}で{E(lname(mc))}を開設する場所と、その是非を判断することにある。'
             '判断に必要な要素を4つのポイントに整理し、それぞれをデータで検証した。前章の総合判断は、この4つの結果をまとめたものである。</p>'
             '<table class="small txt2 issue"><tr><th>ポイント</th><th>主なデータ</th><th>評価</th><th>根拠</th><th>詳細</th></tr>'
             + "".join(f'<tr><td><b>{a}</b></td><td>{b}</td><td style="white-space:nowrap">{mark(c_)}</td><td>{d}</td><td>{e}</td></tr>' for a, b, c_, d, e in rows_q)
             + '</table><p class="note">○＝問題なし、△＝条件付き（確認したうえで進める）、×＝現状では難しい。第5部では、'
             '代表的な経営理論に当てはめ、この結論と異なる見方がないかを検証している。</p></section>')

    # リスクと対策
    jobr = (pf["hoiku"], nf["hoiku"], "保育士") if child else (pf["all"], nf["all"], "全職種")
    d10 = prof(u=std_u - 0.10) - base_p
    rsk = [("利用者が想定どおり集まらない", "中", f'{P_["word"]}が10ポイント下がると年{d10:+,.0f}万円',
            f'開業の3か月前から相談支援事業所{"・学校" if child else ""}に営業し、見学会を開く。開業1年で損益分岐点（約{be_u * 100:.0f}%）に届かない場合は、受け入れ対象や営業時間を見直す。'),
           ("職員の採用難・離職", "高" if jobr[0] >= 2 else "中", f'人件費が10%上がると年{-P_["staff"] * 0.1:+,.0f}万円。人員が不足すると定員まで受け入れられない',
            f'{E(pref)}の{jobr[2]}の有効求人倍率は{jobr[0]:.2f}倍。開業の4〜6か月前から採用活動を始め、処遇改善加算を取得して給与を地域の相場以上に設定する。'),
           ("報酬改定（3年ごと、次回は2027年度）", "中", f'1人1日当たり報酬が5%下がると年{prof(pd_=std_pd * 0.95) - base_p:+,.0f}万円',
            '基本報酬だけに頼らず、算定できる加算（専門職の配置・送迎・医療的ケアなど）を開業時から計画に組み込む。'),
           ("近隣での競合の新規開設", "高" if len(newl[mc]) >= 5 else "中", f'{P_["word"]}が5ポイント下がると年{prof(u=std_u - 0.05) - base_p:+,.0f}万円',
            f'{E(city)}では直近半年で{E(short(mc))}が{len(newl[mc])}か所新規登録された。'
            + ('受け入れ対象や支援内容で差別化を図る。' if ed == "gh" else '営業日・営業時間・受け入れ対象で差別化を図る（「差別化のポイント」の章）。')),
           ("市の総量規制・指定の制限", "高" if caps_open.get(mc) else "中" if ed == "gh" else "低", "開業できない",
            '物件の契約前に、市の障害福祉課で障害福祉計画の見込み量と事前協議の時期を確認する。'),
           ("家賃・改修費の想定超過", "中", (f'家賃が30%上がると、空室分の負担増で年{-P_["rent"] * 0.3 * (1 - std_u):+,.0f}万円（入居者の家賃も上がる）' if ed == "gh" else f'家賃が30%上がると年{-P_["rent"] * 0.3:+,.0f}万円'),
            '契約前に、用途変更・消防設備・バリアフリー工事の費用を、建築士と消防署に確認する。'),
           ("実地指導・監査による報酬返還", "低", "過去にさかのぼって返還を求められることがあり、影響は大きい",
            '人員基準、個別支援計画、支援記録を毎月点検する。不明な点は事前に市に確認する。')]
    if child and "65" in dem:
        kd = dem["65"]["k35"] / dem["65"]["k25"] - 1
        rsk.append(("子どもの数の減少", "中" if kd < -0.05 else "低", f'6〜17歳人口は2035年までに{kd * 100:+.0f}%（社人研推計）',
                    '児童発達支援・保育所等訪問支援など、未就学児向けのサービスにも展開できるよう備える。'))
    lvc = {"高": "red", "中": "yellow", "低": "green"}
    h.append('<section class="pb"><h2>§リスクと対策</h2>'
             '<p>開業時に想定される主なリスクを、発生可能性と影響度（年間利益の変化）で整理した。影響度は、標準ケースから1つの条件だけを変えた場合の変化である。</p>'
             + keypts([f'影響が最も大きいのは「{E(torn[0][0])}」である。', '多くのリスクは、開業前に市・物件・紹介元・採用市場を確認することで軽減できる。'])
             + '<table class="small txt2 risk"><tr><th>リスク</th><th>発生可能性</th><th>影響度</th><th>事前の対策</th></tr>'
             + "".join(f'<tr><td><b>{a}</b></td><td><span class="dot {lvc[b]}"></span>{b}</td><td>{c_}</td><td>{d}</td></tr>' for a, b, c_, d in rsk)
             + '</table><p class="note">発生可能性は、この市のデータ（求人倍率・新規登録数・市の規制）と一般的な目安から設定したもので、確率を示すものではない。</p></section>')

    # 経営理論から見る
    mc = ED["main"]
    sv0 = surv.get(mc, ((0, None, 0, None), (0, None, 0, None)))
    (_, keep_c, born_c, gone_c), (_, _, _, gone_n) = sv0
    lev = torn[0]
    slow = inv["慎重"]
    sm0 = SITE.get(mc)
    top0 = cands[0] if cands else None
    ref = sum(now.get(k, 0) for k in (["70", "52"] if child else ["52"]))
    job = (pf["hoiku"], nf["hoiku"], "保育士") if child else (pf["all"], nf["all"], "全職種")
    th = []

    def card(title, idea, src, here, other):
        th.append(f'<div class="theory"><h3>{title}</h3><p class="idea">{idea}<span class="srcl">（{src}）</span></p>'
                  f'<p><b>{E(city)}への当てはめ：</b>{here}</p><p class="other"><b>別の見方：</b>{other}</p></div>')
    if sm0 and top0:
        here1 = (f'需給指数が最も高いのは{E(top0[2]["towns"][0])}周辺で、新規に開設した後も市平均の約{top0[2].get("idx", 1):.1f}倍となる。'
                 f'市内の上位10地区は、市平均の{sm0["areas"][9]["idx"] if len(sm0["areas"]) > 9 else sm0["areas"][-1]["idx"]:.1f}〜{sm0["areas"][0]["idx"]:.1f}倍である。')
    else:
        here1 = '町丁別の人口データが揃っていないため、空白地域の地図を目安とした。'
    card("1. 商圏分析（ハフモデル）",
         "消費者は、距離が近く規模の大きい店舗ほど選びやすく、需要は周辺の店舗に確率的に分散するという理論である。"
         "出店前に、商圏内の需要と既存の供給を比較することが基本となる。",
         "デイビッド・ハフ、1963年の論文。日本でもスーパーやドラッグストアの出店調査に広く用いられている",
         here1,
         "障害福祉サービスは送迎があり、口コミや紹介で利用先が決まることも多いため、小売業ほど距離の影響は大きくない。点数は、現地調査の優先順位を決める目安として用いるのが適切である。")
    card("2. ランチェスター戦略とドミナント出店",
         "経営資源の少ない企業は、広い地域に分散して出店するよりも、地域を絞り込み、その地域で1位を目指すべきだという理論である。"
         "セブン-イレブンが特定の地域に集中して出店する「ドミナント戦略」がよく知られている。",
         "フレデリック・ランチェスターの法則を田岡信夫が販売戦略として体系化したもの",
         f'{E(city)}の事業所の約{chain_share * 100:.0f}%は、全国で{CHAIN_MIN}か所以上を運営する大手法人である。'
         '1か所目の立地は、近隣に2か所目・3か所目を展開できるか（送迎車両や職員を融通できるか）も考慮して選ぶと、中長期的な競争力が高まる。',
         "空白地域は競合が少ない一方、地域での認知を得るまでに時間がかかる。既に事業所が集まっている地域は、需要が実証されているとも言える（7.を参照）。")
    card("3. 売上最大・経費最小（稲盛和夫）",
         "利益は売上と経費の差にすぎないため、売上を最大に、経費を最小にするという原則を、日々の数字で徹底すべきだという考え方である。",
         "稲盛和夫『稲盛和夫の実学―経営と会計』日本経済新聞社、1998年",
         f'この事業の費用の大半は人件費と家賃であり、利用が少なくても発生する。「投資判断」の章の感応度分析では、利益への影響が最も大きいのは<b>{E(lev[0])}</b>で、'
         f'年間約{max(abs(lev[1]), abs(lev[2])):,.0f}万円の差が出る。売上面では、{"利用率" if ed != "gh" else "入居率"}を毎月確認し、早めに手を打つことが最も効果的である。',
         "経費を削りすぎると、職員の離職やサービスの質の低下を招き、かえって利用者が減る。人件費は「抑える」よりも「定着させる」ほうが、結果として費用を抑えられることが多い。")
    card("4. ダム式経営と安全域（松下幸之助・ベンジャミン・グレアム）",
         "ダムに水を蓄えるように、資金・人材・設備に余裕を持たせておけば、景気の変動や想定外の事態に左右されないという考え方である。"
         "投資の分野では、見通しが外れても損失を抑えられる余裕（安全域）を確保して判断するという考え方が近い。",
         "松下幸之助『実践経営哲学』PHP研究所、1978年。ベンジャミン・グレアム『賢明なる投資家』1949年",
         f'標準ケースの投下資本は約{sd["need"]:,.0f}万円だが、慎重ケースでは'
         + (f'約{slow["need"]:,.0f}万円が必要となる' if slow["back"] else f'回収できず、資金は減り続ける')
         + '。資金計画は標準ケースではなく慎重ケースを前提に立て、運転資金の借入枠も開業前に確保しておくのが安全である。',
         "借入が過大になると、利息と返済の負担が重くなる。手元資金の水準は、開業後1年間の実績を見て見直すのが現実的である。")
    card("5. 事業の目的は顧客の創造（ピーター・ドラッカー）",
         "事業は、顧客が誰で、顧客が何に価値を感じるかを考えることから始まるという考え方である。",
         "P・F・ドラッカー『現代の経営』1954年、『マネジメント』1973年",
         f'この事業の顧客は、利用者本人と家族だけでなく、事業所を紹介する関係者でもある。{E(city)}には'
         + (f'障害児相談支援・計画相談支援' if child else '計画相談支援')
         + f'の事業所が{ref:,}か所あり、そこに所属する相談支援専門員がサービス等利用計画を作成する。開業前にあいさつに回り、'
         '受け入れ可能な対象者を明確に伝えることが、最初の利用者の獲得につながる。',
         "紹介に頼りすぎると、紹介元の事情で利用者数が左右される。ホームページや地域の集まりを通じて、家族に直接知ってもらう経路も並行して整えるのが望ましい。")
    card("6. ファイブフォース分析（マイケル・ポーター）",
         "業界の収益性は、①既存企業間の競争、②新規参入の脅威、③代替品の脅威、④買い手の交渉力、⑤売り手（供給者）の交渉力、の5つの要因で決まるという理論である。",
         "マイケル・E・ポーター『競争の戦略』1980年",
         f'障害福祉では価格（報酬）を国が定めるため、価格競争は起こらない。代わりに影響が大きいのは、⑤の供給者に当たる<b>人材（職員）</b>の市場であり、'
         f'{E(pref)}の{job[2]}の有効求人倍率は{job[0]:.2f}倍（全国{job[1]:.2f}倍）である。②の新規参入は、{E(city)}で直近半年に{n_new}か所であった。'
         'また、国が3年ごとに報酬を改定すること（制度変更）も、他の業界にはない大きな要因である。',
         "価格で競争できない分、サービスの質と評判で差がつきやすい業界でもある。職員が働きやすい事業所は、利用者からも選ばれやすい。")
    card("7. ブルー・オーシャン戦略",
         "競争のない新しい市場（ブルー・オーシャン）を創出すれば、競争せずに利益を上げられるという理論である。一方で、競争のある市場は需要が実証された市場であるという見方もある。",
         "W・チャン・キム、レネ・モボルニュ『ブルー・オーシャン戦略』2005年",
         (f'{E(city)}で2022年以降に新規登録された{E(short(mc))}は{born_c}か所で、そのうち{gone_c * 100:.0f}%が廃止・休止等となっている（全国は{gone_n * 100:.0f}%）。'
          if born_c and gone_c is not None and gone_n is not None else '')
         + '候補地の表で、周辺の新規事業所が継続している地域は需要が実証された地域、最近の新規開設がない地域は需要が未検証の地域と言える。',
         "空白地域には、まだ誰も気づいていない需要がある場合もあれば、そもそも需要がない場合もある。近隣の事業所の空き状況や待機者の数を現地で聞けば、どちらかを見極められる。")
    card("8. 撤退基準の事前設定（柳井正『一勝九敗』）",
         "新しい事業は失敗するのが普通であり、失敗を早く認め、損失が小さいうちに次の手を打つことが重要だという考え方である。",
         "柳井正『一勝九敗』新潮社、2003年",
         f'開業前に、「開業から1年で{"利用率" if ed != "gh" else "入居率"}が○%に届かなければ、立地やサービス内容を見直す」という基準を定めておく。'
         f'目安は、損益分岐点の約{be_u * 100:.0f}%である。',
         "福祉事業は利用者の生活を支えているため、事業をやめる場合は他の事業所への引き継ぎが必要となる。撤退基準は、事業をやめる基準というより、立て直しに着手する基準として使うのが現実的である。")
    h.append('<section class="pb"><h2>§経営理論から見た開業判断</h2>'
             '<p>この章では、代表的な経営理論を本レポートのデータに当てはめて検証する。どの理論が正しいかではなく、'
             '<b>同じデータでも見方によって判断が変わり得る</b>ことを確認するのが目的である。各理論には「別の見方」も併記した。</p>'
             + "".join(th)
             + '<div class="method"><b>本レポートで分かること・分からないこと</b><ul>'
             '<li>分かること：市内のどこで需要に対して供給が不足しているか、市全体の事業所数の推移、必要資金と回収の見通し、競合と人材確保の状況。</li>'
             '<li>分からないこと：物件の有無、近隣事業所の空き状況と評判、相談支援専門員が求める事業所像、自社の強み。'
             'これらは、現地を歩き、関係者に会って確認するしかない。本レポートは、現地で何をどの順に確認するかを決めるための資料である。</li></ul>'
             '<p class="note">各書籍の内容は、ジモふくが要点をまとめたものである。</p></div></section>')

    # ほかのサービスの試算
    allm = {"65": dict(cap=CAP10, util=per["標準"][0], pd=per["標準"][1], rv=CAP10 * DAYS * per["標準"][0] * per["標準"][1] / 1e4,
                       st=staff, ct=cost, bal=sim_year(*per["標準"], cost), be=be, days=DAYS, pr=price),
            "63": oth["63"], "46": oth["46"], "33": gh}
    show = ["63"] if child else []  # 版のサービスだけ（ほかのサービスの話は入れない）
    rows_o = "".join(
        f'<tr><th>{E(short(c))}</th><td>{o["cap"]}人</td><td>{o["util"] * 100:.0f}%</td><td>{o["pd"]:,.0f}円</td><td>{o["rv"]:,.0f}万円</td>'
        f'<td>{o["ct"]:,}万円</td><td><b>{o["bal"]:+,.0f}万円</b></td><td>約{o["be"] * 100:.0f}%</td></tr>' for c in show for o in [allm[c]])
    pre_o = {
        "65": f'年{DAYS}日営業。職員{1 + DIRECT["65"]:.1f}人（常勤換算）で人件費年{staff:,}万円、その他の費用年{other:,}万円（家賃を含む）。1単位{price:.2f}円',
        "63": f'年{OTHERS["63"]["days"]}日営業。職員{1 + DIRECT["63"]:.1f}人（常勤換算）で人件費年{oth["63"]["st"]:,}万円、その他の費用年{oth["63"]["ct"] - oth["63"]["st"]:,}万円（家賃を含む）。1単位{oth["63"]["pr"]:.2f}円',
        "46": f'年{OTHERS["46"]["days"]}日営業。職員{1 + DIRECT["46"] + DIRECT_OTHER["46"]:.1f}人（常勤換算）で人件費年{oth["46"]["st"]:,}万円、その他の費用年{oth["46"]["ct"] - oth["46"]["st"]:,}万円（家賃を含む）。1単位{oth["46"]["pr"]:.2f}円。'
              '工賃（利用者の給料）は売上と別に、作業の収入から払う前提',
        "33": f'定員{GH_HOUSE}人の住居を{GH_HOUSES}か所、入居率{GH_UTIL * 100:.0f}%。人件費年{gh["st"]:,}万円（毎晩の夜勤を含む）、その他の費用年{gh["ct"] - gh["st"]:,}万円（建物の家賃を含む）。1単位{gh_pr:.2f}円',
    }
    src_o = {"65": "福祉医療機構「2024年度 放課後等デイサービスの経営状況」", "63": f'福祉医療機構「{OTHERS["63"]["src"]}」',
             "46": f'福祉医療機構「{OTHERS["46"]["src"]}」', "33": "厚生労働省「令和7年 障害福祉サービス等経営概況調査」「令和6年度 処遇状況等調査」"}
    if show:
        h.append('<section class="pb"><h2>§児童発達支援を併設する場合の収支試算</h2>'
             f'<p>{E(city)}で児童発達支援を開設した場合の年間収支を試算した。'
             f'全国の平均的な事業所の数値を、{E(city)}の単価・給与・家賃の水準に合わせて補正している。放課後等デイとの併設や、将来の事業拡大を検討する際の目安となる。</p>'
             '<table class="small"><tr><th>サービス</th><th>定員</th><th>利用率</th><th>1人1日当たり報酬</th><th>年間売上</th><th>年間費用</th><th>年間収支</th><th>損益分岐点の利用率</th></tr>'
             + rows_o + '</table>'
             + "".join(f'<p class="contact"><b>注意（{E(short(c_))}）：</b>{E(caps_open[c_]["text"])}</p>' for c_ in show if c_ in caps_open)
             + ('<p class="contact"><b>注意（グループホーム）：</b>2027年4月からは総量規制の対象にできるようになるので、開設の前に市の計画を確かめる必要がある。</p>' if "33" in show else '')
             + '<h3>前提</h3><table class="small txt2"><tr><th>サービス</th><th>設定</th></tr>'
             + "".join(f'<tr><td>{E(short(c))}</td><td>{pre_o[c]}</td></tr>' for c in show) + '</table>'
             f'<p class="note">出典：{E("、".join(src_o[c] for c in show))}。本試算は検討のたたき台である。</p></section>')

    # 10年後の需要
    if dem and child:
        h.append('<section class="pb"><h2>§10年後の需要見通し</h2>'
                 f'<p>国立社会保障・人口問題研究所の将来推計人口（2023年推計）をもとに、{E(city)}の対象年齢の子どもの数を推計し、'
                 'サービスの利用量（1か月の延べ利用日数）と、必要な事業所数を見積もった。</p>')
        for c, d_ in dem.items():
            h.append(f'<h3>{E(SERVICES[c][0])}（{"6〜17歳" if c == "65" else "0〜5歳"}）</h3>'
                     '<table class="small"><tr><th>年</th><th>子どもの数（推計）</th><th>利用量（低位）</th><th>利用量（高位）</th>'
                     '<th>必要な事業所数（低位）</th><th>必要な事業所数（高位）</th></tr>'
                     + "".join(f'<tr><th>{y}年</th><td>{kv:,.0f}人</td><td>{lo:,.0f}人日</td><td>{hi:,.0f}人日</td><td>{nl:,.0f}か所</td><td>{nh:,.0f}か所</td></tr>'
                               for y, kv, lo, hi, nl, nh in d_["rows"]) + '</table>')
            n35l, n35h = d_["rows"][-1][4], d_["rows"][-1][5]
            h.append(f'<p>現在の事業所数は<b>{d_["now"]:,}か所</b>である。2035年に必要な事業所数は<b>{n35l:,.0f}〜{n35h:,.0f}か所</b>と見込まれ、'
                     + (f'現在より<b>{n35l - d_["now"]:+,.0f}〜{n35h - d_["now"]:+,.0f}か所</b>の差となる。' )
                     + ("子どもの数は減るものの、利用する子どもの割合が上昇を続ける限り、需要が大きく減ることはないとみられる。" if d_["k35"] < d_["k25"] and n35h >= d_["now"] else
                        "子どもの数の減少により、10年後には事業所が過剰となる可能性がある。開設する場合は、立地やサービス内容で選ばれる理由を明確にすることが一層重要となる。" if n35h < d_["now"] else
                        "子どもの数・需要ともに増加が見込まれる地域である。")
                     + '</p>')
        h.append('<p class="note">低位：サービスを利用する子どもの割合が現在のまま推移すると仮定（子どもの数の変化のみを反映）。'
                 '高位：利用する子どもの割合が、2021年以降の事業所数の伸びから子どもの数の変化を差し引いたペース（上限年8%）で今後5年間上昇し、その後は横ばいと仮定。'
                 '1事業所当たりの利用量は、現在の1事業所当たり定員×月間営業日数×89%とした。'
                 + ('放課後等デイの2025年の利用量は、市の実績値をもとに子どもの数の変化で補正した。' if util else '2025年の利用量は、現在の事業所の利用率を89%と仮定して算出した。')
                 + '子どもの数は、5歳階級の推計値を年齢別の構成比で按分した概数である。</p></section>')

    # 11. 地図
    for i, c in enumerate(SV):
        m = maps[c]
        h.append(f'<section class="pb"><h2>{"§事業所の空白地域" if i == 0 else "§§事業所の空白地域（続き）"}</h2>'
                 f'<h3>{E(city)}の地図：{E(SERVICES[c][0])}</h3>'
                 f'<p>下の地図は{E(city)}の全域である（黒い線が市の境界、上が北）。色は、最寄りの{E(short(c))}までの直線距離を示す。点は{E(short(c))}の事業所である（市外の近隣事業所を含む）。'
                 '<b style="color:#b3392c">赤い数字</b>は「候補地の評価」の章の順位、点線の丸（A、B…）は、住宅地でありながら事業所から1km以上離れている地域である。</p>'
                 f'<div class="mapbox">{map_svg(*m["args"], labels=LABELS.get((pi, city), ()), picks=[(j, cl) for j, (_, c0, cl, cs0, _) in enumerate(cands, 1) if c in cs0])}</div><p class="legend">'
                 + "".join(f'<span class="lg"><i style="background:{col}"></i>{lab}</span>' for _, col, lab in BANDS)
                 + '</p>')
        h.append(f'<p>事業所から1km以上離れた地域は、市の面積の<b>約{m["far"]:.0f}%</b>、住宅地の<b>約{m["live_far"]:.0f}%</b>を占める。</p>')
        if m["clusters"]:
            h.append('<table class="small cl"><tr><th></th><th>おおよその場所</th><th>面積</th><th>最寄り事業所までの最大距離</th></tr>'
                     + "".join(f'<tr><th>{j}</th><td>{E("・".join(cl["towns"]) or "—")}周辺</td><td>約{cl["a"]:.1f}km²</td><td>約{cl["md"]:.1f}km</td></tr>'
                               for j, cl in zip("ABCDEFGH", m["clusters"])) + '</table>')
        else:
            h.append('<p>住宅地で事業所から1km以上離れたまとまった地域は見られなかった。</p>')
        h.append('</section>')

    # 付録：経営理念・行動規範のひな形
    who_v = {"child": ("子ども", "子どもと家族", "放課後や休みの日"), "gh": ("入居する人", "入居する人と家族", "毎日の暮らし"),
             "b": ("利用する人", "利用する人と家族", "働くこと")}[ed]
    ex = {"child": ["一人一人の「できた」を、家族と地域といっしょに育てる。",
                    "どの子も、自分らしく過ごせる放課後を、この町に。",
                    "子どもの今日の安心と、将来の自立を、ていねいに支える。"],
          "gh": ["その人らしい暮らしを、地域の中で、いっしょにつくる。",
                 "「ここが自分の家だ」と思える毎日を支える。",
                 "安心して住み続けられる場所を、この町に増やす。"],
          "b": ["働くよろこびと、地域とのつながりを、仕事を通して届ける。",
                "一人一人の「できる仕事」を見つけ、工賃と自信を育てる。",
                "地域に必要とされる仕事で、利用する人の暮らしを豊かにする。"]}[ed]
    # 現地確認チェックリスト
    top_t = [cl["towns"][0] for _, _, cl, _, _ in cands[:3]]
    rv0 = rivals(S, periods, cands[0][1], cands[0][2]["x"], cands[0][2]["y"], RADIUS.get(cands[0][1], 2.0))[:3] if cands else []
    sps_n = [p_[2] for p_ in SCHOOLS["特別支援学校"] if p_[3] == pref and inside(rings, p_[0], p_[1])]
    chk = [
        (f'候補地（{E("・".join(top_t)) or "空白地"}）を、{"放課後の送迎の時間（14〜18時）" if child else "朝と夕方の通う時間" if ed == "b" else "夕方と夜"}に、車と徒歩で回る',
         '道路の混雑、駐車スペース、周辺の住宅の状況を確認する。地図上の点数では分からない通いやすさを把握する。'),
        ('近隣の同業事業所の見学会・説明会に参加する' + (f'（例：{E("、".join(o["name"][:16] for _, _, o, _ in rv0))}）' if rv0 else ''),
         '空き状況、待機者の有無、受け入れている利用者像を確認する。訪問先は「候補地周辺の競合」の章の一覧から選ぶ。'),
        ('相談支援事業所を2〜3か所訪問する', f'市内に{ref:,}か所ある。どのような利用者の受け皿が不足しているか、どのような事業所なら紹介したいかを聞く。開業当初の利用者の多くは、ここからの紹介である。'),
        (('特別支援学校・小学校の下校時の様子を確認する' if child else '特別支援学校の進路指導担当や、ハローワークの障害者窓口で話を聞く' if ed == "b" else '日中活動先（生活介護・就労B型）と、病院・買い物の場所を確認する'),
         (f'市内の特別支援学校：{E("、".join(sps_n[:4]))}。' if child and sps_n else '') + '送迎の範囲と、利用者の1日の生活の流れが無理なく組めるかを確認する。'),
        ('市の障害福祉課で、障害福祉計画の見込み量と事前協議の時期を確認する', '総量規制の有無や新規指定の受付時期は、物件の契約前に確認しておく必要がある。'),
        ('物件を3件以上比較する', '広さ、用途変更の要否、消防設備、段差を確認する。周辺の募集家賃を、「投資判断」の章の家賃の目安と比較する。'),
        ('近隣の求人の給与を3件以上確認する', f'{"保育士・児童指導員" if child else "生活支援員・職業指導員" if ed == "b" else "世話人・生活支援員"}の求人を、求人サイトやハローワークで調べる。採用に必要な給与水準が、試算の人件費と合っているかを確認する。'),
        ('送迎ルートと車両台数を決める' if ed != "gh" else '近隣住民への説明の方法を決める',
         '受け入れ可能な人数は、送迎車両の台数と運転手の数で決まる。' if ed != "gh" else 'グループホームは生活の場であるため、開設前に近隣へあいさつし、緊急時の連絡先を伝えておくことが望ましい。'),
        ('金融機関に資金計画を相談する', f'投下資本の目安は、標準ケースで約{sd["need"]:,.0f}万円、慎重ケースで約{inv["慎重"]["need"]:,.0f}万円である。5年間の損益計画を持参し、日本政策金融公庫や地元の信用金庫に相談する。'),
        ('見直しの基準を事前に決めておく', f'例：「開業から1年で{P_["word"]}が損益分岐点の{be_u * 100:.0f}%に届かなければ、受け入れ対象・営業時間・立地を見直す」。'),
    ]
    h.append('<section class="pb"><h2>§現地確認チェックリスト</h2>'
             '<p>本レポートは、現地で何をどの順に確認するかを決めるための資料である。データだけでは分からない点は、開業を決める前に次の10項目で確認する必要がある。'
             '費用がかからず、すぐに着手できるものから順に並べた。</p>'
             '<table class="small txt2 chk"><tr><th></th><th>確認事項</th><th>確認のポイント</th></tr>'
             + "".join(f'<tr><td>□ {i}</td><td><b>{a}</b></td><td>{b}</td></tr>' for i, (a, b) in enumerate(chk, 1))
             + '</table></section>')

    h.append('<section class="pb"><h2>§付録：経営理念・行動規範のひな形</h2>'
             '<p>経営理念は、判断に迷ったときに立ち返る基準である。職員の採用や、相談支援専門員・家族への事業所の説明にも使える。'
             '以下のひな形をもとに、自社の言葉に書き換えて活用されたい。</p>'
             '<h3>経営理念の3つの要素</h3><table class="small txt2"><tr><th>要素</th><th>内容</th><th>書き方のポイント</th></tr>'
             f'<tr><td><b>使命（ミッション）</b></td><td>誰の、何のために、この事業所があるのか</td><td>{who_v[1]}を主語にし、1文で書く。</td></tr>'
             '<tr><td><b>目指す姿（ビジョン）</b></td><td>5〜10年後、この地域で何を実現したいか</td><td>「○○な町」「○○が当たり前」のように、実現したかどうかを確認できる形で書く。</td></tr>'
             '<tr><td><b>大切にすること（バリュー）</b></td><td>毎日の仕事で、何を優先するか</td><td>3〜5つに絞り、迷ったときにどちらを選ぶかが分かる言葉で書く。</td></tr></table>'
             '<h3>使命の例</h3><ul>' + "".join(f"<li>{E(x)}</li>" for x in ex) + '</ul>'
             '<h3>経営理念を作るときに考えること</h3><ol class="small">'
             f'<li>なぜこの事業を始めたいのか（きっかけとなった{who_v[0]}や家族を思い浮かべる）。</li>'
             f'<li>{E(city)}のこの地域で、まだ足りないものは何か（本レポートの「候補地の評価」や「差別化のポイント」の章が参考になる）。</li>'
             '<li>職員にどのような人であってほしいか。どのような行動はしてほしくないか。</li>'
             '<li>10年後、地域の人からどのように言われていたいか。</li></ol>'
             '<h3>行動規範のひな形（10か条）</h3><ol class="small">'
             f'<li><b>本人の思いを中心に</b>：{who_v[0]}の気持ちと選択を大切にし、本人の意思を確かめながら支援します。</li>'
             '<li><b>権利を守る</b>：虐待、不適切な関わり、身体拘束をしません。気づいたら、ためらわずに報告します。</li>'
             '<li><b>安全を最優先に</b>：ヒヤリとしたことも記録して共有し、事故を防ぎます。'
             + ('送迎では、車内の置き去りがないよう、必ず確認します。' if ed != "gh" else '夜間や災害のときの連絡と避難の手順を、全員が知っておきます。') + '</li>'
             '<li><b>秘密を守る</b>：知り得た個人の情報は、本人の同意なく外に出しません。SNS に利用者のことを書きません。</li>'
             '<li><b>正直に記録し、正しく請求する</b>：支援の記録は事実のとおりに書き、報酬は決まりのとおりに請求します。</li>'
             f'<li><b>{who_v[1]}とていねいに話す</b>：説明は分かりやすい言葉で行い、苦情や要望は改善の機会として受け止めます。</li>'
             '<li><b>チームで支える</b>：一人で抱えこまず、相談し、助け合います。ハラスメントを許しません。</li>'
             '<li><b>学び続ける</b>：研修や事例の振り返りで、支援の質を高め続けます。</li>'
             '<li><b>地域とつながる</b>：相談支援事業所、学校・医療・行政、近所の人と連携し、地域の一員として行動します。</li>'
             f'<li><b>続けられる経営をする</b>：{who_v[2]}を長く支えるために、むだを省き、職員が働き続けられる職場をつくります。</li></ol>'
             '<p class="note">このひな形は自由に使用・変更してよい。ただし、権利擁護・虐待防止・身体拘束の適正化・個人情報の取り扱いは法令や国の基準で定めがあるため、'
             '運営規程や研修計画とあわせて整備する必要がある。</p></section>')

    # 9. 出典
    h.append(f'''<section class="pb"><h2>§出典と注記</h2><ul class="src">
<li>事業所：独立行政法人福祉医療機構 WAM NET「障害福祉サービス等情報公表システムデータのオープンデータ」（{l0}〜{lp}、半年ごと）をもとに ジモふくが集計。</li>
<li>{E(B.POP_SOURCE)}（{E(B.POP_ASOF[0])}）。</li>
<li>地図の境界：国土交通省「国土数値情報（行政区域データ）」をもとに作成。</li>
<li>事業所数は、WAM NETに情報が登録されている事業所の数である。休止中の事業所や届出が未提出の事業所があるため、実際の数と異なる場合がある。</li>
<li>1つの事業所が複数の市区町村でサービスを提供している場合は、それぞれの市区町村で1件として数えた。</li>
<li>地図上の距離は直線距離であり、道路距離ではない。「住宅地」は、他のサービスの事業所が500m以内にある場所とした。</li>
<li>法人種別は、法人名に含まれる語（株式会社・社会福祉法人など）から判定したおおよその数である。</li>
<li>地域区分：{E(FACTS["grade_source"])}。単価：{E(FACTS["unit_price_source"])}。</li>
<li>求人倍率：{E(FACTS["hoiku_source"])}、{E(FACTS["all_source"])}。最低賃金：{E(FACTS["wage_source"])}。</li>
<li>{"保育士の給与：" + E(FACTS["pay_source"]) if child else "地域の給与水準：" + E(FACTS["care_source"])}。家賃：{E(FACTS["rent_source"])}。</li>
<li>人件費：厚生労働省「令和6年度 障害福祉サービス等従事者処遇状況等調査」（職種別・サービス別の平均給与）、同「令和7年 障害福祉サービス等経営概況調査」（常勤・非常勤の割合）。</li>
<li>学校と駅の位置：国土交通省「国土数値情報（学校 2021年、鉄道 2023年）」。</li>
<li>町丁別・年齢別人口：総務省統計局「令和2年国勢調査 小地域集計（町丁・字等別 年齢5歳階級別人口）」と同「町丁・字等別境界データ」（e-Stat）を、同「令和7年国勢調査 人口等基本集計（第2-7表 市区町村別 年齢5歳階級別人口）」で補正。</li>
<li>候補地ごとの家賃の目安：国土交通省「国土数値情報（地価公示 2026年）」をもとに、候補地周辺と市全体の地価の差から ジモふくが推計。</li>
<li>候補地の評価方法：Huff, D. L. (1963) "A Probabilistic Analysis of Shopping Center Trade Areas", <i>Land Economics</i> 39(1), 81–90／Luo, W. &amp; Wang, F. (2003) "Measures of spatial accessibility to health care in a GIS environment", <i>Environment and Planning B</i> 30(6), 865–884／Luo, W. &amp; Qi, Y. (2009) "An enhanced two-step floating catchment area (E2SFCA) method for measuring spatial accessibility to primary care physicians", <i>Health &amp; Place</i> 15(4), 1100–1107。</li>
{"<li>放課後等デイの利用量：" + (E(hd["src"]) + "（" + E(hd["url"]) + "）" if hd else "市の公表資料では確認できなかった") + "。</li>" if child else ""}
{"<li>収支試算：厚生労働省「令和7年 障害福祉サービス等経営概況調査」（共同生活援助 介護サービス包括型・日中サービス支援型）と、2024年度報酬改定の単位数をもとに ジモふくが試算。</li>" if ed == "gh" else "<li>収支試算：" + E(FACTS["other_source"]) + "をもとに ジモふくが試算。</li>"}
<li>将来推計人口：国立社会保障・人口問題研究所『日本の地域別将来推計人口（令和5年推計）』。</li>
</ul><p class="contact">本レポートの数値は、「ジモふく調べ」と明記すれば自由に引用できる。他のサービスの試算や詳細な調査のご相談は、https://jimofuku.jp/ まで。</p></section>''')
    return number_sections(arrange("\n".join(h).replace("<!--EXEC-->", EXEC + ISSUE)))


PARTS = [
    (None, ["結論", "総合判断", "4つのポイント"]),
    ("第1部　需要", ["事業所数の推移", "人口当たりの事業所数", "区別の事業所数", "近隣市区町村との比較", "定員",
               "放課後等デイの利用率", "10年後の需要見通し"]),
    ("第2部　立地と競合", ["候補地の評価", "事業所の空白地域", "特別支援学校と", "候補地周辺の競合",
                   "差別化のポイント", "直近半年の新規事業所", "新規事業所の継続状況", "運営法人"]),
    ("第3部　収支と資金", ["の収支試算", "投資判断"]),
    ("第4部　人材とリスク", ["人材確保", "リスクと対策", "現地確認チェックリスト"]),
    ("第5部　別の視点からの検証", ["経営理論から見た開業判断"]),
    ("付録", ["付録：", "出典と注記"]),
]
DROP = ["要点"]


def arrange(doc):
    """章を4つの問いの順（第1部〜第4部）に並べ直し、各章の見出しの下に結論の1文を入れる。"""
    head, *secs = doc.split('<section class="pb">')
    chap = []
    for sct in secs:
        m = re.match(r"<h2>(§§?)([^<]*)</h2>", sct)
        if m and m.group(1) == "§§" and chap:
            chap[-1][1].append(sct)
        else:
            chap.append([m.group(2) if m else "", [sct]])
    used, out = set(), []

    def find(key):
        return [i for i, (t, _) in enumerate(chap) if i not in used and key in t and not any(t == d for d in DROP)]
    for part, keys in PARTS:
        got = []
        for k in keys:
            for i in find(k):
                used.add(i)
                got.append(i)
        for j, i in enumerate(got):
            t, ss = chap[i]
            first = ss[0]
            if part and j == 0:
                first = re.sub(r"(<h2>§)", f'<!--PART:{part}--><p class="part">{part}</p>\\1', first, count=1)
            msg = next((MSG[k] for k in sorted(MSG, key=len, reverse=True) if k in t), None)
            if msg and 'class="msg"' not in first:
                first = re.sub(r"(<h2>§[^<]*</h2>)", lambda m_: m_.group(1) + f'<p class="msg">{msg}</p>', first, count=1)
            out.append([first] + ss[1:])
    rest = [chap[i] for i in range(len(chap)) if i not in used and chap[i][0] not in DROP]
    body = "".join('<section class="pb">' + x for ss in out for x in ss)
    body += "".join('<section class="pb">' + x for _, ss in rest for x in ss)
    return head + body


def number_sections(doc):
    """見出しの「§」に順に番号をふり、表紙の目次を作る。「§§」は前の章の続き。<!--PART:…--> は目次の部の見出し。"""
    n, toc = 0, []

    def one(m):
        nonlocal n
        if m.group(1):
            toc.append(f'<li class="tp">{m.group(1)}</li>')
            return ""
        if m.group(2) == "§§":
            return f"<h2>{n}. {m.group(3)}"
        n += 1
        toc.append(f"<li>{n}. {m.group(3)}</li>")
        return f"<h2>{n}. {m.group(3)}"
    doc = re.sub(r"<!--PART:([^>]*)-->|<h2>(§§?)([^<]*)", one, doc)
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
.cover h1 { color: #fff; font-size: 30pt; margin: 6px 0 12px; }
.cover .eyebrow { color: #a6e8b4; font-weight: 700; letter-spacing: .1em; margin: 0; }
.cover .edition { display: inline-block; align-self: flex-start; background: #a6e8b4; color: #1f4d3a; font-weight: 700; font-size: 13pt; padding: 4px 14px; border-radius: 6px; margin: 0 0 10px; }
.cover .sub { color: #b9d6c4; font-size: 13pt; margin: 0 0 18px; }
.cover .toc { list-style: none; padding: 0; columns: 2; font-size: 10pt; line-height: 1.55; color: #e5ede7; margin: 0; }
.cover .by { margin-top: 20px; color: #b9d6c4; }
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
.msg { font-size: 12.5pt; font-weight: 700; color: #1f4d3a; line-height: 1.5; margin: 2px 0 10px; padding-bottom: 6px; border-bottom: 1px solid #cfe2d2; }
.part { font-size: 10pt; font-weight: 700; color: #fff; background: #2d7a45; display: inline-block; padding: 2px 10px; border-radius: 4px; margin: 0 0 6px; }
.cover .toc li.tp { font-weight: 700; color: #ffd27a; margin-top: 4px; list-style: none; }
.keyp { border: 1px solid #2d7a45; border-radius: 8px; padding: 6px 14px; margin: 8px 0 12px; font-size: 10pt; break-inside: avoid; }
.keyp b { color: #2d7a45; } .keyp ul { margin: 4px 0 0; padding-left: 1.2em; }
.exec { background: #f6faf5; border-left: 5px solid #2d7a45; padding: 8px 14px; margin: 8px 0 14px; }
.exec ul { margin: 0; padding-left: 1.1em; } .exec li { margin: 5px 0; font-size: 10.5pt; }
table.rv td, table.rv th { font-size: 8.5pt; } table.pl td { text-align: right; }
table.issue td:nth-child(1) { width: 15%; } table.issue td:nth-child(2) { width: 18%; } table.issue td:nth-child(5) { white-space: nowrap; }
table.exec-t td, table.exec-t th { font-size: 8.5pt; white-space: nowrap; } table.exec-t td:nth-child(2) { white-space: normal; text-align: left; }
table.chk td:first-child { white-space: nowrap; } table.rv td { white-space: nowrap; } table.rv td:nth-child(1), table.rv td:nth-child(2) { white-space: normal; }
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
