"""都道府県の「工賃実績」（data/kouchin/raw/*.txt）から、就労継続支援B型の事業所ごとの平均工賃（月額）を取り出し、
WAM NET の事業所番号に結びつけて data/kouchin/kouchin.csv に書く。

公表資料には事業所番号が無い県が多いので、県の中で事業所名（と法人名・市区町村）が合うものを探す。
名前が同じ事業所が県内に2つ以上あって法人名や市区町村でも決められないときは、まちがえないように載せない。

使い方: python scripts/parse_kouchin.py   （data/kouchin/raw は Actions の kouchin.yml が作る）
"""
import collections
import csv
import gzip
import pathlib
import re
import sys
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "kouchin" / "raw"
OUT = ROOT / "data" / "kouchin" / "kouchin.csv"
B = "46"

# 県ごとの資料の形。pref は 0 始まりの都道府県番号（fetch.py と同じ）。
# Excel: シート名と列番号（name 事業所名, corp 法人名, corpno 法人番号, muni 市区町村, no 事業所番号, avg 月額の平均工賃）
# PDF : 数字の並びの何番目が月額の平均工賃か（avg、-1 は最後）。数字の前の文字から事業所名を探す。
SOURCES = {
    "hokkaido": dict(pref=0, year="令和6年度", kind="pdf", avg=6),
    "miyagi": dict(pref=3, year="令和6年度", kind="pdf", avg=-1),
    "chiba": dict(pref=11, year="令和7年度", kind="xlsx", sheet="就労B型", name=6, corp=5, corpno=4, muni=1, avg=13),
    "tokyo": dict(pref=12, year="令和6年度", kind="xlsx", sheet="就労B型（月額)", no=1, name=2, muni=3, avg=10),
    "kanagawa": dict(pref=13, year="令和6年度", kind="xlsx", sheet="R6_実績就労B型", name=3, corp=2, avg=10),
    "niigata": dict(pref=14, year="令和7年度", kind="xlsx", sheet="B型", name=2, corp=1, muni=3, avg=10),
    "shizuoka": dict(pref=21, year="令和6年度", kind="pdf", avg=-1, section="就労継続支援Ｂ型事業所）"),
    "kyoto": dict(pref=25, year="令和6年度", kind="pdf", avg=-1),
    "osaka": dict(pref=26, year="令和6年度", kind="xlsx", sheet="就労B型", name=7, corp=6, corpno=5, muni=3, avg=14),
    "hyogo": dict(pref=27, year="令和6年度", kind="xlsx", sheet="R6就労B型 ", name=2, corp=1, muni=12, avg=9),
    "hiroshima": dict(pref=33, year="令和6年度", kind="xlsx", sheet="集計データ", no=1, name=2, avg=9),
    "fukuoka": dict(pref=39, year="令和6年度", kind="xlsx", sheet="就労B型", name=1, muni=2, avg=9),
    "kumamoto": dict(pref=42, year="令和6年度", kind="xlsx", sheet="就労B型", name=4, corp=3, corpno=2, avg=11),
}

# 名前をくらべる前に取りのぞく言葉（どの事業所にもつく言葉）
GENERIC = re.compile(r"就労継続支援b型事業所|就労継続支援b型|就労支援b型|b型事業所|b型支援事業所|多機能型事業所|多機能型|"
                     r"指定障害福祉サービス事業所|障害福祉サービス事業所|事業所|就労支援センター")
CORP_FORMS = re.compile(r"社会福祉法人|特定非営利活動法人|npo法人|一般社団法人|一般財団法人|公益社団法人|公益財団法人|"
                        r"株式会社|有限会社|合同会社|医療法人社団|医療法人財団|医療法人|社会福祉協議会|\(株\)|\(有\)|\(福\)|\(特非\)|\(一社\)")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = re.sub(r"[\s　・･\-‐－ー―~〜「」『』()（）【】\[\].,、。'’\"”&＆/／]", "", s.replace("ｰ", "ー"))
    return s


def nname(s: str) -> str:
    n = GENERIC.sub("", norm(s))
    return n or norm(s)


def ncorp(s: str) -> str:
    return CORP_FORMS.sub("", norm(s))


def load_offices():
    """県ごとの、いま公表データに載っている B型 事業所。"""
    latest = sorted((ROOT / "data" / "slim" / B).glob("*.csv.gz"))[-1]
    by_pref = collections.defaultdict(list)
    nos = set()
    with gzip.open(latest, "rt", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            by_pref[int(r["pref"])].append(r)
            nos.add(r["no"])
    return by_pref, nos


def to_yen(x: str):
    x = unicodedata.normalize("NFKC", x or "").replace(",", "").replace("円", "").strip()
    try:
        v = float(x)
    except ValueError:
        return None
    return round(v) if v > 0 else None


def rows_xlsx(text: str, cf: dict):
    lines = text.split("\n")
    i = lines.index("=== " + cf["sheet"])
    for l in lines[i + 1:]:
        if l.startswith("=== "):
            break
        r = l.split("\t")
        get = lambda k: r[cf[k]].strip() if k in cf and cf[k] < len(r) else ""
        yen = to_yen(get("avg"))
        name = get("name")
        if not yen or not name or "合計" in name:
            continue
        yield dict(name=name, corp=get("corp"), corpno=get("corpno"), muni=get("muni"), no=get("no"), yen=yen)


NUM = r"[\d,]+(?:\.\d+)?円?"


def rows_pdf(text: str, cf: dict):
    """pdftotext -layout の行。「文字 文字 … 数字 数字 …」の形の行から、文字の部分（候補）と月額を取る。"""
    on = "section" not in cf
    for l in text.split("\n"):
        if "section" in cf and "令和" in l and "工賃実績" in l:
            on = cf["section"] in l
        if not on:
            continue
        l = unicodedata.normalize("NFKC", l).strip()
        # 数字が2つ以上ならんだ最初のところを、定員・工賃などの欄とみなす（その前が事業所名など）
        m = re.search(rf"(?:^|\s)((?:{NUM}\s+)+{NUM})(?=\s|$)", l)
        if not m or not l[:m.start()].strip():
            continue
        nums = m.group(1).split()
        try:
            yen = to_yen(nums[cf["avg"]])
        except IndexError:
            continue
        head = re.sub(r"^\d+\s+", "", l[:m.start()].strip())
        if yen and head:
            yield dict(text=head, yen=yen)


def match_xlsx(row, offices, idx):
    if row["no"] and row["no"] in idx["no"]:
        return row["no"]
    cands = idx["name"].get(nname(row["name"]), [])
    if len(cands) == 1:
        o = cands[0]
        # 名前が1つしか合わないときも、法人名や市区町村が書いてあって明らかに違うときはやめる
        if row["corp"] and ncorp(row["corp"]) and ncorp(row["corp"]) not in ncorp(o["corp"]) and ncorp(o["corp"]) not in ncorp(row["corp"]) \
                and not (row["corpno"] and row["corpno"] == o["corp_key"]):
            return None
        return o["no"]
    if len(cands) > 1:
        for test in (lambda o: row["corpno"] and row["corpno"] == o["corp_key"],
                     lambda o: row["corp"] and ncorp(row["corp"]) == ncorp(o["corp"]),
                     lambda o: row["muni"] and norm(re.sub(r"^[\d-]+", "", row["muni"])) in norm(o["muni"])):
            hit = [o for o in cands if test(o)]
            if len(hit) == 1:
                return hit[0]["no"]
    return None


def match_pdf(row, offices, idx):
    # 文字の部分は「市区町村 法人名 事業所名」などがスペースで並ぶ。後ろから順に区切って、事業所名に合うものを探す
    parts = row["text"].split()
    for k in range(len(parts)):
        tail = " ".join(parts[k:])
        before = norm(" ".join(parts[:k]))
        cands = idx["name"].get(nname(tail), [])
        if len(cands) == 1:
            return cands[0]["no"]
        if len(cands) > 1 and before:
            hit = [o for o in cands if ncorp(o["corp"]) and ncorp(o["corp"]) in ncorp(before) or norm(o["muni"]).startswith(before)]
            if len(hit) == 1:
                return hit[0]["no"]
    return None


def main():
    by_pref, _ = load_offices()
    out = {}
    for key, cf in SOURCES.items():
        path = RAW / f"{key}.txt"
        if not path.exists() or path.read_text(encoding="utf-8").startswith("ERROR"):
            print(f"{key}: 資料なし", file=sys.stderr)
            continue
        text = path.read_text(encoding="utf-8").split("\n", 1)[1]
        offices = by_pref[cf["pref"]]
        idx = dict(no={o["no"] for o in offices}, name=collections.defaultdict(list))
        for o in offices:
            idx["name"][nname(o["name"])].append(o)
        rows = list(rows_xlsx(text, cf) if cf["kind"] == "xlsx" else rows_pdf(text, cf))
        hit = 0
        seen = set()
        for r in rows:
            no = (match_xlsx if cf["kind"] == "xlsx" else match_pdf)(r, offices, idx)
            if not no:
                continue
            if no in seen:   # 同じ事業所に2行あたったら、どちらが正しいかわからないので両方やめる
                out.pop(no, None)
                continue
            seen.add(no)
            out[no] = (r["yen"], key, cf["year"])
            hit += 1
        print(f"{key}: 資料 {len(rows):,}行 → 結びついた {hit:,}件 / 県内のB型 {len(offices):,}件")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["no", "yen", "src", "year"])
        for no in sorted(out):
            w.writerow([no, *out[no]])
    print(f"合計 {len(out):,}件 → {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
