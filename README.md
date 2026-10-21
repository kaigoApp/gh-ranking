# グループホーム数ランキング

全国の障害者グループホーム（共同生活援助）の事業所数を、都道府県・市区町村・運営法人別に並べるサイトです。
国（WAM NET）のオープンデータを自動で取得して、ページを作り直します。

## 仕組み

1. `scripts/fetch.py` が WAM NET の公開ページを見て、まだ持っていない時点の ZIP を `data/raw/` に保存します。
2. `scripts/build.py` が `data/raw/` の全時点を集計し、`templates/index.html` にデータを入れて `public/index.html` を作ります。
3. GitHub Actions（`.github/workflows/build.yml`）が、毎月1日と15日、または main への push のたびに 1→2 を実行し、GitHub Pages に公開します。

WAM NET のデータは毎年3月末時点と9月末時点の年2回公開されます。

## 手動でデータを足すとき

自動取得がうまくいかない場合は、WAM NET から「共同生活援助」の ZIP をダウンロードし、
`data/raw/YYYYMM_33.zip`（例：`202603_33.zip`）の名前で置いて push してください。

## 手元で作るとき

```
python scripts/fetch.py
python scripts/build.py
```

Python 3.10 以上の標準ライブラリだけで動きます。

## 出典

独立行政法人福祉医療機構 WAM NET「障害福祉サービス等情報公表システムデータのオープンデータ」
https://www.wam.go.jp/content/wamnet/pcpub/top/sfkopendata/
