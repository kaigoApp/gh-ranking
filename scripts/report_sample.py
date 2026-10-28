"""地域別レポートの HTML から、前半だけの無料見本を作る。

  python3 scripts/report_sample.py <もとの HTML> <書き出す HTML> [残す章の数]

表紙の目次はそのまま残す（製品版に何が入っているかが分かるように）。残りの章は名前だけ最後に並べる。
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
    # 競合の章は候補地の名前が出るので、見本には入れない
    kept, rest = [], []
    for c in chapters:
        m = re.match(r"<h2>([^<]*)</h2>", c)
        t = m.group(1) if m else ""
        if len(kept) < keep and "競合" not in t:
            kept.append(c)
        elif t and "（続き）" not in t:
            rest.append(t)
    cover = cover.replace('<p class="eyebrow">ジモふく 地域レポート</p>',
                          '<p class="eyebrow">ジモふく 地域レポート　<span class="stamp">無料見本（前半・大事な数字はぼかしています）</span></p>')
    end = ('<section class="pb"><h2>この先の章（製品版）</h2>'
           '<p>見本はここまでです。見本でぼかした場所と数字は、製品版では全て読めます。製品版には、このあとに次の章が入っています。</p><ul>'
           + "".join(f"<li>{t}</li>" for t in rest) + "</ul>"
           '<p class="contact">ほかの市区のレポートと、グループホーム版・就労継続支援B型版は '
           'https://jimofuku.jp/report/area/ から。</p></section>')
    # 見本では、大事な数字（第一候補の場所、点数と理由、まとめの数字）をぼかす
    style = ("<style>.stamp{background:#ffb93f;color:#1f4d3a;padding:2px 10px;border-radius:4px;margin-left:8px}"
             ".lead b,.tv,table.rank td:nth-child(3),table.rank td:nth-child(5),ol.points li:first-child b,.chart .lab,.chart .val,"
             "table.cl td:nth-child(2),.exec .ev,table.exec-t td:nth-child(n+3),.keyp li b,table.issue td:nth-child(4){filter:blur(4.5px)}"
             "</style>")
    out = head.replace("</head>", style + "</head>") + "<body>" + cover + "".join(
        '<section class="pb">' + c for c in kept) + end + "</body>" + tail
    open(dst, "w", encoding="utf-8").write(out)
    print("見本:", dst, f"（{keep}章＋この先の章）")


if __name__ == "__main__":
    main()
