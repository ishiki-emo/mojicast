"""
リアルタイム風字幕生成
短い音声チャンクを連続処理して、認識でき次第すぐに結果を表示する
"""
import sys
import time
import wave
import warnings

import numpy as np
from reazonspeech.k2.asr import load_model, transcribe, audio_from_numpy

# Windowsコンソールでの文字化け対策
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


def stream_transcribe(audio_path: str, chunk_seconds: float = 5.0, device: str = "cpu"):
    """
    音声をチャンク分割して逐次認識し、結果をその場で表示する

    Args:
        audio_path: 音声ファイルのパス（WAV）
        chunk_seconds: チャンクの長さ（秒）
        device: "cpu" or "cuda"

    Returns:
        list: 字幕データのリスト [{"start", "end", "text"}, ...]
    """
    print("モデルをロード中...", flush=True)
    t0 = time.time()
    model = load_model(device=device)
    print(f"ロード完了 ({time.time() - t0:.1f}秒)\n", flush=True)

    subtitles = []

    with wave.open(audio_path, "rb") as wf:
        sample_rate = wf.getframerate()
        n_channels = wf.getnchannels()
        total_seconds = wf.getnframes() / sample_rate

        chunk_frames = int(chunk_seconds * sample_rate)
        current_time = 0.0

        print(f"認識開始: {audio_path} ({total_seconds:.1f}秒)", flush=True)
        print("-" * 60, flush=True)

        while True:
            frames = wf.readframes(chunk_frames)
            if not frames:
                break

            # 16bit PCM → float32 に正規化
            audio_data = np.frombuffer(frames, dtype=np.int16)
            if n_channels == 2:
                audio_data = audio_data.reshape(-1, 2).mean(axis=1)
            audio_float = audio_data.astype(np.float32) / 32768.0

            # 実際に読めたフレーム数からチャンクの終了時刻を計算（末尾対応）
            chunk_duration = len(audio_float) / sample_rate
            end_time = current_time + chunk_duration

            # 認識実行（リサンプル・モノラル化はライブラリ側で処理される）
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                result = transcribe(model, audio_from_numpy(audio_float, sample_rate))

            text = result.text.strip()
            if text:
                subtitles.append({"start": current_time, "end": end_time, "text": text})
                print(f"[{current_time:6.1f}s - {end_time:6.1f}s] {text}", flush=True)

            current_time = end_time

        print("-" * 60, flush=True)
        print(f"完了: {len(subtitles)}件の字幕を生成", flush=True)

    return subtitles


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("使い方: python transcribe_k2.py <音声ファイル> [チャンク秒数]")
        sys.exit(1)

    chunk = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    stream_transcribe(sys.argv[1], chunk_seconds=chunk)
