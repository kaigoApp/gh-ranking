"""地域別レポートの HTML から、前半だけの無料見本を作る。

  python3 scripts/report_sample.py <もとの HTML> <書き出す HTML> [残す章の数]

表紙の目次はそのまま残す（製品版に何が入っているかがわかるように）。残りの章は名前だけ最後に並べる。
"""
import re
import sys


def main():
    src, dst = sys.argv[1], sys.argv[2]
    keep = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    doc = open(src, encoding="utf-8").read()
    head, body = doc.split("<body>", 1)
    body, tail = body.rsplit("</body>", 1)
    parts = body.split('<section class="pb">')
    cover, chapters = parts[0], parts[1:]
    rest = []
    for c in chapters[keep:]:
        m = re.match(r"<h2>([^<]*)</h2>", c)
        if m and "（つづき）" not in m.group(1):
            rest.append(m.group(1))
    cover = cover.replace('<p class="eyebrow">ジモふく 地域レポート</p>',
                          '<p class="eyebrow">ジモふく 地域レポート　<span class="stamp">無料見本（前半）</span></p>')
    end = ('<section class="pb"><h2>この先の章（製品版）</h2>'
           '<p>見本はここまでです。製品版には、このあとに次の章が入っています。</p><ul>'
           + "".join(f"<li>{t}</li>" for t in rest) + "</ul>"
           '<p class="contact">ほかの市区のレポートと、グループホーム版・就労継続支援B型版は '
           'https://jimofuku.jp/report/area/ から。付録として、市の中のすべての事業所の一覧（Excel）がつきます。</p></section>')
    style = "<style>.stamp{background:#ffb93f;color:#1f4d3a;padding:2px 10px;border-radius:4px;margin-left:8px}</style>"
    out = head.replace("</head>", style + "</head>") + "<body>" + cover + "".join(
        '<section class="pb">' + c for c in chapters[:keep]) + end + "</body>" + tail
    open(dst, "w", encoding="utf-8").write(out)
    print("見本:", dst, f"（{keep}章＋この先の章）")


if __name__ == "__main__":
    main()
