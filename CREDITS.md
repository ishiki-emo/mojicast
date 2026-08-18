# サードパーティ クレジットとライセンス

本アプリ **Mojicast** は、以下のオープンソースソフトウェアと学習済みモデルを利用しています。
各制作者・研究機関に深く感謝します。

---

## 学習済みモデル

モデルの重みはリポジトリには含まれません。初回起動時に Hugging Face から各自の環境へ
ダウンロードされます。句読点・翻訳モデルは軽量化のため、下記オリジナルを変換した
**変換済みモデル**（ONNX / CTranslate2 形式。`tools/convert_models.py` で生成）を
配布リポジトリから取得します。各モデルのライセンスはオリジナルの配布元に従います。

| モデル | 用途 | ライセンス | オリジナル配布元 |
|---|---|---|---|
| ReazonSpeech k2 v2 | 日本語音声認識 | Apache-2.0 | [reazon-research/reazonspeech-k2-v2](https://huggingface.co/reazon-research/reazonspeech-k2-v2) |
| SenseVoice small | 多言語音声認識（中・英・日・韓・広東語） | **FunASR Model License** | [FunAudioLLM/SenseVoiceSmall](https://huggingface.co/FunAudioLLM/SenseVoiceSmall)（作者: Alibaba Group） |
| BERT base Japanese char v3 | 句読点付けの土台（ONNX変換して利用） | Apache-2.0 | [tohoku-nlp/bert-base-japanese-char-v3](https://huggingface.co/tohoku-nlp/bert-base-japanese-char-v3) |
| BERT Japanese punctuation | 句読点の重み（同上） | Apache-2.0 | [bobfromjapan/bert_japanese_punctuation](https://huggingface.co/bobfromjapan/bert_japanese_punctuation) |
| FuguMT ja-en | 日→英翻訳（CTranslate2変換して利用） | **CC BY-SA 4.0** | [staka/fugumt-ja-en](https://huggingface.co/staka/fugumt-ja-en) |
| M2M-100 418M | 中国語・インドネシア語等の多言語翻訳（CTranslate2変換して利用） | MIT | [facebook/m2m100_418M](https://huggingface.co/facebook/m2m100_418M) |
| Silero VAD | 無音（発話区間）検出 | MIT | [snakers4/silero-vad](https://github.com/snakers4/silero-vad) |
| Zipformer audio tagging (small) | 音イベント検出（リアクション演出。笑い声・拍手等） | Apache-2.0 | [k2-fsa/sherpa-onnx-zipformer-small-audio-tagging-2024-04-15](https://huggingface.co/k2-fsa/sherpa-onnx-zipformer-small-audio-tagging-2024-04-15) |

> **FuguMT の変換版について**: 変換済みモデルの配布は**派生物の再配布**にあたるため、
> 配布リポジトリの FuguMT 変換版（CTranslate2形式）には CC BY-SA 4.0 が継承されます。
> 配布リポジトリには原作者（Fugu Machine Translator / staka 氏）のクレジットと
> 同ライセンス表記を必ず掲載してください。モデル同梱の配布物を作る場合も同様です。

> **SenseVoice について**: SenseVoice は Alibaba Group（Tongyi Lab / FunAudioLLM チーム）が
> 開発・公開する音声認識モデルです。実際にダウンロードされるのは csukuangfj 氏が
> sherpa-onnx 向けに変換した int8 版
> （[csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17](https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17)）で、
> ライセンスはオリジナルの
> [FunASR Model Open Source License](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE)
> に従います（出典・作者情報の帰属表示とモデル名の保持が条件）。

> **変換済みモデルの実際の取得元**: 句読点BERTは
> [ishiki-emo/mojicast-punct-onnx](https://huggingface.co/ishiki-emo/mojicast-punct-onnx)、
> FuguMTは [ishiki-emo/mojicast-fugumt-ja-en-ct2](https://huggingface.co/ishiki-emo/mojicast-fugumt-ja-en-ct2)、
> M2M-100は [ishiki-emo/mojicast-m2m100-ct2](https://huggingface.co/ishiki-emo/mojicast-m2m100-ct2)
> からダウンロードされます。各リポジトリのREADMEに原作者クレジット・元モデルへのリンク・
> 継承ライセンスを掲載しています。

> **Silero VAD について**: `silero_vad.onnx`（MIT・[snakers4/silero-vad](https://github.com/snakers4/silero-vad)）
> のみ、DL方式ではなく本体・配布物に同梱しています。

> **音イベント検出モデルについて**: リアクション演出の音イベント検出には、
> Next-gen Kaldi（k2-fsa）プロジェクトが公開する Zipformer 音声タグ付けモデル
> （Apache-2.0・icefall で学習）を利用します。他モデルと同じく非同梱で、機能を
> オンにしたときだけダウンロードされます。同梱のクラス定義
> `class_labels_indices.csv` は Google の
> [AudioSet](https://research.google.com/audioset/) オントロジー（CC BY 4.0）に
> 由来します。

---

## ライブラリ

アプリが直接利用する主要ライブラリです（いずれも寛容型ライセンス）。

| ライブラリ | 役割 | ライセンス |
|---|---|---|
| ONNX Runtime | 句読点BERT の実行 | MIT |
| CTranslate2 | FuguMT / M2M-100 翻訳の実行 | MIT |
| SentencePiece | 翻訳トークナイザ | Apache-2.0 |
| OpenCC | 中国語の台湾正体字・香港繁体字への地域表記変換 | Apache-2.0 |
| huggingface-hub | モデル取得 | Apache-2.0 |
| sherpa-onnx (+core) | k2 / SenseVoice ASR・VAD 実行 | Apache-2.0 |
| NumPy | 数値計算 | BSD-3-Clause |
| pywebview | デスクトップGUI（WebView） | BSD-3-Clause |
| pythonnet / clr_loader | WebView2 バックエンド（Windows版） | MIT |
| PyObjC (AppKit / WebKit ほか) | Cocoa バックエンド（macOS版） | MIT |
| sounddevice | マイク入力 | MIT |

### 配布版に含まれるその他のコンポーネント

上記の依存として、配布パッケージには次のコンポーネントも含まれます。

| コンポーネント | 由来 | ライセンス |
|---|---|---|
| CPython 3.11 本体 | 実行環境 | PSF License |
| PyInstaller ブートローダ | 実行ファイル化 | GPL-2.0（例外条項により本アプリのライセンスへは非伝播） |
| PortAudio | sounddevice の音声入出力 | MIT |
| OpenBLAS | NumPy の線形代数 | BSD-3-Clause |
| Intel OpenMP (libiomp5md) | CTranslate2 の並列実行 | Intel Simplified Software License |
| Microsoft WebView2 SDK / .NET ランタイム | GUI（Windows版） | Microsoft ライセンス / MIT |
| certifi | HTTPS 証明書 | MPL-2.0 |
| Pillow / PyYAML / protobuf / hf_xet / tqdm / click ほか | 依存ライブラリ | MIT / BSD / Apache-2.0 等 |

モデル変換時（開発作業のみ・配布物には含まれない）には PyTorch (BSD-3-Clause) と
Transformers (Apache-2.0) を使用しています。

主要パッケージのライセンス文は配布物の `_internal` フォルダ内（各パッケージの
`*.dist-info/licenses` 等）に含まれます。各ライブラリのライセンス全文は、
それぞれの公式配布元でも参照できます。

---

## フォント

プリセットは Windows 標準フォントを既定にしています。ユーザーが別途インストールした
フォント（例: 源暎、ラノベPOP 等）を指定することもできますが、**それらのフォントファイルは
本アプリには同梱されておらず**、各フォントのライセンスは各配布元に従います。

---

## 音源・テストデータ

リポジトリには個人のテスト録音等は含まれません。
