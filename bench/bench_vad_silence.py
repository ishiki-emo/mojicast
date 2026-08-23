# -*- coding: utf-8 -*-
"""VADの「話し終わりの区切り」(silence_ms) が字幕の切れ方をどう変えるか

設定は前からあるが（settings.html の「字幕の区切り」）、早口の人ほど字幕が
長くなる悩みと結びつきにくい。説明に書く指針を数字で裏づけるための実測。

silence_ms を振って、同じ音声が何区切りになるか・1区切りの長さがどう変わるかを見る。
ASRは通さない（区切り方だけを見たいので VAD だけ回す）。

注意: ここで出る「最長」は VAD 単体の値。実アプリでは「一区切りの最長」
（max_utt・既定12秒）で強制確定が入るため、これより長い行にはならない。

実行: reazonspeech-env\\Scripts\\python.exe bench\\bench_vad_silence.py [wav...]
"""
import os
import sys
import wave

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
BENCH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH)
sys.path.insert(0, ROOT)

import sherpa_onnx

VAD_MODEL = os.path.join(ROOT, "silero_vad.onnx")
SAMPLE_RATE = 16000
WINDOW_SIZE = 512
# UIのスライダーは 100〜800ms（step 50・既定300）
SILENCE_MS = [100, 150, 200, 300, 400, 600, 800]


def load_wav(path):
    """16kHz mono float32 へ。48kHz等は単純間引きで十分（VADの区切り比較用）"""
    with wave.open(path) as w:
        n, ch, sr = w.getnframes(), w.getnchannels(), w.getframerate()
        raw = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        raw = raw.reshape(-1, ch).mean(axis=1)
    if sr != SAMPLE_RATE:
        idx = (np.arange(int(len(raw) * SAMPLE_RATE / sr)) * sr / SAMPLE_RATE)
        raw = raw[idx.astype(np.int64).clip(0, len(raw) - 1)]
    return raw


def build_vad(min_silence_ms, max_utt=12.0):
    """engine.py の _build_vad と同じ設定"""
    c = sherpa_onnx.VadModelConfig()
    c.silero_vad.model = VAD_MODEL
    c.silero_vad.threshold = 0.5
    c.silero_vad.min_silence_duration = min_silence_ms / 1000.0
    c.silero_vad.min_speech_duration = 0.25
    c.silero_vad.window_size = WINDOW_SIZE
    c.sample_rate = SAMPLE_RATE
    return sherpa_onnx.VoiceActivityDetector(c, buffer_size_in_seconds=max_utt + 5)


def segments(samples, min_silence_ms):
    vad = build_vad(min_silence_ms)
    out = []
    for i in range(0, len(samples), WINDOW_SIZE):
        vad.accept_waveform(samples[i:i + WINDOW_SIZE])
        while not vad.empty():
            out.append(len(vad.front.samples) / SAMPLE_RATE)
            vad.pop()
    vad.flush()
    while not vad.empty():
        out.append(len(vad.front.samples) / SAMPLE_RATE)
        vad.pop()
    return out


def main():
    paths = sys.argv[1:] or [os.path.join(ROOT, "20260708.wav")]
    for p in paths:
        if not os.path.exists(p):
            print(f"  見つかりません: {p}")
            continue
        samples = load_wav(p)
        dur = len(samples) / SAMPLE_RATE
        print("=" * 74)
        print(f"■ {os.path.basename(p)}   {dur:.1f}秒")
        print("=" * 74)
        print(f"  {'silence_ms':>10} {'区切り数':>8} {'1区切り平均':>12} "
              f"{'最長':>8} {'発話合計':>10}")
        base = None
        for ms in SILENCE_MS:
            segs = segments(samples, ms)
            if not segs:
                print(f"  {ms:>10} {0:>8}")
                continue
            n, avg, mx = len(segs), sum(segs) / len(segs), max(segs)
            if base is None:
                base = n
            mark = "  ← 既定" if ms == 300 else ""
            print(f"  {ms:>10} {n:>8} {avg:>10.2f}秒 {mx:>6.2f}秒 "
                  f"{sum(segs):>8.1f}秒{mark}")
        print()


if __name__ == "__main__":
    main()
