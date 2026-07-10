"""
マイク入力 + VAD でリアルタイム文字起こし（字幕風に流し表示）
固定秒数ではなく、無音区間（既定200ms）で発話を区切ってから認識するので
単語の途中で切れにくく、精度が上がる。
認識テキストは同じ行にどんどん追記し、約30文字＋文の区切りで改行する。
停止: Ctrl+C
"""
import os
import sys
import time
import queue
import warnings

import numpy as np
import sounddevice as sd
import sherpa_onnx
from reazonspeech.k2.asr import transcribe, audio_from_numpy
from asr_model import load_model  # ホットワード(単語登録)対応のローダ
from caption import LiveCaption

# Windowsコンソールでの文字化け対策
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# ReazonSpeech k2 / Silero VAD ともに 16kHz モノラルを想定
SAMPLE_RATE = 16000
WINDOW_SIZE = 512  # Silero VAD の1フレーム長（16kHzで約32ms）

# 同梱のVADモデル（このスクリプトと同じフォルダの silero_vad.onnx）
VAD_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "silero_vad.onnx")


def build_vad(min_silence_ms: int = 350):
    """Silero VAD の検出器を構築する"""
    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = VAD_MODEL_PATH
    config.silero_vad.threshold = 0.5           # 発話とみなす確率のしきい値
    config.silero_vad.min_silence_duration = min_silence_ms / 1000.0  # 無音区切り
    config.silero_vad.min_speech_duration = 0.25  # これより短い音は無視（ノイズ除け）
    config.silero_vad.window_size = WINDOW_SIZE
    config.sample_rate = SAMPLE_RATE
    # バッファ長は最大発話長に余裕を持たせる（秒）
    return sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=30)


def mic_transcribe_vad(min_silence_ms: int = 200, device: str = "cpu",
                       input_device: int | None = None,
                       punctuate: bool = True, width: int = 30,
                       hotwords_file: str = "", hotwords_score: float = 2.0,
                       precision: str = "fp32"):
    """
    マイク入力を VAD で区切りながら連続文字起こしする（字幕風に流し表示）

    Args:
        min_silence_ms: この長さ以上の無音で発話を区切る（ミリ秒）
        device: モデルの実行先 "cpu" or "cuda"
        input_device: 入力デバイス番号（Noneで既定のマイク）
        punctuate: True で認識結果に句読点(、。)を復元する
        width: 1行の目安文字数（この付近＋文の区切りで改行）
        hotwords_file: 登録単語ファイル（空なら通常認識）
        hotwords_score: 登録単語を出やすくする強さ（目安1.5〜4.0）
        precision: "fp32"（既定） / "int8-fp32" / "int8"（高速）
    """
    print("モデルをロード中...", flush=True)
    t0 = time.time()

    # 単語登録: 語彙(表記,読み)から「読み」のホットワードと 読み→表記 置換を用意
    replacer = None
    hw_path = ""
    if hotwords_file:
        from vocab import parse_vocab, write_hotwords, build_replacer
        entries = parse_vocab(hotwords_file)
        hw_path = write_hotwords(entries)      # 読みベースのsherpa用ファイル
        replacer = build_replacer(entries)     # 読み→表記

    model = load_model(device=device, hotwords_file=hw_path,
                       hotwords_score=hotwords_score, precision=precision)
    if hotwords_file:
        print(f"単語登録: {hotwords_file}（{len(entries)}語・強さ {hotwords_score}）",
              flush=True)
    vad = build_vad(min_silence_ms)
    add_punctuation = None
    if punctuate:
        # 句読点復元モデル（日本語BERT）を遅延インポートしてロード
        from punct import add_punctuation, load_punctuator
        load_punctuator()
    print(f"ロード完了 ({time.time() - t0:.1f}秒)\n", flush=True)

    audio_q: "queue.Queue[np.ndarray]" = queue.Queue()

    def callback(indata, frames, time_info, status):
        if status:
            print(f"[警告] {status}", file=sys.stderr, flush=True)
        audio_q.put(indata[:, 0].copy())

    buffer = np.empty(0, dtype=np.float32)
    seg_index = 0
    caption = LiveCaption(width=width)

    print(f"録音開始（無音 {min_silence_ms}ms で区切り・{width}文字前後で改行）。"
          f"停止は Ctrl+C", flush=True)
    print("-" * 60, flush=True)

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        blocksize=WINDOW_SIZE, device=input_device,
                        callback=callback):
        try:
            while True:
                block = audio_q.get()
                buffer = np.concatenate([buffer, block])

                # VAD は WINDOW_SIZE 単位で処理する
                while len(buffer) >= WINDOW_SIZE:
                    vad.accept_waveform(buffer[:WINDOW_SIZE])
                    buffer = buffer[WINDOW_SIZE:]

                # 区切りが確定した発話セグメントを順に認識
                while not vad.empty():
                    segment = vad.front
                    samples = np.array(segment.samples, dtype=np.float32)
                    vad.pop()

                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        result = transcribe(
                            model, audio_from_numpy(samples, SAMPLE_RATE)
                        )

                    text = result.text.strip()
                    if text:
                        if replacer is not None:
                            text = replacer(text)  # 読み→表記（例: いしきえも→癒色えも）
                        if add_punctuation is not None:
                            text = add_punctuation(text)
                        seg_index += 1
                        # 字幕風に流し表示（約width文字＋文の区切りで改行）
                        caption.feed(text)
        except KeyboardInterrupt:
            caption.close()  # 残りの未確定行を出す
            print("-" * 60, flush=True)
            print(f"停止しました（{seg_index}件）", flush=True)


if __name__ == "__main__":
    # 引数: [無音区切りms] [入力デバイス番号]
    # オプション: --list（デバイス一覧） / --no-punct（句読点なし）
    #            --width=NN（1行の目安文字数、既定30）
    #            --hotwords[=path]（単語登録。省略時は ./hotwords.txt）
    #            --hotwords-score=NN（登録単語の強さ、既定2.0）
    #            --fast（int8で高速化）
    if "--list" in sys.argv:
        print(sd.query_devices())
        sys.exit(0)

    punctuate = "--no-punct" not in sys.argv
    line_width = 30
    hotwords_file = ""
    hotwords_score = 2.0
    precision = "int8-fp32" if "--fast" in sys.argv else "fp32"
    for a in sys.argv[1:]:
        if a.startswith("--width="):
            line_width = int(a.split("=", 1)[1])
        elif a == "--hotwords":
            hotwords_file = "hotwords.txt"
        elif a.startswith("--hotwords="):
            hotwords_file = a.split("=", 1)[1]
        elif a.startswith("--hotwords-score="):
            hotwords_score = float(a.split("=", 1)[1])
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]
    silence_ms = int(pos[0]) if len(pos) > 0 else 200
    dev = int(pos[1]) if len(pos) > 1 else None
    mic_transcribe_vad(min_silence_ms=silence_ms, input_device=dev,
                       punctuate=punctuate, width=line_width,
                       hotwords_file=hotwords_file, hotwords_score=hotwords_score,
                       precision=precision)
