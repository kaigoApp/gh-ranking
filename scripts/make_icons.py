"""ブラウザのタブやスマホのホーム画面に出る小さなアイコン（ファビコン）を作る。

ロゴ（build.py の LOGO と同じ、しずく形のピンと家・黄色い太陽）を描いて site/root/ に書く。
ロゴの形や色を変えたときだけ手で動かす（python scripts/make_icons.py）。Pillow が必要。
"""
import pathlib

from PIL import Image, ImageDraw

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEST = ROOT / "site" / "root"
BRAND, SUN, INK, WHITE = "#3d9a58", "#ffb93f", "#1f4d3a", "#ffffff"
# LOGO と同じ形（viewBox 0 0 32 32）。背景は透明
SVG = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><path d="M16 2C9.4 2 4 7.3 4 13.9 4 22.6 16 30 16 30s12-7.4 12-16.1C28 7.3 22.6 2 16 2z" fill="{BRAND}"/><path d="M10 15.2 16 10l6 5.2V21a1 1 0 0 1-1 1h-3.2v-4h-3.6v4H11a1 1 0 0 1-1-1z" fill="{WHITE}"/><circle cx="23.5" cy="6.5" r="3.5" fill="{SUN}"/></svg>
"""


def draw(size, bg=None, pad=0.0):
    """size 四方のロゴ。大きく描いてから縮めて、ふちをなめらかにする。"""
    k = 8
    big = size * k
    im = Image.new("RGBA", (big, big), bg or (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    s = big * (1 - 2 * pad) / 32
    o = big * pad

    def p(x, y):
        return (o + x * s, o + y * s)
    # しずく形：上の円と、下にとがる三角
    d.ellipse([p(4, 2), p(28, 26)], fill=BRAND)
    d.polygon([p(5.2, 18.5), p(26.8, 18.5), p(16, 30)], fill=BRAND)
    # 家（屋根の三角と四角、真ん中の入口）
    d.polygon([p(10, 15.2), p(16, 10), p(22, 15.2), p(22, 22), p(10, 22)], fill=WHITE)
    d.rectangle([p(14.2, 18), p(17.8, 22)], fill=BRAND)
    d.ellipse([p(20, 3), p(27, 10)], fill=SUN)
    return im.resize((size, size), Image.LANCZOS)


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / "favicon.svg").write_text(SVG, encoding="utf-8")
    draw(48).save(DEST / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    # iPhone のホーム画面・Google の検索結果用。角は端末が丸めるので、地色で塗りつぶす
    draw(180, INK, 0.12).convert("RGB").save(DEST / "apple-touch-icon.png")
    draw(192, INK, 0.12).convert("RGB").save(DEST / "icon-192.png")
    print("作りました:", ", ".join(sorted(p.name for p in DEST.iterdir())))


if __name__ == "__main__":
    main()
