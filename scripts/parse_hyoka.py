"""東京都の第三者評価を受けた事業所（data/hyoka/hyoka.csv、fetch_hyoka.py が作る）を、
WAM NET の事業所番号に結びつけて data/hyoka/matched.csv に書く。

福ナビの番号は WAM NET の事業所番号とは別のものなので、同じサービスの中で
電話番号・事業所名・法人名・市区町村をくらべて結びつける。決めきれないものは、まちがえないように載せない。

使い方: python scripts/parse_hyoka.py
"""
import collections
import csv
import gzip
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from parse_kouchin import ROOT, EXCLUDE_CORPS, EXCLUDE_NOS, ncorp, norm  # noqa: E402

SRC = ROOT / "data" / "hyoka" / "hyoka.csv"
OUT = ROOT / "data" / "hyoka" / "matched.csv"
TOKYO = "12"

# 福ナビのサービスの番号 → WAM NET のサービスの番号
SVC = {"428": ["68"], "429": ["68"], "427": ["68"], "430": ["69"], "431": ["69"], "234": ["11"], "235": ["24"],
       "344": ["33"], "411": ["63"], "413": ["63"], "419": ["63"], "420": ["63"], "421": ["64"], "415": ["65"],
       "422": ["65"], "425": ["22"], "337": ["22"], "338": ["41"], "339": ["42"], "433": ["34"], "340": ["60"],
       "341": ["45"], "342": ["46"]}
# 多機能型などは（ ）の中に実際のサービスが書いてある
WORDS = [("児童発達支援", "63"), ("放課後等デイサービス", "65"), ("生活介護", "22"), ("機能訓練", "41"),
         ("生活訓練", "42"), ("宿泊型自立訓練", "34"), ("就労移行支援", "60"), ("就労継続支援Ａ型", "45"),
         ("就労継続支援Ｂ型", "46"), ("施設入所支援", "32")]

GENERIC = re.compile(r"グループホーム|ぐるーぷほーむ|gh|ショートステイ|短期入所|生活介護|就労継続支援[ab]型|就労移行支援|"
                     r"放課後等デイサービス|児童発達支援|事業所|センター")


def yr(s):
    return int(re.sub(r"\D", "", s) or 0)


def codes(r):
    if r["svc"] in SVC:
        return SVC[r["svc"]]
    inner = r["svcname"].split("（", 1)[-1]
    return [c for w, c in WORDS if w in inner]


def nname(s):
    n = GENERIC.sub("", norm(s))
    return n or norm(s)


def tel(s):
    d = re.sub(r"\D", "", s or "")
    return d if len(d) >= 10 else ""


def place(addr):
    m = re.search(r"東京都(.+?[区市町村])", addr)
    return m.group(1) if m else ""


def load(c):
    files = sorted((ROOT / "data" / "slim" / c).glob("*.csv.gz"))
    if not files:
        return {}
    out = {}
    with gzip.open(files[-1], "rt", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["pref"] != TOKYO or r["no"] in EXCLUDE_NOS or r["corp_key"] in EXCLUDE_CORPS:
                continue
            out.setdefault(r["no"], r)
    return out


def same_corp(a, b):
    a, b = ncorp(a), ncorp(b)
    return bool(a and b) and (a == b or a in b or b in a)


def main():
    rows = list(csv.DictReader(SRC.open(encoding="utf-8")))
    wam = {}
    idx = {}
    for c in sorted({c for r in rows for c in codes(r)}):
        wam[c] = load(c)
        by_tel, by_name = collections.defaultdict(list), collections.defaultdict(list)
        for o in wam[c].values():
            if tel(o["tel"]):
                by_tel[tel(o["tel"])].append(o)
            by_name[nname(o["name"])].append(o)
        idx[c] = (by_tel, by_name)
    out = {}
    stat = collections.Counter()
    for r in rows:
        names = {nname(x) for x in [r["name"], *r["name"].split("／")] if x.strip()}
        t = tel(r["addr"].split()[-3:] and "".join(re.findall(r"[\d-]+-\s*[\d]+-\s*\d+", r["addr"])[-1:]))
        muni = place(r["addr"])
        for c in codes(r):
            by_tel, by_name = idx[c]
            hit = None
            # 1) 名前が合い、電話・法人・市区町村のどれかも合う
            cands = [o for n in names for o in by_name.get(n, [])]
            cands = list({o["no"]: o for o in cands}.values())
            good = [o for o in cands if (t and tel(o["tel"]) == t) or same_corp(r["corp"], o["corp"])]
            if len(good) == 1:
                hit, how = good[0], "name+"
            elif len(good) > 1 and muni:
                g2 = [o for o in good if o["muni"].endswith(muni)]
                if len(g2) == 1:
                    hit, how = g2[0], "name+muni"
            # 2) 電話番号が合い、法人も合う（名前の書き方がちがうとき）
            if not hit and t:
                g = [o for o in by_tel.get(t, []) if same_corp(r["corp"], o["corp"])]
                if len(g) == 1:
                    hit, how = g[0], "tel+corp"
            if not hit:
                stat["no-" + c] += 1
                continue
            stat[how] += 1
            k = (hit["no"], c)
            if k not in out or yr(r["year"]) > yr(out[k]["year"]):
                out[k] = dict(no=hit["no"], c=c, year=r["year"].replace("令和0", "令和"), svcname=r["svcname"],
                              hyk_id=r["hyk_id"], svc=r["svc"], fno=r["no"])
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["no", "c", "year", "svcname", "hyk_id", "svc", "fno"], lineterminator="\n")
        w.writeheader()
        w.writerows(sorted(out.values(), key=lambda x: (x["no"], x["c"])))
    print(dict(stat))
    print(f"福ナビ {len(rows):,}行 → 結びついた {len(out):,}件 → {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
