"""WAM NET の障害福祉サービス等情報公表オープンデータ（共同生活援助）を取得する。

公開ページに並ぶ ZIP のうち、まだ data/raw/ に無い時点のものだけをダウンロードする。
取得に失敗しても、手元にある data/raw/ のファイルでページを作れるよう、終了コードは 0 にする。
"""
import pathlib
import re
import sys
import time
import urllib.request

PAGE = "https://www.wam.go.jp/content/wamnet/pcpub/top/sfkopendata/"
BASE = "https://www.wam.go.jp"
SERVICE = "33"  # 共同生活援助
RAW = pathlib.Path(__file__).resolve().parent.parent / "data" / "raw"
UA = "gh-ranking (+https://github.com/kaigoApp/gh-ranking)"


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    try:
        html = get(PAGE).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        print(f"::warning::WAM NET の一覧ページを取得できませんでした: {e}")
        return 0

    pat = re.compile(r'href="([^"]*sfkopendata_(\d{6})_' + SERVICE + r'\.zip)"')
    found = {}
    for href, period in pat.findall(html):
        found[period] = href if href.startswith("http") else BASE + href
    print(f"一覧ページで見つかった時点: {sorted(found)}")

    for period, url in sorted(found.items()):
        dest = RAW / f"{period}_{SERVICE}.zip"
        if dest.exists():
            continue
        try:
            data = get(url)
        except Exception as e:  # noqa: BLE001
            print(f"::warning::{period} の取得に失敗: {e}")
            continue
        if not data.startswith(b"PK"):
            print(f"::warning::{period} は ZIP ではありませんでした（{len(data)} bytes）")
            continue
        dest.write_bytes(data)
        print(f"取得: {dest.name} ({len(data):,} bytes)")
        time.sleep(2)  # 相手のサーバーに負荷をかけない
    return 0


if __name__ == "__main__":
    sys.exit(main())
