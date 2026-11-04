"""東京都の「福祉サービス第三者評価」を受けた障がい福祉の事業所の一覧を、
とうきょう福祉ナビゲーション（https://www.fukunavi.or.jp/）の検索結果から取り出して data/hyoka/hyoka.csv に書く。

検索結果の一覧には事業所番号（指定番号）が出ているので、WAM NET の事業所番号にそのまま結びつく。
評価の点数や文章は取らない（受けた年度と、公式の評価結果ページを開くための番号だけ）。
このコンテナからは福ナビにつながらないので、GitHub Actions（hyoka.yml）で動かす。
"""
import csv
import html
import http.cookiejar
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "hyoka"
B = "https://www.fukunavi.or.jp/fukunavi/"
UA = {"User-Agent": "Mozilla/5.0 (jimofuku.jp data update; monthly)"}
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def get(url):
    for i in range(3):
        try:
            t = op.open(urllib.request.Request(url, headers=UA), timeout=90).read().decode("utf-8", "replace")
            time.sleep(1.5)  # 相手のサーバーに負担をかけないよう、1件ずつ間をあける
            return t
        except Exception as e:
            print("retry", i, e, file=sys.stderr)
            time.sleep(10)
    raise RuntimeError(url)


def form(t, fname):
    """<form name=fname> の hidden を取り出す（同じ名前は最後の値）"""
    i = t.find(f'name="{fname}"')
    body = t[i:t.find("</form>", i)]
    return {m.group(1): m.group(2) for m in
            re.finditer(r'<input type="hidden" name="(\w+)"\s+value="([^"]*)"', body)}


def clean(s):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"\s+", " ", s.replace("　", " ")).strip()


ROW = re.compile(r"<tr>\s*(<td bgcolor=.*?)</tr>", re.S)


def rows(t):
    for m in ROW.finditer(t):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", m.group(1), re.S)
        if len(tds) < 6:
            continue
        d = re.findall(r"showDetail\('(\d+)','(\d+)','(\w+)','(\d)'\);\"><img [^>]*alt=\"([^\"]+)\"", tds[5])
        if not d:
            continue
        yield dict(no=d[0][2], svc=d[0][0], hyk_id=d[0][1], year=d[0][4], years="|".join(x[4] for x in d),
                   corp=clean(tds[0]), name=clean(tds[1]), addr=clean(tds[2]), svcname=clean(tds[4]))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    t1 = get(B + "controller?actionID=hyksearch&cmd=svcsbr&S_MODE=service&SVCDBR_CD=23&MODE=multi")
    codes = re.findall(r'name="STEP_SVCSBRCD" value="(\d+)"', t1)
    print("services", codes)
    out, seen = [], set()
    for code in codes:
        f = form(t1, "FRM")
        f["STEP_SVCSBRCD"] = code
        f2 = form(get(B + "controller?" + urllib.parse.urlencode(f)), "FRM")
        f2.update(MLT_AREA="3", actionID="hyk", cmd="hyklst")
        t = get(B + "controller?" + urllib.parse.urlencode(f2))
        m = re.search(r"全(\d+)件中", t)
        total = int(m.group(1)) if m else 0
        got = list(rows(t))
        fn = form(t, "FRM_NEXT")
        row = 40
        while row < total:
            fn["ROW"] = str(row)
            got += list(rows(get(B + "controller?" + urllib.parse.urlencode(fn))))
            row += 40
        print(code, "total", total, "got", len(got))
        for r in got:
            k = (r["no"], r["svc"])
            if k not in seen:
                seen.add(k)
                out.append(r)
        if code == codes[0]:  # 評価結果ページを直接開けるか（リンクの形）を確かめる
            check_detail(t, got[0] if got else None)
    with (OUT / "hyoka.csv").open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=["no", "svc", "svcname", "hyk_id", "year", "years", "name", "corp", "addr"])
        w.writeheader()
        w.writerows(sorted(out, key=lambda r: (r["no"], r["svc"])))
    print("written", len(out))


def check_detail(t, r):
    if not r:
        return
    fd = form(t, "FRM_DTL")
    fd.update(cmd="hyklstdtldigest", HYK_ID=r["hyk_id"], SVCSBRCD=r["svc"], JGY_CD=r["no"])
    short = dict(actionID="hyk", cmd="hyklstdtldigest", BEF_PRC="hyk", HYK_ID=r["hyk_id"], SVCSBRCD=r["svc"],
                 JGY_CD=r["no"])
    tries = {"full_session": fd, "short_session": short}
    for k, p in tries.items():
        u = B + "controller?" + urllib.parse.urlencode(p)
        x = get(u)
        ok = "表示することができません" not in x
        print("detail", k, ok, len(x), u)
        (OUT / f"detail_{k}.html").write_text(x, encoding="utf-8")
    # 新しいセッション（リンクを踏んだ人と同じ状態）
    fresh = urllib.request.build_opener()
    for k, p in tries.items():
        u = B + "controller?" + urllib.parse.urlencode(p)
        x = fresh.open(urllib.request.Request(u, headers=UA), timeout=90).read().decode("utf-8", "replace")
        print("detail fresh", k, "表示することができません" not in x, len(x))
        time.sleep(1.5)


if __name__ == "__main__":
    main()
