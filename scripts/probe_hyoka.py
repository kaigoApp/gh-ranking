"""とうきょう福祉ナビゲーションのページの作りを調べるための一時スクリプト（Actions で動かす）。"""
import pathlib, re, sys, urllib.parse, urllib.request, http.cookiejar
OUT = pathlib.Path("data/hyoka/probe"); OUT.mkdir(parents=True, exist_ok=True)
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
B = "https://www.fukunavi.or.jp/fukunavi/"


def get(name, url):
    r = op.open(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60)
    t = r.read().decode("utf-8", "replace")
    (OUT / f"{name}.html").write_text(t, encoding="utf-8")
    print(name, r.status, len(t), url[:200])
    return t


def form(t, fname="FRM"):
    """name=FRM の form の hidden を順に取り出す（同じ名前は最後の値）"""
    i = t.find(f'name="{fname}"')
    j = t.find("</form>", i)
    body = t[i:j]
    out = {}
    for m in re.finditer(r'<input type="hidden" name="(\w+)"\s+value="([^"]*)"', body):
        out[m.group(1)] = m.group(2)
    return out


t1 = get("svc", B + "controller?actionID=hyksearch&cmd=svcsbr&S_MODE=service&SVCDBR_CD=23&MODE=multi")
f = form(t1)
f["STEP_SVCSBRCD"] = "342"
t2 = get("s2_area", B + "controller?" + urllib.parse.urlencode(f))
f2 = form(t2)
print("form2", f2)
f2["MLT_AREA"] = "3"
t3 = get("s3_list", B + "controller?" + urllib.parse.urlencode(f2))
f3 = form(t3)
print("form3", f3)
