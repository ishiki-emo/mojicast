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

## データファイル（すべて `data/` 配下）

| ファイル | 内容 | 反映タイミング |
|---|---|---|
| `data/config.json` | 全設定（自動保存） | エンジン系は次回開始時 |
| `data/hotwords.txt` | ホットワード（表記,読み,スコア）＝「共通」 | 次回開始時 |
| `data/effects.json` | エフェクト単語（色/フォント/アニメ/パーティクル） | 即時 |
| `data/banned.txt` / `data/glossary.txt` | 禁止ワード / 英訳辞書 | 次回開始時 |
| `data/presets.json` | 文字スタイルプリセット（自作追加OK） | 選択・保存時 |
| `data/boxes.json` | 字幕ボックス（位置/背景/スクロール） | 選択・保存時 |
| `data/profiles/<名前>/` | 単語プロファイル（hotwords/effects/banned/glossary） | 種類ごと上記と同じ |

- 単語プロファイルは「共通」との**合成**で使う（加算・同じ単語はプロファイル優先）。
  切替はコックピットの「単語プロファイル」、編集は単語スタジオ上部のセレクタから。
- バックアップ = `data/` フォルダのコピー。旧レイアウト（ルート直下）からは初回起動時に自動移行。

## 配布パッケージ化（PyInstaller）

```powershell
.\reazonspeech-env\Scripts\pip.exe install pyinstaller
.\reazonspeech-env\Scripts\pyinstaller.exe --noconfirm Mojicast.spec  # onedir でexe化
.\build_bundle.ps1 -NoModels    # 軽量版（既定・初回DL・Zip約320MB）← GitHub Releases 用
.\build_bundle.ps1              # 同梱版（モデル込み・完全オフライン・約3.1GB）
```

### リリース前チェックリスト（重要）
**dev では起きずリリース版でだけ起きる不具合**（凍結環境の挙動差・新規DL経路の故障）を
配布前に検出するため、必ずスモークテストを通すこと:

```powershell
.\smoke_test.ps1           # 高速: ローカルのモデル複製で検証（約2分）
.\smoke_test.ps1 -Fresh    # 完全: モデル実DL（約1.2GB）で新規ユーザーを再現 ←リリース前は必ずこちら
```

- テスト中に表示される Mojicast の窓は**閉じない**こと（閉じるとアプリ終了＝FAIL扱い）
- `-Fresh` は句読点/翻訳の変換済みモデルを配布リポジトリ（HFの2リポジトリ、
  `punct.py` / `translate.py` の `_REPO_ID`）から実DLする。アップロード済みであること
- PASS 後、`dist\Mojicast\models`・`models_conv`・`data`・`logs` を削除してから Zip する
- 教訓: ロード系は必ず自己診断（語彙サイズ検査・試訳・試句読点）で守ること。
  旧transformers時代、語彙欠落でも例外を出さず「空トークナイザ」ができて
  無言で字幕が消える事故があった（現行の ONNX/CT2 実装でも自己診断は継承済み）

構成ファイル:
- `Mojicast.spec` — ビルド定義（onnxruntime/ctranslate2/sherpa_onnx/pywebview等を collect_all）
- `build_bundle.ps1` — ビルド後にアセット（+同梱版はモデル）を app 直下へ配置
- `smoke_test.ps1` — リリース前スモークテスト（上記）
- `tools\convert_models.py` — 句読点→ONNX / FuguMT→CT2 の変換（開発時のみ・要torch）
- `bench\regression_diff.py` — 移行回帰diff（実配信ログで旧torch実装と比較）
- `apppaths.py` — 凍結時に HF キャッシュを exe 隣 `models/` に固定＋MOTW自己解除
- `defaults\` — 配布用の汎用データ（hotwords/effects/presets/boxes/banned）。
  個人用の単語帳やDLフォント指定を配布物に入れないため、パッケージ時はこちらが優先コピーされる
- `README_TESTER.txt` / `マニュアル.html` / `ブロック解除.bat` — テスターへ同梱

同梱版で使うモデル:
- ASR `reazonspeech-k2-v2`（`%USERPROFILE%\.cache\huggingface\hub` から）
- 句読点ONNX＋翻訳CT2（リポジトリ直下 `models_conv\` から。`tools\convert_models.py` で生成）

注意点:
- 配布先PCには WebView2 ランタイムが必要（Windows 11 は標準搭載）
- FuguMT の**変換済みモデルの配布（HFリポジトリ含む）は CC BY-SA 4.0 の継承が必要**（CREDITS.md 参照）
