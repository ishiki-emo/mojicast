"""
認識モデルのローダとレジストリ

- k2-ja: ReazonSpeech k2（日本語特化・既定）。sherpa_onnx.OfflineRecognizer.from_transducer
  を直接呼び、modified_beam_search + hotwords_file（単語登録＝コンテキストバイアス）を
  有効にする（reazonspeech の標準 load_model は greedy 固定でホットワード不可のため）。
- sensevoice: SenseVoice small（中・英・日・韓・広東語）。多言語モード用。
  句読点と数字正規化（ITN）を内蔵する代わりに、ホットワードの認識誘導は使えない。

MODELS レジストリの capabilities を engine が見て、後処理を自動で切り替える。
"""
import re

import sherpa_onnx
import huggingface_hub as hf

_REPO_ID = "reazon-research/reazonspeech-k2-v2"
_EPOCHS = 99  # v2(ja) のモデルファイル名に埋まっているepoch番号


def _resolve_files(precision: str = "fp32"):
    """モデルファイル群のローカルパスを解決（キャッシュ優先、無ければDL）"""
    try:
        basedir = hf.snapshot_download(_REPO_ID, local_files_only=True)
    except hf.utils.LocalEntryNotFoundError:
        basedir = hf.snapshot_download(_REPO_ID)
    import os
    # int8 系は encoder/joiner を量子化版に差し替え（CPUで高速）
    enc = "int8." if precision in ("int8", "int8-fp32") else ""
    join = "int8." if precision in ("int8", "int8-fp32") else ""
    dec = "int8." if precision == "int8" else ""  # int8-fp32 は decoder のみ fp32
    return {
        "tokens": os.path.join(basedir, "tokens.txt"),
        "encoder": os.path.join(basedir, f"encoder-epoch-{_EPOCHS}-avg-1.{enc}onnx"),
        "decoder": os.path.join(basedir, f"decoder-epoch-{_EPOCHS}-avg-1.{dec}onnx"),
        "joiner": os.path.join(basedir, f"joiner-epoch-{_EPOCHS}-avg-1.{join}onnx"),
    }


def load_model(device: str = "cpu", hotwords_file: str = "",
               hotwords_score: float = 2.0, num_threads: int = 4,
               precision: str = "fp32"):
    """
    ホットワード対応の k2 認識器をロードする

    Args:
        device: "cpu" or "cuda"
        hotwords_file: 登録単語ファイル（空文字なら通常のgreedy_search）
        hotwords_score: 登録単語を出やすくする強さ。大きいほど強制的。
                        （目安 1.5〜4.0。強すぎると誤爆が増える）
        num_threads: CPUスレッド数（1→4で約1.7倍速。16コアなら4前後が最適）
        precision: "fp32"（既定・最高精度） / "int8-fp32"（高速・精度ほぼ同） /
                   "int8"（最速・精度わずかに低下）

    Returns:
        sherpa_onnx.OfflineRecognizer
    """
    files = _resolve_files(precision)

    # ホットワードを使うときだけ modified_beam_search（greedyは非対応）
    method = "modified_beam_search" if hotwords_file else "greedy_search"

    return sherpa_onnx.OfflineRecognizer.from_transducer(
        tokens=files["tokens"],
        encoder=files["encoder"],
        decoder=files["decoder"],
        joiner=files["joiner"],
        num_threads=num_threads,
        sample_rate=16000,
        feature_dim=80,
        decoding_method=method,
        hotwords_file=hotwords_file,
        hotwords_score=hotwords_score,
        modeling_unit="cjkchar",  # 日本語をそのまま単語として扱える
        provider=device,
    )


# ---------------- モデルレジストリ（多言語/軽量モードの受け皿） ----------------
# capabilities（engine が見て後処理を自動切替する）:
#   hotwords: 認識誘導（コンテキストバイアス）が使えるか（transducer系のみ）
#   punct:    句読点・数字正規化(ITN)を内蔵するか（True なら後段のBERT/numnormをスキップ）
#   spaces:   CJK文字間に余分な空白が入るか（True なら後段で除去）
#   multilang:言語指定（asr_lang）を受け付けるか

MODELS = {
    "k2-ja": {
        "name": "日本語特化（ReazonSpeech k2）",
        "caps": {"hotwords": True, "punct": False,
                 "spaces": False, "multilang": False},
    },
    "sensevoice": {
        "name": "多言語（SenseVoice: 中・英・日・韓・広東語）",
        "caps": {"hotwords": False, "punct": True,
                 "spaces": True, "multilang": True},
    },
}

_SV_REPO = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
# fp32版(894MB)は落とさない。int8＋語彙＋ライセンス類のみ
_SV_FILES = ["model.int8.onnx", "tokens.txt", "LICENSE", "README.md"]


def _resolve_sensevoice(download=True):
    """SenseVoice モデルフォルダの解決（HFキャッシュ → DL）"""
    import os
    try:
        d = hf.snapshot_download(_SV_REPO, allow_patterns=_SV_FILES,
                                 local_files_only=True)
    except Exception:
        if not download:
            raise
        d = hf.snapshot_download(_SV_REPO, allow_patterns=_SV_FILES)
    if not os.path.exists(os.path.join(d, "model.int8.onnx")):
        raise FileNotFoundError(f"SenseVoiceモデルが不完全です: {d}")
    return d


def cached(model_id="k2-ja") -> bool:
    """モデルがローカルにあるか（DLはしない。初回DLサイズ見積り用）"""
    try:
        if model_id == "sensevoice":
            _resolve_sensevoice(download=False)
        else:
            hf.snapshot_download(_REPO_ID, local_files_only=True)
        return True
    except Exception:
        return False


def load_sensevoice(language="auto", num_threads: int = 4):
    """SenseVoice をロードする。language: auto/ja/zh/en/ko/yue（auto=自動判定）"""
    import os
    d = _resolve_sensevoice()
    lang = "" if language in (None, "", "auto") else language
    return sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=os.path.join(d, "model.int8.onnx"),
        tokens=os.path.join(d, "tokens.txt"),
        num_threads=num_threads,
        use_itn=True,          # 句読点・数字正規化を内蔵側で実施
        language=lang,
    )


def load_by_config(model_id="k2-ja", hotwords_file="", hotwords_score=2.0,
                   num_threads=4, precision="int8-fp32", language="auto"):
    """レジストリ経由でロードし (recognizer, capabilities) を返す。

    未知IDは k2-ja に落とす（古い設定ファイルや将来IDへの防御）。
    """
    if model_id not in MODELS:
        model_id = "k2-ja"
    caps = MODELS[model_id]["caps"]
    if model_id == "sensevoice":
        return load_sensevoice(language, num_threads), caps
    return load_model(device="cpu", hotwords_file=hotwords_file,
                      hotwords_score=hotwords_score, num_threads=num_threads,
                      precision=precision), caps


# 全角記号・かな・CJK漢字・互換漢字・全角英数（SenseVoiceが空白を挟みがちな範囲）
_CJK = ('[\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff'
        '\uf900-\ufaff\uff00-\uffef]')
_CJK_SPACE = re.compile(f'(?<={_CJK})\\s+(?={_CJK})')


def strip_cjk_spaces(text: str) -> str:
    """CJK文字同士の間の空白だけ除去する（SenseVoiceのトークン間スペース対策）。
    英単語間の空白は保持されるので、英語・混在文にも安全。"""
    return _CJK_SPACE.sub("", text)
