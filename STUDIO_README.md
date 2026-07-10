# Mojicast

配信用リアルタイム字幕アプリ（ローカル完結・オフライン動作）。
ReazonSpeech k2 + Silero VAD + BERT句読点 + OBSオーバーレイ。

## 起動

`Mojicast.bat` をダブルクリック（またはコマンドで）:

```powershell
.\reazonspeech-env\Scripts\python.exe app.py
```

## 画面構成

- **コックピット（メイン窓）**
  - ▶開始/■停止、マイク選択、入力レベルメーター
  - 無音確定・途中更新間隔・最大発話長・単語ブーストのスライダー
  - 字幕モニタ（配信に乗る内容の確認用）
  - スタイルプリセット切替（OBS側へ即反映）
  - OBS用URLのコピー
- **単語スタジオ（別窓）** — コックピットの「✦ 単語スタジオ」から
  - 🎤 ホットワード: 認識を誘導する単語（表記・読み・スコア）→ 次回開始時に反映
  - ✨ エフェクト単語: 画面装飾（色・サイズ・アニメ、プレビュー付き）→ 即反映

## OBS 連携

1. ソース追加 →「ブラウザ」
2. URL: `http://localhost:8765`（コックピット右下からコピー）
3. 幅・高さは配信解像度と同じに（例 1920×1080）

## データファイル

| ファイル | 内容 | 反映タイミング |
|---|---|---|
| `config.json` | 全設定（自動保存） | エンジン系は次回開始時 |
| `hotwords.txt` | ホットワード（表記,読み,スコア） | 次回開始時 |
| `effects.json` | エフェクト単語（色/フォント/アニメ/パーティクル） | 即時 |
| `presets.json` | 文字スタイルプリセット（自作追加OK） | 選択・保存時 |
| `boxes.json` | 字幕ボックス（位置/背景/スクロール） | 選択・保存時 |

## 配布パッケージ化（PyInstaller・完全オフライン）

テスター配布用の「Python不要・ネット不要」パッケージを作る手順（検証済み）。
モデルを事前DLしておく必要があるので、先に一度アプリを起動して認識まで動かしておく。

```powershell
.\reazonspeech-env\Scripts\pip.exe install pyinstaller
.\reazonspeech-env\Scripts\pyinstaller.exe --noconfirm Mojicast.spec  # onedir でexe化
.\build_bundle.ps1                                                            # モデル+アセットを同梱
```

生成物: `dist\Mojicast\`（約3.1GB）。フォルダごとZipして渡す。

構成ファイル:
- `Mojicast.spec` — ビルド定義（torch/transformers/sherpa_onnx/pywebview等を collect_all）
- `build_bundle.ps1` — ビルド後にアセットと**必要な4モデルだけ**を app 直下へ配置
- `apppaths.py` — 凍結時に同梱 `models/` を HF キャッシュとして使いオフライン化
  （`HF_HOME` / `HF_HUB_OFFLINE` を HF系 import より前に設定）
- `defaults\` — 配布用の汎用データ（hotwords/effects/presets/boxes）。
  個人用の単語帳やDLフォント指定を配布物に入れないため、パッケージ時はこちらが優先コピーされる
- `README_TESTER.txt` / `マニュアル.html` — テスターへ同梱するドキュメント

同梱する4モデル（`%USERPROFILE%\.cache\huggingface\hub` から）:
`reazonspeech-k2-v2`（ASR） / `bert-base-japanese-char-v3`（句読点の土台） /
`bert_japanese_punctuation`（句読点の重み） / `fugumt-ja-en`（英訳）

注意点:
- torch同梱のためサイズは大きめ（モデル込みで約3.1GB）
- 配布先PCには WebView2 ランタイムが必要（Windows 11 は標準搭載）
