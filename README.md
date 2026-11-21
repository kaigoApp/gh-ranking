# 地域レポートの PDF

サイトの無料ダウンロード（https://jimofuku.jp/report/area/ ）で渡す PDF の置き場です。
main の GitHub Actions（build.yml）が、この枝の `<時点>/` を取り出して、エックスサーバーの公開フォルダの外（jimofuku.jp/reports/）へ送ります。
ファイル名は `<市区の英字>_<版>.pdf`（版は grouphome / houday-jihatsu / shuro-b）。どの時点を使うかは data/report/shop.json の pdf_dir で決めます。
