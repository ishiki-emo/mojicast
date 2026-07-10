"""
マイク入力の疑似ストリーミング文字起こし（喋りながら出る版）

日本語の真のストリーミング（オンライン）モデルは公開されていないため、
オフラインモデルを「たまっていく音声に対して繰り返し認識」して途中経過を
薄字で上書き表示し、無音(VAD)または最大長で確定する simulated streaming 方式。

- 発話中: current_segment（進行中の音声）を一定間隔で認識し、薄字で上書き
- 確定時: 無音区切り or 最大長で、句読点・単語置換を適用して通常色で改行
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
from asr_model import load_model
from caption import LiveCaption

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

SAMPLE_RATE = 16000
WINDOW_SIZE = 512
VAD_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "silero_vad.onnx")


def build_vad(min_silence_ms: int):
    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = VAD_MODEL_PATH
    config.silero_vad.threshold = 0.5
    config.silero_vad.min_silence_duration = min_silence_ms / 1000.0
    config.silero_vad.min_speech_duration = 0.25
    config.silero_vad.window_size = WINDOW_SIZE
    config.sample_rate = SAMPLE_RATE
    return sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=30)


def _recognize(model, samples: np.ndarray) -> str:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return transcribe(model, audio_from_numpy(samples, SAMPLE_RATE)).text.strip()


def stream_transcribe(min_silence_ms: int = 300, device: str = "cpu",
                      input_device: int | None = None, punctuate: bool = True,
                      width: int = 30, hotwords_file: str = "",
                      hotwords_score: float = 2.0, precision: str = "int8-fp32",
                      partial_interval: float = 0.4, max_utt_sec: float = 12.0,
                      overlay_port: int | None = None):
    """
    疑似ストリーミングでマイク入力を文字起こしする

    Args:
        min_silence_ms: この無音で発話を確定（区切る）
        partial_interval: 途中経過を認識し直す間隔（秒）。短いほど更新が速いが重い
        max_utt_sec: 無音が来なくてもこの長さで強制確定（切れ目なく喋る人対策）
        （他は transcribe_mic_vad と同じ）
    """
    print("モデルをロード中...", flush=True)
    t0 = time.time()

    replacer = None
    hw_path = ""
    if hotwords_file:
        from vocab import parse_vocab, write_hotwords, build_replacer
        entries = parse_vocab(hotwords_file)
        hw_path = write_hotwords(entries)
        replacer = build_replacer(entries)

    model = load_model(device=device, hotwords_file=hw_path,
                       hotwords_score=hotwords_score, precision=precision)
    add_punctuation = None
    if punctuate:
        from punct import add_punctuation, load_punctuator
        load_punctuator()

    # OBS向けオーバーレイ（HTTPサーバ）を起動
    overlay = None
    if overlay_port:
        import overlay_server as overlay
        overlay.set_hotwords([s for s, _r, _sc in entries] if hotwords_file else [])
        overlay.start(overlay_port)
        print(f"オーバーレイ: http://localhost:{overlay_port}"
              "  （OBSのブラウザソースに指定）", flush=True)
    print(f"ロード完了 ({time.time() - t0:.1f}秒)\n", flush=True)

    vad = build_vad(min_silence_ms)
    caption = LiveCaption(width=width)
    audio_q: "queue.Queue[np.ndarray]" = queue.Queue()

    def callback(indata, frames, time_info, status):
        if status:
            print(f"[警告] {status}", file=sys.stderr, flush=True)
        audio_q.put(indata[:, 0].copy())

    buffer = np.empty(0, dtype=np.float32)
    last_partial_len = 0
    interval_samples = int(partial_interval * SAMPLE_RATE)
    max_samples = int(max_utt_sec * SAMPLE_RATE)

    print(f"録音開始（疑似ストリーミング / 無音 {min_silence_ms}ms で確定）。"
          f"停止は Ctrl+C", flush=True)
    print("-" * 60, flush=True)

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        blocksize=WINDOW_SIZE, device=input_device,
                        callback=callback):
        try:
            while True:
                block = audio_q.get()
                buffer = np.concatenate([buffer, block])
                while len(buffer) >= WINDOW_SIZE:
                    vad.accept_waveform(buffer[:WINDOW_SIZE])
                    buffer = buffer[WINDOW_SIZE:]

                # 1) 確定した発話を通常色で改行
                while not vad.empty():
                    samples = np.array(vad.front.samples, dtype=np.float32)
                    vad.pop()
                    text = _recognize(model, samples)
                    if text:
                        if replacer is not None:
                            text = replacer(text)      # 読み→表記
                        if add_punctuation is not None:
                            text = add_punctuation(text)
                        caption.feed(text)
                        if overlay is not None:
                            overlay.push_final(text)
                    last_partial_len = 0

                # 2) 発話中は途中経過を薄字で上書き
                if vad.is_speech_detected():
                    cur = np.array(vad.current_segment.samples, dtype=np.float32)
                    if len(cur) - last_partial_len >= interval_samples \
                            and len(cur) >= int(0.3 * SAMPLE_RATE):
                        last_partial_len = len(cur)
                        # 途中は生の認識のみ（句読点・置換は確定時に適用）
                        ptext = _recognize(model, cur)
                        caption.set_partial(ptext)
                        if overlay is not None:
                            overlay.push_partial(ptext)
                    # 無音が来ない長話は最大長で強制確定
                    if len(cur) >= max_samples:
                        vad.flush()
        except KeyboardInterrupt:
            caption.close()
            print("-" * 60, flush=True)
            print("停止しました", flush=True)


if __name__ == "__main__":
    # 引数: [無音確定ms] [入力デバイス番号]
    # オプション: --list / --no-punct / --width=NN / --hotwords[=path]
    #            --hotwords-score=NN / --fast(既定int8) / --fp32(最高精度)
    #            --interval=秒（途中更新間隔） / --max=秒（強制確定）
    #            --overlay（OBS用オーバーレイをlocalhost:8765で起動）/ --port=NN
    if "--list" in sys.argv:
        print(sd.query_devices())
        sys.exit(0)

    punctuate = "--no-punct" not in sys.argv
    precision = "fp32" if "--fp32" in sys.argv else "int8-fp32"
    line_width, hotwords_file, hotwords_score = 30, "", 2.0
    interval, max_utt = 0.4, 12.0
    overlay_port = None
    for a in sys.argv[1:]:
        if a.startswith("--width="):
            line_width = int(a.split("=", 1)[1])
        elif a == "--hotwords":
            hotwords_file = "hotwords.txt"
        elif a.startswith("--hotwords="):
            hotwords_file = a.split("=", 1)[1]
        elif a.startswith("--hotwords-score="):
            hotwords_score = float(a.split("=", 1)[1])
        elif a.startswith("--interval="):
            interval = float(a.split("=", 1)[1])
        elif a.startswith("--max="):
            max_utt = float(a.split("=", 1)[1])
        elif a == "--overlay":
            overlay_port = 8765
        elif a.startswith("--port="):
            overlay_port = int(a.split("=", 1)[1])
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]
    silence_ms = int(pos[0]) if len(pos) > 0 else 300
    dev = int(pos[1]) if len(pos) > 1 else None
    stream_transcribe(min_silence_ms=silence_ms, input_device=dev,
                      punctuate=punctuate, width=line_width,
                      hotwords_file=hotwords_file, hotwords_score=hotwords_score,
                      precision=precision, partial_interval=interval,
                      max_utt_sec=max_utt, overlay_port=overlay_port)
