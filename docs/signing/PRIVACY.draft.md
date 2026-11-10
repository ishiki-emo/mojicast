<!--
下書き: 承認後に docs/PRIVACY.md として公開し、README の Code signing policy 節からリンクする。
接続先はコード（v1.1.0 / develop 8c16117）から拾ったもの。リリースごとに
「新しい外部通信を足していないか」を確認し、足したらここに追記する。
  - 更新確認: app_server.py UPDATE_MANIFEST_URL / _check_update()
  - モデル DL: asr_model.py / punct.py / translate.py / soundfx.py の _REPO*
  - それ以外の外向き通信: なし（HTTP サーバは 127.0.0.1 のみで待ち受け、
    VRChat 連携の OSC も 127.0.0.1 宛て）
-->

# プライバシーポリシー / Privacy Policy

最終更新: YYYY-MM-DD（Mojicast vX.Y.Z）

## 日本語

Mojicast（以下「本アプリ」）は、音声認識・句読点付け・翻訳をすべて利用者の
パソコンの中で行います。

### 外部に送信しないもの

- マイクの音声、録音
- 字幕・翻訳のテキスト、発話ログ
- 単語・字幕デザイン・設定などの利用者データ

本アプリは利用状況の統計（テレメトリ）や広告のための情報を収集しません。
アカウント登録もありません。

### 本アプリが行う外部通信（2 種類だけ）

| 目的 | いつ | 接続先 | 送る内容 |
|---|---|---|---|
| AI モデルのダウンロード | 初回起動時、または翻訳・多言語認識・リアクション演出などの機能を初めてオンにしたとき（取得済みなら通信しない） | `huggingface.co` とその配信用 CDN | ダウンロードするモデルの名前。下記のリポジトリから取得します |
| 更新の確認 | 画面を開いたとき（6 時間に 1 回まで）、および設定画面で「更新を確認」を押したとき | `raw.githubusercontent.com`（`ishiki-emo/mojicast` の `latest.json`） | 本アプリのバージョン番号（User-Agent: `Mojicast/x.y.z`） |

ダウンロードするモデルのリポジトリ（Hugging Face）:

- `reazon-research/reazonspeech-k2-v2`（日本語音声認識）
- `ishiki-emo/mojicast-punct-onnx`（句読点）
- `ishiki-emo/mojicast-fugumt-ja-en-ct2`（英訳）
- `ishiki-emo/mojicast-m2m100-ct2`（多言語翻訳）
- `csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17`（多言語音声認識・使う場合のみ）
- `k2-fsa/sherpa-onnx-zipformer-small-audio-tagging-2024-04-15`（リアクション演出・使う場合のみ）

どちらの通信でも、通信の性質上、接続先には利用者の IP アドレスが伝わります。
接続先での取り扱いは各サービスのプライバシーポリシーに従います。

- Hugging Face: <https://huggingface.co/privacy>
- GitHub: <https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement>

### パソコンの中だけで行う通信

- 字幕の表示（OBS のブラウザソース等）と設定画面は、本アプリが `127.0.0.1`
  （このパソコンの中だけ）で動かすサーバを使います。外部のパソコンからは接続できません。
- VRChat 連携をオンにした場合、字幕を `127.0.0.1` の OSC ポートへ送ります。
- AI 連携（MCP サーバ）を使う場合、AI アプリから本アプリへの接続は `127.0.0.1` 経由です。
  その先で AI サービスに何が送られるかは、利用者が使う AI アプリの設定とポリシーに従います。
- Stream Deck プラグイン（別配布）を使う場合、プラグインから本アプリへの接続も `127.0.0.1` 経由です。

### オフラインでの利用

モデルの取得が済んでいれば、インターネットに接続していなくても全機能が動きます。
更新確認は失敗しても何も起きません（ファイアウォール等で止めて構いません）。

### お問い合わせ

<https://github.com/ishiki-emo/mojicast/issues>

---

## English

Mojicast runs speech recognition, punctuation and translation entirely on your
computer.

### What is never sent

- Microphone audio or recordings
- Caption and translation text, speech logs
- Your words, caption styles, settings and other data

Mojicast collects no usage statistics (telemetry) and shows no ads. There is no
account.

### The only two kinds of network access

| Purpose | When | Host | What is sent |
|---|---|---|---|
| Download AI models | On first launch, or the first time you turn on a feature that needs a model (nothing is downloaded once a model is present) | `huggingface.co` and its CDN | The name of the model to download (repositories listed below) |
| Check for updates | When the window opens (at most once every 6 hours) and when you click "Check for updates" | `raw.githubusercontent.com` (`latest.json` in `ishiki-emo/mojicast`) | The app version (User-Agent `Mojicast/x.y.z`) |

Model repositories on Hugging Face:

- `reazon-research/reazonspeech-k2-v2`
- `ishiki-emo/mojicast-punct-onnx`
- `ishiki-emo/mojicast-fugumt-ja-en-ct2`
- `ishiki-emo/mojicast-m2m100-ct2`
- `csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17` (only if used)
- `k2-fsa/sherpa-onnx-zipformer-small-audio-tagging-2024-04-15` (only if used)

As with any internet connection, these hosts see your IP address. Their
handling of it is covered by their own privacy policies:
[Hugging Face](https://huggingface.co/privacy),
[GitHub](https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement).

### Local-only connections

The caption overlay, the settings window, the VRChat OSC output, the MCP
server for AI apps and the Stream Deck plugin (distributed separately) all use `127.0.0.1` (this computer only). Mojicast cannot be
reached from other computers. What an AI app sends to its own service is
governed by that app's settings and policy.

### Offline use

Once models are downloaded, every feature works offline. A failed update check
has no effect, so it is safe to block it with a firewall.

### Contact

<https://github.com/ishiki-emo/mojicast/issues>
