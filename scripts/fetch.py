"""WAM NET の障害福祉サービス等情報公表オープンデータを、全サービス・全時点取得する。

ZIP をそのまま保存するとリポジトリが重くなるので、必要な列だけを抜き出した
gzip 圧縮の CSV（data/slim/<コード>/<YYYYMM>.csv.gz）にして保存する。
すでにある時点は取得しない。取得に失敗しても終了コードは 0 にして、
手元にあるデータでページを作れるようにする。

  python scripts/fetch.py              WAM NET から足りない分を取得
  python scripts/fetch.py --zip X.zip  手元の ZIP を変換して取り込む（名前は sfkopendata_YYYYMM_CC.zip 形式）
"""
import csv
import gzip
import io
import pathlib
import re
import sys
import time
import unicodedata
import urllib.request
import zipfile

PAGE = "https://www.wam.go.jp/content/wamnet/pcpub/top/sfkopendata/"
BASE = "https://www.wam.go.jp"
ROOT = pathlib.Path(__file__).resolve().parent.parent
SLIM = ROOT / "data" / "slim"
UA = "shofuku-ranking (+https://github.com/kaigoApp/gh-ranking)"
FIELDS = ["no", "pref", "muni", "corp_key", "corp", "name", "addr", "lat", "lng", "tel", "url"]


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s.replace("�", "〓")  # 元データの文字化けを明示的な代替文字にする


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def col(header, *keys):
    for h in header:
        if all(k in h for k in keys):
            return h
    return None


def slim(zip_bytes: bytes) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        raw = z.read(name)
    for enc in ("utf-8-sig", "cp932"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", "replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return []
    h = list(rows[0].keys())
    c = dict(
        code=col(h, "都道府県コード"), no=col(h, "事業所番号"),
        muni=col(h, "事業所住所", "市区町村"), addr=col(h, "事業所住所", "番地"),
        corpno=col(h, "法人番号"), lat=col(h, "緯度"), lng=col(h, "経度"),
        tel=col(h, "事業所電話番号"), url=col(h, "事業所URL"),
        corp=next((x for x in h if x.startswith("法人の名称") and "かな" not in x), None),
        name=next((x for x in h if x.startswith("事業所の名称") and "かな" not in x), None),
    )
    missing = [k for k in ("code", "no", "muni", "name") if not c[k]]
    if missing:
        raise ValueError(f"必要な列がありません: {missing}")
    g = lambda r, k: (r.get(c[k]) or "") if c[k] else ""
    out = []
    for r in rows:
        try:
            pi = int(g(r, "code")[:2]) - 1
        except ValueError:
            continue
        if not 0 <= pi < 47:
            continue
        corp = norm(g(r, "corp"))
        out.append(dict(
            no=g(r, "no").strip(), pref=pi, muni=norm(g(r, "muni")),
            corp_key=g(r, "corpno").strip() or corp, corp=corp,
            name=norm(g(r, "name")), addr=norm(g(r, "addr")),
            lat=g(r, "lat").strip()[:10], lng=g(r, "lng").strip()[:11],
            tel=norm(g(r, "tel"))[:20], url=g(r, "url").strip()[:200],
        ))
    out.sort(key=lambda r: (r["no"], r["muni"], r["addr"], r["name"]))
    return out


def save(code: str, period: str, rows: list[dict]) -> pathlib.Path:
    dest = SLIM / code / f"{period}.csv.gz"
    dest.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    # mtime=0 で、同じ中身なら同じバイト列になるようにする（不要な差分を出さない）
    dest.write_bytes(gzip.compress(buf.getvalue().encode("utf-8"), compresslevel=9, mtime=0))
    return dest


def has_all_fields(path: pathlib.Path) -> bool:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return f.readline().strip().split(",") == FIELDS


def from_page() -> int:
    try:
        html = get(PAGE).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        print(f"::warning::WAM NET の一覧ページを取得できませんでした: {e}")
        return 0
    links = {}
    for href, period, code in re.findall(r'href="([^"]*sfkopendata_(\d{6})_(\d{2})\.zip)"', html):
        links[(period, code)] = href if href.startswith("http") else BASE + href
    print(f"一覧ページの ZIP: {len(links)} 件、時点 {sorted({p for p, _ in links})}")
    got = failed = 0
    latest = max(p for p, _ in links) if links else ""
    for (period, code), url in sorted(links.items()):
        dest = SLIM / code / f"{period}.csv.gz"
        # 最新時点は、列が足りない古い形式なら取り直す（電話番号・URL は最新時点だけ使う）
        if dest.exists() and not (period == latest and not has_all_fields(dest)):
            continue
        try:
            data = get(url)
            if not data.startswith(b"PK"):
                raise ValueError(f"ZIP ではありません（{len(data)} bytes）")
            rows = slim(data)
            save(code, period, rows)
            got += 1
            print(f"取得: {code}/{period} {len(rows):,} 行")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"::warning::{code}/{period} の取得に失敗: {e}")
        time.sleep(1)  # 相手のサーバーに負荷をかけない
    print(f"新規取得 {got} 件、失敗 {failed} 件")
    return 0


def from_zips(paths: list[str]) -> int:
    for p in paths:
        m = re.search(r"(\d{6})_(\d{2})\.zip$", p)
        if not m:
            print(f"ファイル名から時点とサービスを読み取れません: {p}")
            continue
        period, code = m.groups()
        rows = slim(pathlib.Path(p).read_bytes())
        print(f"変換: {save(code, period, rows).relative_to(ROOT)} {len(rows):,} 行")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--zip":
        sys.exit(from_zips(sys.argv[2:]))
    sys.exit(from_page())
