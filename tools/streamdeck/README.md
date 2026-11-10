# Mojicast for Stream Deck

Stream Deck から起動中の Mojicast を操作するプラグイン（試作 v0.1）。

本体の外部操作キー専用の窓口（`GET /api/deck/state` / `POST /api/deck/action`）だけを使う。字幕の本文・辞書・ログ・設定変更には届かない。

## ボタン

| ボタン | 中身 |
|---|---|
| 認識 スタート/ストップ | 押すたびに切替。認識中は点灯 |
| 字幕クリア | |
| シーン切替／デザイン切替／レイアウト切替 | ボタンごとに切替先を選ぶ。使用中なら点灯 |
| おまかせ切替 | 対象（シーン／デザイン／レイアウト）を選ぶ |
| 演出を出す | 笑い・拍手などのグループを選ぶ。演出ルールが ON のグループだけ出る |
| 単語エフェクト ON/OFF | |
| VRChat転送 ON/OFF・送信内容の切替 | Windows のみ |

翻訳の ON/OFF・翻訳先の切替は入れていない（認識の再起動を伴い、押すと字幕が数秒止まるため）。

## 使い方

1. Mojicast の［アプリ設定］→［AI連携］→［Stream Deckと接続する］で接続キーを発行する
2. Stream Deck にボタンを置き、設定欄の「接続キー」に貼る（全ボタン共通・1回だけ）
3. ポートを変えている場合だけ「ポート」も入れる（既定 8765）

## 開発

Node.js 24 以上（Stream Deck 7.1 以上が同梱の Node で動かす）。

```sh
cd tools/streamdeck
npm ci
python scripts/make_assets.py   # manifest・アイコン・設定画面を表から作り直す
npm run build                   # src/ → com.ishiki-emo.mojicast.sdPlugin/bin/plugin.js
npx streamdeck link com.ishiki-emo.mojicast.sdPlugin   # 初回だけ。Stream Deck に開発版を登録
npm run watch                   # 保存のたびに再ビルド＋プラグイン再起動
npm run validate
npm run pack                    # dist/com.ishiki-emo.mojicast.streamDeckPlugin
```

- 設定画面（Property Inspector）のデバッグ: Stream Deck で設定欄を開いた状態で http://localhost:23654/
- ログ: `com.ishiki-emo.mojicast.sdPlugin/logs/`
- アイコンは暫定（`scripts/make_assets.py` が色付きの角丸に1文字で描く）
- UUID（`com.ishiki-emo.mojicast` と各ボタンの UUID）は公開後に変えない。変えると利用者の配置が消える
