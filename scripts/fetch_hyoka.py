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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from hyoka_link import hyoka_url  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "hyoka"
B = "https://www.fukunavi.or.jp/fukunavi/"
UA = {"User-Agent": "Mozilla/5.0 (jimofuku.jp data update; monthly)"}
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def get(url):
    t0 = time.time()
    for i in range(3):
        try:
            t = op.open(urllib.request.Request(url, headers=UA), timeout=40).read().decode("utf-8", "replace")
            if time.time() - t0 > 5:
                print("  slow", round(time.time() - t0), "s")
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
            page = list(rows(get(B + "controller?" + urllib.parse.urlencode(fn))))
            print(" ", code, row, len(page))
            if not page:
                break
            got += page
            row += 40
        print(code, "total", total, "got", len(got))
        for r in got:
            k = (r["no"], r["svc"])
            if k not in seen:
                seen.add(k)
                out.append(r)
    if len(out) < 1000:  # 相手のサイトの作りが変わったときなど。前の一覧を消さないように止める
        sys.exit(f"取れた事業所が少なすぎます（{len(out)}件）。fukunavi の検索ページの作りが変わっていないか確かめてください")
    check_links(out)
    with (OUT / "hyoka.csv").open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=["no", "svc", "svcname", "hyk_id", "year", "years", "name", "corp", "addr"])
        w.writeheader()
        w.writerows(sorted(out, key=lambda r: (r["no"], r["svc"])))
    print("written", len(out))


def check_links(out):
    """サイトに出すリンクで評価結果ページが開けるか、いくつか確かめる。福ナビの作りが変わったら止めて知らせる。"""
    fresh = urllib.request.build_opener()  # リンクを初めて押す人と同じく、クッキーなし
    bad = 0
    for r in out[:: max(1, len(out) // 5)][:5]:
        u = hyoka_url(r["hyk_id"], r["svc"], r["no"])
        res = fresh.open(urllib.request.Request(u, headers=UA), timeout=40)
        t = res.read().decode("utf-8", "replace")
        ok = "error.html" not in res.geturl() and "評価結果" in t and r["name"].split("／")[0][:4] in t
        print("link", "OK" if ok else "NG", r["name"], res.geturl()[-40:])
        bad += not ok
        time.sleep(1.5)
    if bad:
        sys.exit("評価結果ページへのリンクが開けません。scripts/hyoka_link.py の項目を、福ナビの一覧で事業所名を押したときの URL に合わせて直してください")


if __name__ == "__main__":
    main()
