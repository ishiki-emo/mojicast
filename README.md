# Mojicast

**配信用リアルタイム字幕アプリ — 音声認識・句読点付け・英訳まで、すべてローカルで。**

_A fully-offline real-time captioning app for live streaming: Japanese speech recognition, punctuation, and English translation — all running locally on your PC._

日本語の話し声をリアルタイムに字幕化し、OBS のブラウザソースに重ねられます。音声はネットに送られず、
モデルの推論も含めてすべてお使いの PC の中だけで動きます。

[![1対1コラボ字幕のデモ — クリックでデモ動画へ](docs/images/hero_video.jpg)](https://youtu.be/GObqilowmkU)

<p align="center">▶ <b><a href="https://youtu.be/GObqilowmkU">30秒デモ動画を見る（YouTube）</a></b> — 2人の字幕・翻訳・エフェクトが動く様子<br>
テスト協力: <a href="https://x.com/oftunlab">絵咲まくらさん</a></p>

| コックピット（メイン画面） | 1対1コラボ中（自分/相手の見た目を個別に割当） |
|---|---|
| ![コックピット](docs/images/cockpit_solo.png) | ![コラボ中のコックピット](docs/images/cockpit_collab.png) |

📖 **画像つきの詳しい使い方は [docs/MANUAL.md](docs/MANUAL.md)**（配布版には同内容の `マニュアル.html` を同梱）

## 🔍 逆引き — やりたいことから探す

| やりたいこと | 見る場所 |
|---|---|
| とにかく動かしたい | [使い方（配布版）](#使い方配布版) |
| OBS に字幕を出したい | [マニュアル 4章](docs/MANUAL.md#4-obs-に字幕を出す) |
| 字幕の見た目を手早く変えたい | プリセット切替 — [マニュアル 5章](docs/MANUAL.md#5-スタジオ見た目のカスタマイズと単語登録) |
| 自分だけの字幕デザインを作りたい | [スタイル・レイアウト作成ガイド](docs/STYLE_GUIDE.md) |
| 「ありがとう」でキラキラを飛ばしたい | [エフェクトガイド](docs/EFFECT_GUIDE.md) |
| 名前・ゲーム名が誤認識される | 認識させる単語 — [マニュアル 5章](docs/MANUAL.md#5-スタジオ見た目のカスタマイズと単語登録) |
| 英語・中国語などの字幕も併記したい | [マニュアル 6章](docs/MANUAL.md#6-翻訳の併記英訳中国語訳) |
| 日本語以外の言語で配信したい | 多言語モデル — [マニュアル 3章](docs/MANUAL.md#3-コックピットメイン画面) |
| 海外の配信を日本語字幕で見たい | 多言語認識＋翻訳先=日本語 — [マニュアル 6章](docs/MANUAL.md#6-翻訳の併記英訳中国語訳) |
| コラボ相手の字幕も出したい | [マニュアル 7章](docs/MANUAL.md#7-1対1コラボ字幕) |
| 歌枠で歌詞みたいに散らしたい | リリックモード — [スタイルガイド 4章](docs/STYLE_GUIDE.md#4-レイアウトボックスの項目とコツ) |
| 作ったスタイルを配りたい・もらいたい | mojipack — [スタイルガイド 7章](docs/STYLE_GUIDE.md#7-書き出し取り込みmojipack-みんなで使おう) |
| 配信後の文字起こしが欲しい | ログ保存 — [マニュアル 8章](docs/MANUAL.md#8-フォルダとアップデート) |
| 設定をバックアップしたい | `data\` をコピーするだけ — [マニュアル 8章](docs/MANUAL.md#8-フォルダとアップデート) |
| 動作が重い | 認識モデルを「多言語」に（負荷約1/3）— [マニュアル 9章](docs/MANUAL.md#9-困ったときは) / [テクニカルガイド](docs/TECH_GUIDE.md) |
| うまく動かない | [マニュアル 9章](docs/MANUAL.md#9-困ったときは) |

## 特長

- 🎙 **日本語音声認識**（ReazonSpeech k2）— ホットワード登録で固有名詞の誤認識を軽減
- 🌏 **多言語認識**（SenseVoice）— 中・英・日・韓・広東語。CPU負荷が軽く、
  低スペック機の軽量モードとしても使える
- ✍️ **句読点の自動付与**（日本語 BERT）・**数字の算用数字化**（三十五→35、1万5000円）
- 🌐 **翻訳の併記** — 英訳（FuguMT）／中・日・韓（M2M-100）を切替可能。
  認識言語×翻訳先から使用モデルを自動選択（中国語認識＋英訳=中→英 など）。
  配信用語の組み込み辞書つき（配信→stream / 直播 等）
- 🎧 **1対1コラボ字幕** — Discord 等の通話音声から**相手の字幕も表示**
  （WASAPI プロセスループバックで通話アプリの音だけを取り込み。相手側の準備は一切不要・仮想ケーブルも不要）
- 🗂 **単語プロファイル** — 雑談用・ゲーム用・歌枠用など、配信の性質ごとに単語セットを切替
- ✨ **エフェクト/スタイル** — 単語装飾・パーティクル・プリセット・レイアウト。
  `.mojipack` ファイルでスタイルの配布・取り込みも可能
- 🎬 **OBS 連携** — ブラウザソースに URL を入れるだけ
- 🔌 **完全オフライン** — 音声もテキストも外部送信なし
- 🖥 **Windows でダブルクリック起動**（配布版は Python 不要・Zip 約90MB）

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
| OS | Windows 10 2004 以降（64bit） | Windows 11 |
| CPU | 4コア8スレッド・AVX2対応（2017年以降の Core i5 / Ryzen 5 相当） | Ryzen 7000/9000（最適）/ Intel 12世代以降・AVX-512対応の10〜11世代 |
| メモリ | 8GB | 16GB |
| ストレージ | 空き5GB | SSD |

負荷の本体は int8 の ONNX 推論のため、コア数より **CPU の世代**（AVX-512 VNNI / AVX-VNNI 対応）が
効きます。世代別の適性表・実測データは [docs/TECH_GUIDE.md](docs/TECH_GUIDE.md) を参照。
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

# 依存を入れる（torch / transformers / ReazonSpeechパッケージは不要）
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
.\build_bundle.ps1 -NoModels    # 軽量版（モデル無し・初回DL・約0.22GB / Zip 約91MB）
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
