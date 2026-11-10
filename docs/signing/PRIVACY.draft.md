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

### 同じパソコンのほかのプログラムから読めるもの

OBS のブラウザソースは鍵（認証情報）を付けて接続できないため、字幕の表示に使う
次の 2 つは**認証なし**で読めるようにしています。

- `http://127.0.0.1:8765/events?overlay=1`（ポート番号は設定で変えられます）:
  認識中の字幕（確定前の途中経過を含む）・訳文・コラボ時の話者名などがリアルタイムに流れます
- `http://127.0.0.1:8765/api/style`: 字幕のデザイン・レイアウト、単語の演出、
  ハイライトする単語の一覧、カウンターの値

どちらも `127.0.0.1` でしか待ち受けないため、ほかのパソコンからは読めません。
また、ほかの Web サイトに埋め込まれたページからの読み取りは拒否します
（接続元のサイトを確認しています）。ただし、**同じパソコンで動いているほかのプログラム**
（ほかの利用者アカウントで動くものを含む）は読み取れます。字幕は配信で公開する
内容である前提の設計です。配信前の打ち合わせなど、映したくない話をするときは
認識を止めてください。設定の変更や字幕ログの読み出しなど、それ以外の操作には
本アプリが発行する鍵が必要です。

### パソコンに残るもの（字幕ログ）

- 「字幕ログを保存する」（アプリ設定の「字幕ログ」欄）は**既定でオン**です。
  認識した文（確定した行）を、時刻とコラボ時の話者名付きで
  **暗号化していないテキストファイル**として保存します。
  - Windows: 本アプリのフォルダの `logs\日付\日付時刻.log`
  - macOS: `~/Library/Application Support/Mojicast/logs/日付/日付時刻.log`
- 保存をやめるには、アプリ設定の「字幕ログを保存する」をオフにします
  （次に認識を開始したときから保存されなくなります）。保存済みのログは
  「字幕ログの保存フォルダを開く」から開いたフォルダを消してください。
- 字幕ログはこのパソコンの外へは送りません。AI 連携（MCP）から字幕ログを読む機能は
  既定でオフで、オンにした場合だけ AI アプリが読めます。
- 字幕ログとは別に、翻訳でエラーが起きたときは、原因の調査用に訳せなかった文と
  エラー内容を `translate_error.log` に残します（字幕ログの設定とは無関係）。

### Windows 版が起動時に行うこと（ダウンロード由来の印の解除）

インターネットから入手した Zip を展開すると、Windows は中のファイルに「インターネットから
ダウンロードした」という印（Mark of the Web / `Zone.Identifier`）を付けます。この印が
残っていると Windows の .NET が本アプリの部品（pythonnet・WebView2 の DLL）の読み込みを
拒否し、本アプリが起動できません。

そのため Windows 版の本アプリは起動時に、**自分のフォルダ（`Mojicast.exe` のあるフォルダ）の中の
ファイルに限って**この印を外します。

- 印が付いているかを部品の 1 ファイルで確かめ、付いているときだけ行います
  （実質、ダウンロード後の初回起動の 1 回だけ）
- 自分のフォルダの外のファイル、Windows のセキュリティ設定には触れません
- `Mojicast.exe` 自体を実行してよいかの確認（SmartScreen）は、この処理より前に
  Windows が行います。インストーラ版は印が付かないため、この処理は実質何もしません
- 同梱の「ブロック解除.bat」は、同じ処理を手動で行うためのものです

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

### What other programs on the same computer can read

OBS browser sources cannot send credentials, so the two endpoints used to draw
captions are readable **without authentication**:

- `http://127.0.0.1:8765/events?overlay=1` (the port can be changed in the
  settings): live captions (including in-progress text), translations and, in
  collab mode, speaker names
- `http://127.0.0.1:8765/api/style`: caption design and layout, word effects,
  the list of highlighted words, and counter values

Both listen on `127.0.0.1` only, so other computers cannot read them, and
requests from pages on other websites are rejected (the request origin is
checked). **Other programs running on the same computer** (including ones
running under another user account) can read them. This follows from the
design assumption that captions are shown on stream anyway; stop recognition
when you talk about something you do not want captioned. Every other
operation, such as changing settings or reading caption logs, requires a key
issued by Mojicast.

### What is stored on this computer (caption logs)

- "Save caption log" (Settings → Caption log) is **on by default**. Finalized
  caption lines are saved with a timestamp (and the speaker name in collab
  mode) as **unencrypted plain-text files**:
  - Windows: `logs\<date>\<date-time>.log` in the Mojicast folder
  - macOS: `~/Library/Application Support/Mojicast/logs/<date>/<date-time>.log`
- To stop saving, turn off "Save caption log" (takes effect the next time
  recognition starts). To remove existing logs, delete the folder opened by
  "Open caption log folder".
- Caption logs never leave the computer. Reading caption logs through the AI
  connection (MCP) is off by default and only possible if you turn it on.
- Separately from caption logs, when a translation fails Mojicast writes the
  sentence that could not be translated and the error to `translate_error.log`
  for troubleshooting (not affected by the caption log setting).

### What the Windows version does at startup (removing the download mark)

When a downloaded zip is extracted, Windows marks the files as downloaded from
the internet (Mark of the Web, `Zone.Identifier`). While that mark is present,
.NET refuses to load Mojicast's components (the pythonnet and WebView2 DLLs)
and the app cannot start.

The Windows version therefore removes this mark at startup, **only from files
inside its own folder** (the folder containing `Mojicast.exe`):

- It checks one component file first and only proceeds if the mark is present
  (in practice, only on the first launch after download).
- It does not touch files outside its folder or any Windows security setting.
- Windows' own check on whether `Mojicast.exe` may run (SmartScreen) happens
  before this. The installer version does not get the mark, so this step does
  nothing there.
- The bundled `ブロック解除.bat` does the same thing manually.

### Offline use

Once models are downloaded, every feature works offline. A failed update check
has no effect, so it is safe to block it with a firewall.

### Contact

<https://github.com/ishiki-emo/mojicast/issues>
