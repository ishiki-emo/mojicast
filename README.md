# Mojicast

**配信用リアルタイム字幕アプリ — 音声認識・句読点付け・英訳まで、すべてローカルで。**

_A fully-offline real-time captioning app for live streaming: Japanese speech recognition, punctuation, and English translation — all running locally on your PC._

日本語の話し声をリアルタイムに字幕化し、OBS のブラウザソースに重ねられます。音声はネットに送られず、
モデルの推論も含めてすべてお使いの PC の中だけで動きます。

<!-- TODO: スクリーンショット/GIF を docs/ に置いてここに貼る
![cockpit](docs/screenshot-cockpit.png)
![overlay](docs/screenshot-overlay.png)
-->

## 特長

- 🎙 **日本語音声認識**（ReazonSpeech k2）— ホットワード登録で固有名詞の誤認識を軽減
- ✍️ **句読点の自動付与**（日本語 BERT）
- 🌐 **英訳の併記**（FuguMT・ニューラル機械翻訳）— 確定行の下に小さく表示
- 🎬 **OBS 連携** — ブラウザソースに URL を入れるだけ
- ✨ **エフェクト/スタイル** — 単語装飾・パーティクル・プリセット・レイアウト
- 🔌 **完全オフライン** — 音声もテキストも外部送信なし
- 🖥 **Windows でダブルクリック起動**（配布版は Python 不要）

## 仕組み

```
マイク → VAD(Silero) → 音声認識(ReazonSpeech k2) → 句読点(BERT) ┬→ 字幕(OBSオーバーレイ)
                                                    └→ 英訳(FuguMT) ┘
```

内部は pywebview のデスクトップ窓＋ローカル HTTP/SSE サーバで、OBS 用オーバーレイと
設定 UI を同じサーバから配信します。英訳（FuguMT）は Transformer による生成型の
ニューラル機械翻訳で、いわゆる「生成 AI」を部品として利用しています。

利用するモデル・ライブラリとそのライセンスは [CREDITS.md](CREDITS.md) を参照してください。

## 動作環境

| | 最低 | 推奨 |
|---|---|---|
| OS | Windows 10（64bit） | Windows 11 |
| CPU | 4コア8スレッド（2017年以降の Core i5 / Ryzen 5 相当） | 6コア以上（ゲーム配信と併用時） |
| メモリ | 8GB（英訳ON時、本アプリ実測 約2GB） | 16GB |
| ストレージ | 空き5GB | SSD |

ほか: マイク / WebView2（Win11標準搭載） / 初回のみネット接続（モデル約1.2GB DL）。
OBS併用時はGPUエンコード（NVENC / AMF）推奨。開発には Python 3.11。

## 使い方（配布版）

1. リリースの Zip をローカルに解凍
2. `Mojicast.exe` をダブルクリック
3. マイクを選んで ▶開始 / OBS のブラウザソースに `http://localhost:8765`

詳しい操作は同梱の `マニュアル.html` を参照。

## 開発セットアップ

```bash
git clone <this-repo>
cd mojicast
python -m venv .venv && . .venv/Scripts/activate   # Windows

# 1) ReazonSpeech k2-asr（上流から）
git clone https://github.com/reazon-research/ReazonSpeech
pip install ./ReazonSpeech/pkg/k2-asr

# 2) 残りの依存（torch / transformers は不要）
pip install -r requirements.txt

# 起動
python app.py
```

初回起動時、必要な AI モデルが Hugging Face から自動ダウンロードされます
（合計約 1.2GB・進捗はコックピットに表示）。以降はキャッシュから読み込み、オフラインで動作します。
句読点・翻訳は変換済みモデル（ONNX / CTranslate2）を使用します。モデルを変換し直す場合のみ
`tools/convert_models.py` を参照してください（torch / transformers が必要）。

## 配布用パッケージ（PyInstaller）

```powershell
pyinstaller --noconfirm Mojicast.spec
.\build_bundle.ps1              # 同梱版（モデル込み・完全オフライン・約3.1GB）
.\build_bundle.ps1 -NoModels    # 軽量版（モデル無し・初回DL・約1GB）
```

詳細は [STUDIO_README.md](STUDIO_README.md)。

## リポジトリ構成

| パス | 内容 |
|---|---|
| `app.py` | エントリポイント（pywebview 窓） |
| `app_server.py` | ローカル HTTP/SSE サーバ・設定 API |
| `engine.py` | 認識エンジン（VAD→ASR→句読点→英訳） |
| `asr_model.py` / `punct.py` / `translate.py` / `vocab.py` | モデルローダと語彙処理 |
| `apppaths.py` | 実行時パス解決（開発/凍結 両対応） |
| `overlay.html` / `ui/` | OBS オーバーレイと設定 UI |
| `defaults/` | 既定データ（初回に個人環境へ複製される種） |
| `packaging`（`*.spec` / `build_bundle.ps1`） | 配布ビルド |

設定・単語帳などの実行時データは `data/` フォルダに集約され、各自の環境で
生成・編集されるもので、リポジトリには含まれません（初回起動時に `defaults/` から複製。
旧バージョンでルート直下にあったデータは初回起動時に `data/` へ自動移行）。

## ライセンス

- 本アプリのコード: [MIT License](LICENSE)
- 利用する AI モデル・ライブラリ・フォント: それぞれ個別のライセンス（[CREDITS.md](CREDITS.md)）

特に翻訳モデル FuguMT は CC BY-SA 4.0 です。既定の初回 DL 方式では各自が配布元から取得するため
問題ありませんが、モデルを同梱して再配布する場合は各ライセンスの遵守が必要です。
