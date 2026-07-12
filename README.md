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
- ✍️ **句読点の自動付与**（日本語 BERT）・**数字の算用数字化**（三十五→35、1万5000円）
- 🌐 **英訳の併記**（FuguMT）— 配信用語の組み込み辞書つき（配信→stream 等）、
  固有名詞はユーザー英訳辞書で固定可能
- 🗂 **単語プロファイル** — 雑談用・ゲーム用・歌枠用など、配信の性質ごとに単語セットを切替
- ✨ **エフェクト/スタイル** — 単語装飾・パーティクル・プリセット・レイアウト。
  `.mojipack` ファイルでスタイルの配布・取り込みも可能
- 🎬 **OBS 連携** — ブラウザソースに URL を入れるだけ
- 🔌 **完全オフライン** — 音声もテキストも外部送信なし
- 🖥 **Windows でダブルクリック起動**（配布版は Python 不要・Zip 約180MB）

## 仕組み

```
マイク → VAD → 音声認識 → 単語置換 → 数字正規化 → 句読点 ┬→ 字幕(OBSオーバーレイ)
      (Silero)  (k2)     (ホットワード) (numnorm)   (BERT) └→ 英訳(FuguMT) ┘
```

推論はすべて軽量ランタイムで動きます（**PyTorch / Transformers 非依存**）:

| 処理 | モデル | ランタイム |
|---|---|---|
| 音声認識 / VAD | ReazonSpeech k2 v2 / Silero VAD | sherpa-onnx |
| 句読点 | 日本語BERT（ONNX変換済み） | ONNX Runtime |
| 英訳 | FuguMT ja-en（CTranslate2変換済み） | CTranslate2 + SentencePiece |

内部は pywebview のデスクトップ窓＋ローカル HTTP/SSE サーバで、OBS 用オーバーレイと
設定 UI を同じサーバから配信します。英訳（FuguMT）は Transformer による生成型の
ニューラル機械翻訳で、いわゆる「生成 AI」を部品として利用しています。

利用するモデル・ライブラリとそのライセンスは [CREDITS.md](CREDITS.md) を参照してください。

## 動作環境

| | 最低 | 推奨 |
|---|---|---|
| OS | Windows 10（64bit） | Windows 11 |
| CPU | 4コア8スレッド（2017年以降の Core i5 / Ryzen 5 相当） | 6コア以上（ゲーム配信と併用時） |
| メモリ | 8GB | 16GB |
| ストレージ | 空き3GB | SSD |

ほか: マイク / WebView2（Win11標準搭載） / 初回のみネット接続（モデル約1.2GB DL）。
OBS併用時はGPUエンコード（NVENC / AMF）推奨。開発には Python 3.11。

## 使い方（配布版）

1. [リリース](../../releases)の Zip をローカルに解凍
2. `Mojicast.exe` をダブルクリック
3. マイクを選んで ▶開始 / OBS のブラウザソースに `http://localhost:8765`

初回の ▶開始 時に AI モデル（約1.2GB）を自動ダウンロードします。以降はオフラインで動作します。
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

句読点・翻訳は変換済みモデル（ONNX / CTranslate2）を Hugging Face の配布リポジトリ
（[mojicast-punct-onnx](https://huggingface.co/ishiki-emo/mojicast-punct-onnx) /
[mojicast-fugumt-ja-en-ct2](https://huggingface.co/ishiki-emo/mojicast-fugumt-ja-en-ct2)）
から取得します。モデルを変換し直す場合のみ `tools/convert_models.py` を使ってください
（このときだけ torch / transformers が必要）。移行時の品質検証は
`bench/regression_diff.py`（実配信ログでの新旧比較）で行います。

## 配布用パッケージ（PyInstaller）

```powershell
pyinstaller --noconfirm Mojicast.spec
.\build_bundle.ps1 -NoModels    # 軽量版（モデル無し・初回DL・約0.43GB / Zip 約178MB）
.\build_bundle.ps1              # 同梱版（モデル込み・完全オフライン・約1.7GB）
.\smoke_test.ps1 -Fresh         # リリース前検証（新規ユーザーのDL経路を再現）
```

詳細は [STUDIO_README.md](STUDIO_README.md)。

## リポジトリ構成

| パス | 内容 |
|---|---|
| `app.py` | エントリポイント（pywebview 窓） |
| `app_server.py` | ローカル HTTP/SSE サーバ・設定/プロファイル/mojipack API |
| `engine.py` | 認識エンジン（VAD→ASR→後処理→英訳） |
| `asr_model.py` / `punct.py` / `translate.py` | 各モデルのローダと推論 |
| `vocab.py` / `numnorm.py` | ホットワード語彙処理 / 漢数字→算用数字の正規化 |
| `wordstore.py` | ユーザーデータ（data/）の管理・単語プロファイルの合成 |
| `overlay.html` / `ui/` | OBS オーバーレイと設定 UI |
| `defaults/` | 既定データ（初回に個人環境へ複製される種） |
| `tools/` | モデル変換（ONNX / CTranslate2 化・開発時のみ） |
| `bench/` | 移行ベンチ・回帰diff（アプリ非組込） |
| `apppaths.py` | 実行時パス解決（開発/凍結 両対応） |
| `Mojicast.spec` / `build_bundle.ps1` / `smoke_test.ps1` | 配布ビルドと検証 |
| `ROADMAP.md` / `COLLAB_DESIGN.md` | 将来構想・コラボ字幕の設計メモ |

設定・単語帳などの実行時データは `data/` フォルダに集約され、各自の環境で
生成・編集されるもので、リポジトリには含まれません（初回起動時に `defaults/` から複製。
旧バージョンでルート直下にあったデータは初回起動時に `data/` へ自動移行）。
バックアップは `data/` フォルダのコピーだけで済みます。

## ライセンス

- 本アプリのコード: [MIT License](LICENSE)
- 利用する AI モデル・ライブラリ・フォント: それぞれ個別のライセンス（[CREDITS.md](CREDITS.md)）

翻訳モデル FuguMT は CC BY-SA 4.0 のため、変換済みモデルの配布リポジトリは
同ライセンスを継承し原作者クレジットを掲載しています。モデルを同梱した配布物を
作る場合も各ライセンスの遵守が必要です（詳細は [CREDITS.md](CREDITS.md)）。
