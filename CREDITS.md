# サードパーティ クレジットとライセンス

本アプリ **Mojicast** は、以下のオープンソースソフトウェアと学習済みモデルを利用しています。
各制作者・研究機関に深く感謝します。

---

## 学習済みモデル

モデルの重みはリポジトリには含まれません。初回起動時に Hugging Face から各自の環境へ
ダウンロードされます（＝本アプリはモデルweightを再配布しません）。各モデルのライセンスは
配布元に従います。

| モデル | 用途 | ライセンス | 配布元 |
|---|---|---|---|
| ReazonSpeech k2 v2 | 日本語音声認識 | Apache-2.0 | [reazon-research/reazonspeech-k2-v2](https://huggingface.co/reazon-research/reazonspeech-k2-v2) |
| BERT base Japanese char v3 | 句読点付けの土台 | ※HFページ参照 | [tohoku-nlp/bert-base-japanese-char-v3](https://huggingface.co/tohoku-nlp/bert-base-japanese-char-v3) |
| BERT Japanese punctuation | 句読点の重み | Apache-2.0 | [bobfromjapan/bert_japanese_punctuation](https://huggingface.co/bobfromjapan/bert_japanese_punctuation) |
| FuguMT ja-en | 日→英翻訳 | **CC BY-SA 4.0** | [staka/fugumt-ja-en](https://huggingface.co/staka/fugumt-ja-en) |
| Silero VAD | 無音（発話区間）検出 | MIT | [snakers4/silero-vad](https://github.com/snakers4/silero-vad) |

> **FuguMT について**: CC BY-SA 4.0 のため、モデルを**再配布する場合**は表示（クレジット）と
> 継承（同一条件での配布）が必要です。本アプリの既定（初回DL方式）ではユーザー各自が配布元から
> 取得するため再配布には当たりませんが、モデルを同梱した配布物を作る場合はライセンス遵守が必要です。

---

## ライブラリ

すべて寛容型ライセンス（Apache-2.0 / BSD / MIT）です。

| ライブラリ | 役割 | ライセンス |
|---|---|---|
| PyTorch (torch) | 推論エンジン | BSD-3-Clause |
| Transformers | BERT/MarianMT の実行 | Apache-2.0 |
| tokenizers / safetensors | トークナイザ・重み形式 | Apache-2.0 |
| huggingface-hub | モデル取得 | Apache-2.0 |
| sherpa-onnx (+core) | k2 ASR / VAD 実行 | Apache-2.0 |
| ReazonSpeech (k2-asr) | ASR ラッパ | Apache-2.0 |
| NumPy | 数値計算 | BSD-3-Clause |
| pywebview | デスクトップGUI（WebView） | BSD-3-Clause |
| pythonnet / clr_loader | WebView2 バックエンド | MIT |
| sounddevice | マイク入力 | MIT |
| bottle / proxy_tools | pywebview 依存 | MIT |
| fugashi | 形態素解析ラッパ | MIT |
| unidic-lite | 辞書（同梱UniDic辞書に独自条項あり） | MIT |

各ライブラリの完全なライセンス文は、それぞれの配布パッケージに含まれます。

---

## フォント

プリセットは Windows 標準フォントを既定にしています。ユーザーが別途インストールした
フォント（例: 源暎、ラノベPOP 等）を指定することもできますが、**それらのフォントファイルは
本アプリには同梱されておらず**、各フォントのライセンスは各配布元に従います。

---

## 音源・テストデータ

リポジトリには個人のテスト録音等は含まれません。
