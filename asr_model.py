"""
ReazonSpeech k2 モデルのローダ（ホットワード＝単語登録に対応した版）

reazonspeech の標準 load_model は greedy_search 固定でホットワードを使えないため、
sherpa_onnx.OfflineRecognizer.from_transducer を直接呼んで
modified_beam_search + hotwords_file を有効にする。

単語登録の仕組み（コンテキストバイアス）:
  認識時に、登録した単語のスコアを底上げして出やすくする。
  固有名詞・専門用語・人名など、モデルが知らない語の誤認識を減らせる。
"""
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
