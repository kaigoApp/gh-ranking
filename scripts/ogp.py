"""LINE や X でページを送ったときに出るシェア用の画像（1200×630 の PNG）を作る。

Pillow と、.fonts/ に置いたフォント（Zen Maru Gothic・Barlow Semi Condensed）が必要。
どちらかが無ければ画像は作らず、ページはそのまま作る（手元では標準ライブラリだけで動くように）。
フォントは GitHub Actions が Google Fonts のリポジトリから取得する。
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONTS = ROOT / ".fonts"
W, H = 1200, 630
INK, INK2 = (12, 71, 70), (21, 95, 92)
ON, MUTED, LEAF, SUN = (242, 251, 250), (168, 213, 208), (143, 227, 218), (255, 185, 63)
BRAND = (10, 130, 125)

try:
    from PIL import Image, ImageDraw, ImageFont
    _F = {k: FONTS / v for k, v in dict(black="ZenMaruGothic-Black.ttf", bold="ZenMaruGothic-Bold.ttf", num="BarlowSemiCondensed-Bold.ttf").items()}
    ENABLED = all(p.exists() for p in _F.values())
except ImportError:  # Pillow がない
    ENABLED = False

_cache = {}


def font(kind, size):
    k = (kind, size)
    if k not in _cache:
        _cache[k] = ImageFont.truetype(str(_F[kind]), size)
    return _cache[k]


def _base():
    if "base" in _cache:
        return _cache["base"].copy()
    im = Image.new("RGB", (W, H), INK)
    d = ImageDraw.Draw(im)
    # 地図を思わせる点模様と、右上のやわらかい光
    for y in range(12, H, 24):
        for x in range(12, W, 24):
            d.ellipse((x - 1, y - 1, x + 1, y + 1), fill=INK2)
    # ロゴ（しずく形のピンと家、黄色い太陽）
    s = 1.6
    ox, oy = 64, 50
    d.ellipse((ox + 4 * s, oy + 2 * s, ox + 28 * s, oy + 26 * s), fill=BRAND)
    d.polygon([(ox + 5.5 * s, oy + 19 * s), (ox + 26.5 * s, oy + 19 * s), (ox + 16 * s, oy + 30 * s)], fill=BRAND)
    d.polygon([(ox + 10 * s, oy + 15 * s), (ox + 16 * s, oy + 10 * s), (ox + 22 * s, oy + 15 * s), (ox + 22 * s, oy + 22 * s), (ox + 10 * s, oy + 22 * s)], fill=(255, 255, 255))
    d.ellipse((ox + 20 * s, oy + 3 * s, ox + 27 * s, oy + 10 * s), fill=SUN)
    d.text((ox + 64, oy + 4), "ジモふく", font=font("black", 40), fill=ON)
    d.text((ox + 250, oy + 18), "地元の障害福祉が見つかる", font=font("bold", 22), fill=MUTED)
    _cache["base"] = im
    return im.copy()


def _fit(d, text, kind, size, max_w, min_size=36):
    while size > min_size and d.textlength(text, font=font(kind, size)) > max_w:
        size -= 4
    return font(kind, size)


def make(dest: pathlib.Path, eyebrow: str, title: str, number=None, unit="", lines=(), foot="", pic=None) -> bool:
    """dest に PNG を書く。作れなかったら False。pic は右側に置く画像（透明の PNG、レポートの表紙など）。"""
    if not ENABLED:
        return False
    im = _base()
    d = ImageDraw.Draw(im)
    x = 72
    y = 160
    right = W - x
    if pic:
        p = Image.open(pic).convert("RGBA")
        p.thumbnail((470, H - 64 - 40))
        px = W - p.width - 40
        im.paste(p, (px, H - 64 - p.height + 24), p)
        right = px - 24
    if eyebrow:
        d.text((x, y), eyebrow, font=font("bold", 28), fill=LEAF)
        y += 50
    f = _fit(d, title, "black", 72, right - x)
    d.text((x, y), title, font=f, fill=ON)
    y += f.size + 34
    if number is not None:
        fn = font("num", 150)
        d.text((x, y - 18), f"{number:,}", font=fn, fill=ON)
        nx = x + d.textlength(f"{number:,}", font=fn) + 16
        d.text((nx, y + 92), unit, font=font("bold", 34), fill=ON)
        lx = max(nx + d.textlength(unit, font=font("bold", 34)) + 48, 560)
        ly = y + 10
        for ln in lines[:3]:
            d.text((lx, ly), ln, font=_fit(d, ln, "bold", 30, right - lx, 20), fill=MUTED)
            ly += 46
    else:
        for ln in lines[:3]:
            d.text((x, y), ln, font=_fit(d, ln, "bold", 34, right - x, 20), fill=MUTED)
            y += 52
    d.rectangle((0, H - 64, W, H), fill=(8, 52, 51))
    d.text((x, H - 50), foot or "jimofuku.jp", font=font("bold", 24), fill=MUTED)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # 色数を減らすと 1 枚 20KB ほどになる（そのままだと 80KB）
    im.quantize(48, method=Image.Quantize.FASTOCTREE).save(dest, "PNG")
    return True
