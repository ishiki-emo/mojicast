"""
マイク入力でリアルタイム文字起こし
マイクから連続録音し、一定秒数たまるごとに認識して結果を表示し続ける
停止: Ctrl+C
"""
import sys
import time
import queue
import warnings

import numpy as np
import sounddevice as sd
from reazonspeech.k2.asr import load_model, transcribe, audio_from_numpy

# Windowsコンソールでの文字化け対策
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# ReazonSpeech k2 は 16kHz モノラルを想定
SAMPLE_RATE = 16000


def mic_transcribe(chunk_seconds: float = 5.0, device: str = "cpu",
                   input_device: int | None = None):
    """
    マイク入力を連続で文字起こしする

    Args:
        chunk_seconds: 何秒たまるごとに認識するか
        device: モデルの実行先 "cpu" or "cuda"
        input_device: 入力デバイス番号（Noneで既定のマイク）
    """
    print("モデルをロード中...", flush=True)
    t0 = time.time()
    model = load_model(device=device)
    print(f"ロード完了 ({time.time() - t0:.1f}秒)\n", flush=True)

    audio_q: "queue.Queue[np.ndarray]" = queue.Queue()

    def callback(indata, frames, time_info, status):
        if status:
            print(f"[警告] {status}", file=sys.stderr, flush=True)
        # indata は float32 (frames, 1)。コピーしてキューへ
        audio_q.put(indata[:, 0].copy())

    chunk_frames = int(chunk_seconds * SAMPLE_RATE)
    buffer = np.empty(0, dtype=np.float32)
    current_time = 0.0

    print(f"録音開始（{chunk_seconds}秒ごとに認識）。停止は Ctrl+C", flush=True)
    print("-" * 60, flush=True)

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        device=input_device, callback=callback):
        try:
            while True:
                # マイクからのブロックを取り出してバッファに溜める
                block = audio_q.get()
                buffer = np.concatenate([buffer, block])

                # 1チャンク分たまったら認識
                while len(buffer) >= chunk_frames:
                    chunk = buffer[:chunk_frames]
                    buffer = buffer[chunk_frames:]

                    end_time = current_time + chunk_seconds

                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        result = transcribe(
                            model, audio_from_numpy(chunk, SAMPLE_RATE)
                        )

                    text = result.text.strip()
                    if text:
                        print(f"[{current_time:6.1f}s - {end_time:6.1f}s] {text}",
                              flush=True)

                    current_time = end_time
        except KeyboardInterrupt:
            print("\n" + "-" * 60, flush=True)
            print("停止しました", flush=True)


if __name__ == "__main__":
    # 引数: [チャンク秒数] [入力デバイス番号]
    # デバイス一覧を見たいときは:  python transcribe_mic.py --list
    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        print(sd.query_devices())
        sys.exit(0)

    chunk = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
    dev = int(sys.argv[2]) if len(sys.argv) > 2 else None
    mic_transcribe(chunk_seconds=chunk, input_device=dev)
