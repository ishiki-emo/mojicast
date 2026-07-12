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
| BERT base Japanese char v3 | 句読点付けの土台（ONNX変換して利用） | Apache-2.0 | [tohoku-nlp/bert-base-japanese-char-v3](https://huggingface.co/tohoku-nlp/bert-base-japanese-char-v3) |
| BERT Japanese punctuation | 句読点の重み（同上） | Apache-2.0 | [bobfromjapan/bert_japanese_punctuation](https://huggingface.co/bobfromjapan/bert_japanese_punctuation) |
| FuguMT ja-en | 日→英翻訳（CTranslate2変換して利用） | **CC BY-SA 4.0** | [staka/fugumt-ja-en](https://huggingface.co/staka/fugumt-ja-en) |
| Silero VAD | 無音（発話区間）検出 | MIT | [snakers4/silero-vad](https://github.com/snakers4/silero-vad) |

> **FuguMT の変換版について**: 変換済みモデルの配布は**派生物の再配布**にあたるため、
> 配布リポジトリの FuguMT 変換版（CTranslate2形式）には CC BY-SA 4.0 が継承されます。
> 配布リポジトリには原作者（Fugu Machine Translator / staka 氏）のクレジットと
> 同ライセンス表記を必ず掲載してください。モデル同梱の配布物を作る場合も同様です。

---

## ライブラリ

すべて寛容型ライセンス（Apache-2.0 / BSD / MIT）です。

| ライブラリ | 役割 | ライセンス |
|---|---|---|
| ONNX Runtime | 句読点BERT の実行 | MIT |
| CTranslate2 | FuguMT 翻訳の実行 | MIT |
| SentencePiece | 翻訳トークナイザ | Apache-2.0 |
| huggingface-hub | モデル取得 | Apache-2.0 |
| sherpa-onnx (+core) | k2 ASR / VAD 実行 | Apache-2.0 |
| ReazonSpeech (k2-asr) | ASR ラッパ | Apache-2.0 |
| NumPy | 数値計算 | BSD-3-Clause |
| pywebview | デスクトップGUI（WebView） | BSD-3-Clause |
| pythonnet / clr_loader | WebView2 バックエンド | MIT |
| sounddevice | マイク入力 | MIT |
| bottle / proxy_tools | pywebview 依存 | MIT |

モデル変換時（開発作業のみ・配布物には含まれない）には PyTorch (BSD-3-Clause) と
Transformers (Apache-2.0) を使用しています。

各ライブラリの完全なライセンス文は、それぞれの配布パッケージに含まれます。

---

## フォント

プリセットは Windows 標準フォントを既定にしています。ユーザーが別途インストールした
フォント（例: 源暎、ラノベPOP 等）を指定することもできますが、**それらのフォントファイルは
本アプリには同梱されておらず**、各フォントのライセンスは各配布元に従います。

---

## 音源・テストデータ

リポジトリには個人のテスト録音等は含まれません。
