"""日本語→英語のローカル翻訳（FuguMT / MarianMT）

確定字幕を英訳して併記するためのモジュール。CPUで動作・完全オフライン。
モデル: staka/fugumt-ja-en（約60Mパラメータの軽量MT。1行あたり数十ms）
初回呼び出し時にモデルをロード（遅延ロード）。

k2 の確定テキスト（句読点適用済み）をそのまま渡す想定。
翻訳は認識ループとは別スレッドから呼ぶこと（engine.CaptionEngine が担当）。
"""
import warnings

_MODEL_NAME = "staka/fugumt-ja-en"

_tokenizer = None
_model = None


def _local_first(loader, *args, **kwargs):
    """まずローカルキャッシュから読み、無ければDL（同梱版=DL無し / 軽量版=初回DL）"""
    try:
        return loader(*args, local_files_only=True, **kwargs)
    except Exception:
        return loader(*args, **kwargs)


def load_translator(num_threads: int = 4):
    """モデルとトークナイザをロード（初回のみ実行）"""
    global _tokenizer, _model
    if _model is not None:
        return
    import torch
    from transformers import MarianMTModel, MarianTokenizer
    torch.set_num_threads(num_threads)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _tokenizer = _local_first(MarianTokenizer.from_pretrained, _MODEL_NAME)
        _model = _local_first(MarianMTModel.from_pretrained, _MODEL_NAME).eval()


def translate(text: str, max_new_tokens: int = 96) -> str:
    """日本語テキストを英訳して返す（greedy＝最速）。空文字は空文字を返す。"""
    if not text or not text.strip():
        return ""
    if _model is None:
        load_translator()
    import torch
    with torch.no_grad(), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        inputs = _tokenizer(text, return_tensors="pt",
                            padding=True, truncation=True, max_length=512)
        out = _model.generate(**inputs, max_new_tokens=max_new_tokens,
                              num_beams=1)
    return _tokenizer.decode(out[0], skip_special_tokens=True).strip()


if __name__ == "__main__":
    import sys
    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    for s in ["皆様こんばんは、癒色えもでございます。",
              "今日は新しい機能のテストをしていきます。",
              "配信を見てくれてありがとう、めっちゃ嬉しいです。"]:
        print("JA:", s)
        print("EN:", translate(s), "\n")
