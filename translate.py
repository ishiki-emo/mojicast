"""確定字幕のローカル翻訳（CTranslate2版）。CPUで動作・完全オフライン。

2系統を持つ（どちらも tools/convert_models.py で変換・torch/transformers 非依存）:
  - 英訳:     FuguMT (staka/fugumt-ja-en, fp32)        … ja→en 専用・実績枠
  - 多言語訳: M2M-100 418M (facebook/m2m100_418M, int8) … 中国語・インドネシア語等

中国語はM2M-100の簡体字訳を基準にし、台湾・香港向けはOpenCCで地域表記へ
後処理する。広東語はM2M-100非対応であり、繁体字変換では代用しない。

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
    # --- 配信スラング（2026-08-21 追加）。素の FuguMT では
    # 「メン限」→"talk to men"・「推し」→"stigma"・「バズった」→"messy scum" のように
    # 壊れるため、名詞形で事前置換する。活用する語（尊い/凸待ち/初見殺し）は
    # 置換しても英文が不自然になるだけなので入れない ---
    (re.compile(r"メン限"), "members-only"),
    (re.compile(r"概要欄"), "description"),
    (re.compile(r"待機所"), "waiting room"),
    (re.compile(r"同時視聴"), "watch party"),
    (re.compile(r"神回"), "legendary stream"),
    (re.compile(r"リスナーさん|リスナー"), "viewers"),
    (re.compile(r"バズ(ってる|って|った|る|り)"), "viral"),
    (re.compile(r"推し(?![てたまさ])"), "favorite"),   # 「推して/推した」は動詞なので除く
    # 笑いの「草」だけを拾う（前が漢字なら「雑草」等、後ろが仮名なら「草むら」等で不発）
    (re.compile(r"(?<![一-龥])草(?=[。、！!？?]|$)"), "lol"),
]


def _apply_stream_terms(text: str) -> str:
    for pat, en in _STREAM_TERMS:
        text = pat.sub(en, text)
    return text


_SENT_HEAD = re.compile(r"(^|[!?]\s+)([a-z])")
_LONE_I = re.compile(r"\bi\b")
_TIGHT = re.compile(r"([!?])([A-Za-z])")


def _fix_case(text: str) -> str:
    """英訳の文頭が小文字になることがあるので直す（一人称 i も大文字へ）。

    FuguMT は入力の末尾に句点が無い・話し言葉が続くといった条件で
    `i forgot to say that` のように全体を小文字で出すことがある。
    字幕として目立つため、文頭と終止符の直後、および単独の i を大文字にする。
    """
    if not text:
        return text
    # 「Thank you for Super Chat!I'll use it.」のように終止符の直後が詰まることがある。
    # ピリオドは略語（U.S.A.）を壊すので触らず、! ? のみ空けを入れる
    text = _TIGHT.sub(lambda m: m.group(1) + " " + m.group(2), text)
    text = _SENT_HEAD.sub(lambda m: m.group(1) + m.group(2).upper(), text)
    return _LONE_I.sub("I", text)

_REPO_ID = "ishiki-emo/mojicast-fugumt-ja-en-ct2"   # 変換済みモデルの配布リポジトリ
_SUBDIR = "fugumt-ja-en-ct2"                         # ローカル models_conv/ 内のフォルダ名

_translator = None
_sp_src = None
_sp_tgt = None
_loaded_precision = None    # ロード済みモデルの精度（切替時の再ロード判定に使う）

# CTranslate2 は実行時に重みの精度を選べる（モデルの再変換・再配布は不要）。
# int8_float32 = 重みint8・計算float32。実測（2026-08-21・実配信400文）で
# 常駐 481MB→311MB（-170MB）・28ms→9ms（3倍速）、訳文はfp32と62%一致で
# 差分の大半は同等の言い換え。fp32が文の後半を落とす例がint8では残るなど、
# 総合的な品質は互角のため int8 を既定にする。
_COMPUTE_TYPE = {"int8": "int8_float32", "fp32": "float32"}


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


def load_translator(num_threads: int = 4, precision: str = "int8"):
    """モデルとトークナイザをロード（初回、および精度が変わったときのみ実行）"""
    global _translator, _sp_src, _sp_tgt, _loaded_precision
    if _translator is not None and _loaded_precision == precision:
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
    _translator = ctranslate2.Translator(
        d, device="cpu",
        compute_type=_COMPUTE_TYPE.get(precision, _COMPUTE_TYPE["int8"]),
        inter_threads=1, intra_threads=num_threads)
    _loaded_precision = precision
    # ロード直後の自己診断: 1文訳して空なら異常として失敗させる。
    # （エンジン側が英訳だけ無効化して字幕本体を守れる）
    if not translate("これはテストです。").strip():
        _translator = None
        _loaded_precision = None
        raise RuntimeError("翻訳モデルの自己診断に失敗（出力が空）")


def translate(text: str, max_new_tokens: int = 96,
              repetition_penalty: float = 1.2) -> str:
    """日本語テキストを英訳して返す（greedy＝最速）。空文字は空文字を返す。

    repetition_penalty と no_repeat_ngram_size は中国語・韓国語（#7）と同じ
    反復暴走の対策。「だめだだめだ、逃げて逃げて」のような繰り返しの口語で
    `no, no, no, ...` と延々続く出力になるのを断ち切る（実測 2026-08-21:
    実配信965文＋定型8文で暴走6件→0件・速度差なし・訳文の劣化なし）。
    """
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
                                      repetition_penalty=repetition_penalty,
                                      no_repeat_ngram_size=3,
                                      max_decoding_length=max_new_tokens)
    out = [t for t in res[0].hypotheses[0]
           if t not in ("</s>", "<pad>", "<unk>")]
    return _fix_case(_sp_tgt.decode(out).strip())


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

# インドネシア語向け。M2M-100は「配信」を配送（pengiriman）と誤訳しやすいため、
# インドネシアの配信文化でも一般的な借用語を先に入れて意味を固定する。
# モデル通過後の実測で崩れにくかった語だけを登録する。
_STREAM_TERMS_ID = [
    (re.compile(r"スーパーチャット|スパチャ"), "Super Chat"),
    (re.compile(r"配信者"), "streamer"),
    (re.compile(r"生配信|配信"), "live stream"),
    (re.compile(r"切り抜き"), "clip"),
    (re.compile(r"歌枠"), "Karaoke Stream"),
]

_m2m = None
_sp_m2m = None
_zh_converters = {}

_ZH_VARIANT_CONFIGS = {
    "zh_tw": "s2twp.json",  # 台湾正体字＋台湾で一般的な語彙
    "zh_hk": "s2hk.json",   # 香港繁体字
}


def convert_zh_variant(text: str, target: str) -> str:
    """簡体字中国語を台湾／香港の地域表記へ変換する。

    OpenCCは文字・地域語彙の変換であり、普通話→広東語の翻訳には使わない。
    target: "zh"（無変換）| "zh_tw" | "zh_hk"
    """
    config = _ZH_VARIANT_CONFIGS.get(target)
    if not config or not text:
        return text
    converter = _zh_converters.get(target)
    if converter is None:
        import opencc
        converter = opencc.OpenCC(config)
        _zh_converters[target] = converter
    return converter.convert(text)


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


def _decode_m2m(hypothesis) -> str:
    """M2M の出力トークン列を訳文へ。訳す価値のない出力は空文字にする。

    <unk> は「ここは訳せない」というモデル自身の申告。従来はこれを黙って取り除き
    残りを繋げて出していたため、意味の核だけが抜けた文が字幕に出ていた
    （「花粉症。もう私は」→「이 아프다. 이제는」＝「が痛い。今は」）。
    訳を捨てて空を返せば engine.py が原文へフォールバックするので字幕は消えない。
    言ってないことを足すくらいなら原文のまま出す。

    実測 2026-08-22（実配信ログ1200行・bench/bench_unk_rate.py）: 該当行は
    ko 11.4% / zh 1.8%。同条件で FuguMT(ja→en) は0%のため、あちらは従来どおり。
    """
    if "<unk>" in hypothesis:
        return ""
    out = [t for t in hypothesis
           if not (t.startswith("__") and t.endswith("__"))
           and t not in ("</s>", "<pad>")]
    translated = _sp_m2m.decode(out).strip()
    # トークン単体ではなくサブワードに埋もれて出ることもあり、その場合は上の
    # 完全一致では拾えない（「정말 기<unk>니다」がそのまま字幕に出ていた）
    if "<unk>" in translated:
        return ""
    # 反復抑止の副産物で、訳せない入力（「うどん」等の単語単発）が「。」や
    # 記号だけの断片になることがある。文字を1つも含まない出力は空として扱い、
    # ゴミを字幕に出さない（空の扱いは従来どおり＝訳文行を出さない）
    return translated if re.search(r"\w", translated) else ""


def translate_m2m(text: str, src: str = "ja", tgt: str = "zh",
                  max_new_tokens: int = 96,
                  repetition_penalty: float | None = None) -> str:
    """M2M-100 で src → tgt に翻訳する。

    基本の言語コードはM2M-100準拠（ja/zh/en/ko/id等）。アプリ独自の
    zh_tw/zh_hk はモデル上ではzhへ翻訳し、出力をOpenCCで地域表記へ変換する。
    広東語yueはM2M-100非対応のため翻訳先には使わない（音声認識のみ）。

    - ja→zh のときだけ配信用語の事前置換（_STREAM_TERMS_ZH）を適用
    - repetition_penalty 省略時は言語別の既定を使う: greedy だと相槌・
      繰り返し口語で反復暴走するため、韓国語（実測 2026-07-22）に続き
      中国語も 1.2（#7 実測 2026-08-18: 実配信3298文中90件の暴走が、
      1.2＋no_repeat_ngram=3 で0件。対照12文の劣化なし・速度差なし）
    """
    if not text or not text.strip():
        return ""
    model_tgt = "zh" if tgt in _ZH_VARIANT_CONFIGS else tgt
    if repetition_penalty is None:
        repetition_penalty = 1.2 if model_tgt in ("ko", "zh") else 1.0
    if _m2m is None:
        load_translator_zh()
    if src == "ja" and model_tgt == "zh":
        for pat, zh in _STREAM_TERMS_ZH:
            text = pat.sub(zh, text)
    elif src == "ja" and model_tgt == "id":
        for pat, indonesian in _STREAM_TERMS_ID:
            text = pat.sub(indonesian, text)
    tokens = _sp_m2m.encode(text, out_type=str)
    if len(tokens) > 510:
        tokens = tokens[:510]
    source = [f"__{src}__"] + tokens + ["</s>"]
    res = _m2m.translate_batch([source], target_prefix=[[f"__{model_tgt}__"]],
                               beam_size=1,
                               repetition_penalty=repetition_penalty,
                               # フレーズ単位の反復暴走（「是的,是的,…」×30等）は
                               # repetition_penalty だけでは残るため、3-gram の
                               # 再出現を禁止して確実に断ち切る（#7）
                               no_repeat_ngram_size=3,
                               max_decoding_length=max_new_tokens)
    translated = _decode_m2m(res[0].hypotheses[0])
    if not translated:
        return ""
    if model_tgt == "en":
        translated = _fix_case(translated)
    return convert_zh_variant(translated, tgt)


def translate_zh(text: str, max_new_tokens: int = 96) -> str:
    """日本語テキストを中国語（簡体字）へ翻訳して返す（translate_m2m の既定方向）"""
    return translate_m2m(text, "ja", "zh", max_new_tokens)


def loaded_precision():
    """常駐中の英訳モデルの精度（未ロードなら None）"""
    return _loaded_precision


def unload(which: str):
    """使わなくなった翻訳バックエンドを解放してメモリを返す（次回使用時に再ロード）。

    翻訳経路の切替（FuguMT⇔M2M）で旧バックエンドが常駐し続けるのを防ぐ。
    which: "fugumt" | "m2m"
    """
    global _translator, _sp_src, _sp_tgt, _m2m, _sp_m2m, _loaded_precision
    if which == "fugumt":
        _translator = _sp_src = _sp_tgt = None
        _loaded_precision = None
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
