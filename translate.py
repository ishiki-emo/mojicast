"""日本語→英語のローカル翻訳（FuguMT / CTranslate2版）

確定字幕を英訳して併記するためのモジュール。CPUで動作・完全オフライン。
モデル: staka/fugumt-ja-en を tools/convert_models.py で CTranslate2 (fp32) 化したもの。
torch / transformers 非依存。トークナイザは SentencePiece を直接使う。

k2 の確定テキスト（句読点適用済み）をそのまま渡す想定。
翻訳は認識ループとは別スレッドから呼ぶこと（engine.CaptionEngine が担当）。
"""
import os
import re

import huggingface_hub as hf

from apppaths import BASE

# 配信ドメインの組み込み用語。FuguMTが誤訳しがちな配信用語を、翻訳前に
# 日本語側で英語へ置換する（例: 配信→delivery/distribution を防ぐ）。
# ユーザーの英訳辞書（engine側で先に置換される）が常に優先される。
# 順序は上から適用（長い語・限定的な語を先に）。実測で改善した語のみ登録し、
# 悪化した語（チャンネル登録者・バズる 等）は素の翻訳に任せる。
_STREAM_TERMS = [
    (re.compile(r"スーパーチャット|スパチャ"), "Super Chat"),
    (re.compile(r"同時接続|同接"), "concurrent viewers"),
    (re.compile(r"チャンネル登録(?!者)"), "subscribe"),   # 登録者は素の訳が良い
    (re.compile(r"生配信"), "live stream"),
    (re.compile(r"配信者"), "streamer"),
    (re.compile(r"配信"), "stream"),
    (re.compile(r"切り抜き"), "clip"),
    (re.compile(r"コメ欄"), "comment section"),
    (re.compile(r"高評価"), "like"),
    (re.compile(r"歌枠"), "singing stream"),
]


def _apply_stream_terms(text: str) -> str:
    for pat, en in _STREAM_TERMS:
        text = pat.sub(en, text)
    return text

_REPO_ID = "ishiki-emo/mojicast-fugumt-ja-en-ct2"   # 変換済みモデルの配布リポジトリ
_SUBDIR = "fugumt-ja-en-ct2"                         # ローカル models_conv/ 内のフォルダ名

_translator = None
_sp_src = None
_sp_tgt = None


def _resolve_dir(download=True):
    """CT2モデルフォルダの解決: models_conv/（開発・手動配置）→ HFキャッシュ → DL"""
    local = os.path.join(BASE, "models_conv", _SUBDIR)
    if os.path.exists(os.path.join(local, "model.bin")):
        return local
    try:
        d = hf.snapshot_download(_REPO_ID, local_files_only=True)
    except Exception:
        if not download:
            raise
        d = hf.snapshot_download(_REPO_ID)
    if not os.path.exists(os.path.join(d, "model.bin")):
        raise FileNotFoundError(f"CT2モデルが不完全です: {d}")
    return d


def cached() -> bool:
    """モデルがローカルにあるか（DLはしない。初回DLサイズ見積り用）"""
    try:
        _resolve_dir(download=False)
        return True
    except Exception:
        return False


def load_translator(num_threads: int = 4):
    """モデルとトークナイザをロード（初回のみ実行）"""
    global _translator, _sp_src, _sp_tgt
    if _translator is not None:
        return
    import ctranslate2
    import sentencepiece as spm
    d = _resolve_dir()
    _sp_src = spm.SentencePieceProcessor(model_file=os.path.join(d, "source.spm"))
    _sp_tgt = spm.SentencePieceProcessor(model_file=os.path.join(d, "target.spm"))
    _translator = ctranslate2.Translator(d, device="cpu",
                                         compute_type="float32",
                                         inter_threads=1,
                                         intra_threads=num_threads)
    # ロード直後の自己診断: 1文訳して空なら異常として失敗させる。
    # （エンジン側が英訳だけ無効化して字幕本体を守れる）
    if not translate("これはテストです。").strip():
        _translator = None
        raise RuntimeError("翻訳モデルの自己診断に失敗（出力が空）")


def translate(text: str, max_new_tokens: int = 96) -> str:
    """日本語テキストを英訳して返す（greedy＝最速）。空文字は空文字を返す。"""
    if not text or not text.strip():
        return ""
    if _translator is None:
        load_translator()
    text = _apply_stream_terms(text)
    tokens = _sp_src.encode(text, out_type=str)
    if len(tokens) > 511:                     # 旧実装の truncation=512 相当
        tokens = tokens[:511]
    tokens.append("</s>")
    res = _translator.translate_batch([tokens], beam_size=1,
                                      max_decoding_length=max_new_tokens)
    out = [t for t in res[0].hypotheses[0]
           if t not in ("</s>", "<pad>", "<unk>")]
    return _sp_tgt.decode(out).strip()


if __name__ == "__main__":
    import sys
    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    for s in ["皆様こんばんは、癒色えもでございます。",
              "今日は新しい機能のテストをしていきます。",
              "配信を見てくれてありがとう、めっちゃ嬉しいです。"]:
        print("JA:", s)
        print("EN:", translate(s), "\n")
