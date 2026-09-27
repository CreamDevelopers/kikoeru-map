# きこえる地図プロジェクト 公式サイト（www.kikoeru.org）

HTML・CSS・JavaScript（ES モジュール）だけの静的サイトです。ビルドは不要です。

```
site/
  index.html        トップ
  about.html        プロジェクトについて
  features.html     できること
  guide.html        使い方
  contribute.html   開発に参加
  sponsors.html     スポンサー
  faq.html          よくある質問
  404.html          ページが見つからないとき
  style.css         全ページ共通のスタイル
  js/
    theme-init.js   保存したテーマを描画前に適用（<head> で同期読み込み）
    main.js         エントリーポイント（以下のモジュールを読み込む）
    nav.js          現在のページの強調・スマートフォン用メニュー
    theme.js        テーマ切り替え（自動 / ライト / ダーク）
    copy.js         コードブロックのコピーボタン
    toc.js          ページ内目次と、表示中の節の強調
  assets/           ロゴ・アイコン・スポンサーロゴ
```

## 公開方法（Cloudflare Pages の例）

1. Cloudflare ダッシュボード → Workers & Pages → Create → Pages で、GitHub リポジトリ `CreamDevelopers/kikoeru-map` を接続します。
2. Build command は空欄にし、Build output directory には `site` を指定します。
3. Custom domains に `www.kikoeru.org` を追加します。

`404.html` は Cloudflare Pages が存在しない URL に対して自動で返します。

ローカルで確認するときは `python3 -m http.server -d site 8080` を実行し、<http://localhost:8080/> を開いてください。

## 変更するときの注意

- ヘッダーとフッターは各ページに同じものを書いています。メニューを変えるときは全ページをそろえてください。
- 地図サービスへのリンクは `https://map.kikoeru.org/` です。公開 URL が変わったら全ページで置き換えてください。
- スペシャルスポンサーのロゴは `assets/creamdev-logo.png` です（トップとスポンサーページで使用）。
- ロゴやアイコンを差し替えた場合は、`app/static/img/kikoeru-logo.png` と `app/static/icons/` から `assets/` にコピーし直してください。
