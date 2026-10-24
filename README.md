# ジモふく

地元の障害福祉が見つかるサイトです。全国の障害福祉サービス事業所を、目的（暮らす場所・働く・子どもの支援など）と地域からさがせます。
事業所の数と推移も、サービス別・都道府県別・市区町村別に見られます。
国（WAM NET）のオープンデータを自動で取得して、ページを作り直します。

公開ページ：https://jimofuku.jp/

## ページの構成

| URL | 内容 |
| --- | --- |
| `/` | 検索、目的からさがす、最近できたグループホーム、地域からさがす、全国の推移 |
| `/s/<サービス>/` | サービス別ランキング（都道府県・市区町村・運営法人・新規掲載・掲載終了） |
| `/p/<都道府県>/` | 都道府県のサービス別・市区町村別の事業所数 |
| `/p/<都道府県>/<市区町村>/` | 市区町村の事業所の一覧と、サービス別の事業所数 |
| `/f/<事業所番号>/` | グループホームの施設ページ（電話・地図・同じ法人や地域の施設） |

## 仕組み

1. `scripts/fetch.py` が WAM NET の公開ページを見て、まだ持っていない時点・サービスの ZIP を取得し、
   必要な列だけを抜き出した gzip CSV（`data/slim/<サービスコード>/<YYYYMM>.csv.gz`）にして保存します。
2. `scripts/build.py` が `data/slim/` の全時点を集計し、`public/` にサイト一式を書き出します。
   デザインは `site/assets/`（CSS と、サービス別ページのランキング用 JS）にあります。
3. GitHub Actions（`.github/workflows/build.yml`）が、毎月1日と15日、または main への push のたびに 1→2 を実行し、
   エックスサーバー（jimofuku.jp）と GitHub Pages に公開します。

WAM NET のデータは毎年3月末時点と9月末時点の年2回公開されます。
サービスの名前と URL に使う名前は `scripts/services.py` で決めています。

## 手元で作るとき

```
python scripts/fetch.py
SITE_URL=https://kaigoapp.github.io/gh-ranking python scripts/build.py
```

Python 3.10 以上の標準ライブラリだけで動きます。自動取得がうまくいかない場合は、
WAM NET からダウンロードした ZIP を `python scripts/fetch.py --zip sfkopendata_202603_33.zip` のように取り込めます。

## 出典

独立行政法人福祉医療機構 WAM NET「障害福祉サービス等情報公表システムデータのオープンデータ」
https://www.wam.go.jp/content/wamnet/pcpub/top/sfkopendata/
