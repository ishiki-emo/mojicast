"""確定字幕のローカル翻訳（CTranslate2版）。CPUで動作・完全オフライン。

2系統を持つ（どちらも tools/convert_models.py で変換・torch/transformers 非依存）:
  - 英訳:     FuguMT (staka/fugumt-ja-en, fp32)        … ja→en 専用・実績枠
  - 中国語訳: M2M-100 418M (facebook/m2m100_418M, int8) … 多言語枠の第一弾 ja→zh

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

    # SentencePiece は Windows で非ASCIIパス上のファイルを開けない（narrow string
    # でopenするため）。凍結版は exe 隣にモデルを置くので、日本語名フォルダに
    # インストールされると model_file= 渡しでは必ず失敗する。バイト列で渡して回避。
    def _sp_load(path):
        with open(path, "rb") as f:
            return spm.SentencePieceProcessor(model_proto=f.read())

    _sp_src = _sp_load(os.path.join(d, "source.spm"))
    _sp_tgt = _sp_load(os.path.join(d, "target.spm"))
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


# ---------------- 中国語訳（M2M-100 418M / int8） ----------------
# M2M-100 は100言語の相互翻訳モデル。言語トークン（__ja__ 等）で方向を指定する。
# 将来 zh→ja / zh→en 等へ広げるときも同じモデル・同じ仕組みで対応できる。

_REPO_ID_M2M = "ishiki-emo/mojicast-m2m100-ct2"   # 変換済みモデルの配布リポジトリ
_SUBDIR_M2M = "m2m100-418m-ct2"                    # ローカル models_conv/ 内のフォルダ名

# 配信ドメインの組み込み用語（中国語版）。英訳側の _STREAM_TERMS と同思想で、
# M2M-100が誤訳しがちな配信用語を翻訳前に中国語へ置換する（例: 配信→传递 を防ぐ）。
# 注意: 置換した中国語がモデルに再翻訳されて壊れる語がある（歌回→歌曲回归、
# 点赞→赞赞 を実測で確認）。実測で置換後も生き残った語だけ登録する。
_STREAM_TERMS_ZH = [
    (re.compile(r"配信者"), "主播"),
    (re.compile(r"生配信|配信"), "直播"),
    (re.compile(r"チャンネル登録(?!者)"), "订阅"),
    (re.compile(r"コメ欄"), "评论区"),
]

_m2m = None
_sp_m2m = None


def _resolve_dir_m2m(download=True):
    """CT2モデルフォルダの解決: models_conv/ → HFキャッシュ → DL（FuguMTと同型）"""
    local = os.path.join(BASE, "models_conv", _SUBDIR_M2M)
    if os.path.exists(os.path.join(local, "model.bin")):
        return local
    try:
        d = hf.snapshot_download(_REPO_ID_M2M, local_files_only=True)
    except Exception:
        if not download:
            raise
        d = hf.snapshot_download(_REPO_ID_M2M)
    if not os.path.exists(os.path.join(d, "model.bin")):
        raise FileNotFoundError(f"CT2モデルが不完全です: {d}")
    return d


def cached_zh() -> bool:
    """中国語訳モデルがローカルにあるか（DLはしない。初回DLサイズ見積り用）"""
    try:
        _resolve_dir_m2m(download=False)
        return True
    except Exception:
        return False


def load_translator_zh(num_threads: int = 4):
    """M2M-100 のモデルとトークナイザをロード（初回のみ実行）"""
    global _m2m, _sp_m2m
    if _m2m is not None:
        return
    import ctranslate2
    import sentencepiece as spm
    d = _resolve_dir_m2m()
    # 非ASCIIパス対策で model_proto 渡し（FuguMT側と同じ理由）
    with open(os.path.join(d, "sentencepiece.model"), "rb") as f:
        _sp_m2m = spm.SentencePieceProcessor(model_proto=f.read())
    _m2m = ctranslate2.Translator(d, device="cpu",
                                  compute_type="int8",
                                  inter_threads=1,
                                  intra_threads=num_threads)
    if not translate_zh("これはテストです。").strip():
        _m2m = None
        raise RuntimeError("中国語訳モデルの自己診断に失敗（出力が空）")


def translate_m2m(text: str, src: str = "ja", tgt: str = "zh",
                  max_new_tokens: int = 96,
                  repetition_penalty: float = 1.0) -> str:
    """M2M-100 で src → tgt に翻訳する（言語コードは m2m100 準拠: ja/zh/en/ko/yue 等）。

    - ja→zh のときだけ配信用語の事前置換（_STREAM_TERMS_ZH）を適用
    - 韓国語など一部ターゲットは greedy だと反復暴走するため、その言語を
      追加するときは repetition_penalty=1.2 前後を指定すること（実測 2026-07-22）
    """
    if not text or not text.strip():
        return ""
    if _m2m is None:
        load_translator_zh()
    if src == "ja" and tgt == "zh":
        for pat, zh in _STREAM_TERMS_ZH:
            text = pat.sub(zh, text)
    tokens = _sp_m2m.encode(text, out_type=str)
    if len(tokens) > 510:
        tokens = tokens[:510]
    source = [f"__{src}__"] + tokens + ["</s>"]
    res = _m2m.translate_batch([source], target_prefix=[[f"__{tgt}__"]],
                               beam_size=1,
                               repetition_penalty=repetition_penalty,
                               max_decoding_length=max_new_tokens)
    out = [t for t in res[0].hypotheses[0]
           if not (t.startswith("__") and t.endswith("__"))
           and t not in ("</s>", "<pad>", "<unk>")]
    return _sp_m2m.decode(out).strip()


def translate_zh(text: str, max_new_tokens: int = 96) -> str:
    """日本語テキストを中国語（簡体字）へ翻訳して返す（translate_m2m の既定方向）"""
    return translate_m2m(text, "ja", "zh", max_new_tokens)


def unload(which: str):
    """使わなくなった翻訳バックエンドを解放してメモリを返す（次回使用時に再ロード）。

    翻訳経路の切替（FuguMT⇔M2M）で旧バックエンドが常駐し続けるのを防ぐ。
    which: "fugumt" | "m2m"
    """
    global _translator, _sp_src, _sp_tgt, _m2m, _sp_m2m
    if which == "fugumt":
        _translator = _sp_src = _sp_tgt = None
    elif which == "m2m":
        _m2m = _sp_m2m = None


if __name__ == "__main__":
    import sys
    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    for s in ["皆様こんばんは、癒色えもでございます。",
              "今日は新しい機能のテストをしていきます。",
              "配信を見てくれてありがとう、めっちゃ嬉しいです。"]:
        print("JA:", s)
        print("EN:", translate(s))
        try:
            print("ZH:", translate_zh(s))
        except Exception as e:
            print("ZH: (モデル未変換:", e, ")")
        print()
