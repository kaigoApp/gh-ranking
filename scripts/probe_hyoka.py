"""とうきょう福祉ナビゲーションのページの作りを調べるための一時スクリプト（Actions で動かす）。"""
import pathlib, sys, urllib.request, http.cookiejar
OUT = pathlib.Path("data/hyoka/probe"); OUT.mkdir(parents=True, exist_ok=True)
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
B = "https://www.fukunavi.or.jp/fukunavi/"
urls = {
 "svc": B + "controller?actionID=hyksearch&cmd=svcsbr&S_MODE=service&SVCDBR_CD=23&MODE=multi",
 "name": B + "controller?actionID=hyksearch&cmd=svcdbr&S_MODE=name&MLT_AREA=&H_NAME=&J_NAME=",
 "area": B + "controller?actionID=hyksearch&cmd=arealist&S_MODE=area&MLT_AREA=&H_NAME=&J_NAME=",
 "cmp": B + "controller?actionID=hyk&cmd=hykhkk_search",
}
for k, u in urls.items():
    try:
        r = op.open(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=60)
        b = r.read()
        (OUT / f"{k}.html").write_bytes(b)
        print(k, r.status, len(b), r.headers.get("Content-Type"))
    except Exception as e:
        print("NG", k, e, file=sys.stderr)
