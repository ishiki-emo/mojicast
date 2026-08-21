"""
日本語テキストに句読点(、。)を復元するモジュール（ONNX Runtime版）

モデル: bobfromjapan/bert_japanese_punctuation
        (tohoku-nlp/bert-base-japanese-char-v3 + 線形層のトークン分類)
        を tools/convert_models.py で ONNX 化したもの。

torch / transformers 非依存。トークナイザは文字単位（1文字=1トークン）なので
vocab.txt から自前で構築する。k2 など句読点を出さないASRの出力を、
認識後にここへ通して整形する。初回呼び出し時にロード（遅延ロード）。CPUで動作。

旧torch版との差分: 語彙に無い文字（絵文字など）を旧版は出力から落としていたが、
本版は元の文字をそのまま残す（判定はUNKとして計算するので句読点位置は同等）。

精度は2種類（アプリ設定の「高精度モード」が認識モデルと一緒に切り替える）:
  int8（既定）  109MB / 常駐+154MB / 1行約5ms
  fp32（高精度）364MB / 常駐+390MB / 1行約11ms
int8はfp32と判定が89.5%一致し、不一致の7割は文末の「。」の欠落（本文は変わらない）。
実測は bench/README.md、変換は tools/convert_models.py の quantize_punct()。
"""
import os
import re

import numpy as np
import huggingface_hub as hf

from apppaths import BASE

# 数字の直後・助数詞の直前に打たれた句読点を除去する（例:「1987、年」→「1987年」）。
# モデルは文字単位BERTのため、長い数字列＋短い文脈だと桁の途中や単位の前に
# 句読点を打つことがある。数字→数字（15、16歳 のような列挙）は正当なので触らない。
_NUM_PUNC_FIX = re.compile(
    r"(\d)[、。](?=[年月日時分秒歳才人個円回本枚台匹冊話曲位点倍万億兆つ杯件番週%％])")

_REPO_ID = "ishiki-emo/mojicast-punct-onnx"   # 変換済みモデルの配布リポジトリ
_SUBDIR = "punct"                              # ローカル models_conv/ 内のフォルダ名
_FILES = {"int8": "punct_bert.int8.onnx",      # 既定（109MB）
          "fp32": "punct_bert.onnx"}           # 高精度モード（364MB）

_sess = None
_loaded_precision = None   # ロード済みモデルの精度（切替時の再ロード判定に使う）
_vocab = None          # 文字 → トークンID
_cls_id = _sep_id = _unk_id = None


def _resolve(filename, download=True):
    """モデルファイルのパス解決: models_conv/（開発・手動配置）→ HFキャッシュ → DL"""
    local = os.path.join(BASE, "models_conv", _SUBDIR, filename)
    if os.path.exists(local):
        return local
    try:
        return hf.hf_hub_download(_REPO_ID, filename, local_files_only=True)
    except Exception:
        if not download:
            raise
        return hf.hf_hub_download(_REPO_ID, filename)


def model_file(precision: str = "int8") -> str:
    """精度名 → モデルファイル名（未知の値は既定のint8へ倒す）"""
    return _FILES.get(precision, _FILES["int8"])


def cached(precision: str = "int8") -> bool:
    """モデルがローカルにあるか（DLはしない。初回DLサイズ見積り用）"""
    try:
        _resolve("vocab.txt", download=False)
        _resolve(model_file(precision), download=False)
        return True
    except Exception:
        return False


def loaded_precision():
    """常駐中のモデルの精度（未ロードなら None）"""
    return _loaded_precision


def unload():
    """句読点モデルを解放してメモリを返す（次回使用時に再ロード）。
    句読点内蔵の認識モデル（SenseVoice等）へ切り替えたときにBERTを常駐させないため。"""
    global _sess, _loaded_precision
    _sess = None
    _loaded_precision = None


def load_punctuator(num_threads: int = 4, precision: str = "int8"):
    """モデルと語彙をロード（初回、および精度が変わったときのみ実行）"""
    global _sess, _vocab, _cls_id, _sep_id, _unk_id, _loaded_precision
    if _sess is not None and _loaded_precision == precision:
        return
    vocab = {}
    with open(_resolve("vocab.txt"), encoding="utf-8") as f:
        for i, line in enumerate(f):
            vocab[line.rstrip("\n")] = i
    # 語彙が壊れていたら句読点は使わせない（呼び出し側が機能を無効化して字幕を守る）
    if len(vocab) < 100 or "[CLS]" not in vocab or "[SEP]" not in vocab:
        raise RuntimeError(f"句読点トークナイザの語彙が不正です (size={len(vocab)})")
    _vocab = vocab
    _cls_id = vocab["[CLS]"]
    _sep_id = vocab["[SEP]"]
    _unk_id = vocab.get("[UNK]", 1)

    import onnxruntime as ort
    so = ort.SessionOptions()
    so.intra_op_num_threads = num_threads
    # スピンウェイト（仕事の合間の空回り待機）を無効化。字幕はチャンク間の待ち時間が
    # 長いワークロードで、スピンはCPU使用率を水増しして配信ソフト等と競合するだけ。
    # スリープ起床の遅延増は数µs〜msで、音声チャンク（数百ms）に対して無視できる。
    so.add_session_config_entry("session.intra_op.allow_spinning", "0")
    sess = ort.InferenceSession(_resolve(model_file(precision)), so,
                                providers=["CPUExecutionProvider"])
    _sess = sess
    _loaded_precision = precision
    # ロード直後の自己診断: 1文処理して空なら異常として失敗させる
    if not add_punctuation("これはてすとです"):
        _sess = None
        _loaded_precision = None
        raise RuntimeError("句読点モデルの自己診断に失敗（出力が空）")


def add_punctuation(text: str, comma_thresh: float = 0.1,
                    period_thresh: float = 0.1, max_length: int = 256) -> str:
    """
    テキストに句読点を復元して返す

    Args:
        text: 句読点なし（または混在）の日本語テキスト
        comma_thresh: 読点(、)を打つ確率しきい値。上げると、が減る
        period_thresh: 句点(。)を打つ確率しきい値。上げると。が減る
        max_length: 一度に処理する文字数（長文は分割）

    Returns:
        句読点入りテキスト
    """
    if _sess is None:
        load_punctuator()

    text = text.replace("、", "").replace("。", "")
    if not text:
        return text

    out = []
    for i in range(0, len(text), max_length):
        chunk = text[i:i + max_length]
        ids = [_cls_id] + [_vocab.get(ch, _unk_id) for ch in chunk] + [_sep_id]
        input_ids = np.array([ids], dtype=np.int64)
        attention_mask = np.ones_like(input_ids)
        logits = _sess.run(None, {"input_ids": input_ids,
                                  "attention_mask": attention_mask})[0][0]
        probs = 1.0 / (1.0 + np.exp(-logits))   # sigmoid → [:, (、確率, 。確率)]
        for j, ch in enumerate(chunk):
            comma, period = probs[j + 1]        # +1 は先頭の [CLS] ぶん
            if period > period_thresh:
                out.append(ch + "。")
            elif comma > comma_thresh:
                out.append(ch + "、")
            else:
                out.append(ch)
    result = _NUM_PUNC_FIX.sub(r"\1", "".join(out))
    # 万一空になったら原文を返す＝字幕を消さない
    return result if result else text


if __name__ == "__main__":
    import sys
    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    samples = [
        "きょうはいい天気ですね明日は雨が降るそうです傘を持っていきましょう",
        "こんにちは私はえもですよろしくお願いします配信を始めます",
        "本日の会議では新機能の仕様について議論しますまず画面設計から確認しましょう",
    ]
    for s in samples:
        print("IN :", s)
        print("OUT:", add_punctuation(s))
        print()
