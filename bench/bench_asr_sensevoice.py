"""ASRモデル比較ベンチ: ReazonSpeech k2（現行） vs SenseVoice int8（多言語/軽量候補）

実運用と同じく Silero VAD で発話ごとに区切り、同一セグメントを両モデルに
食わせて速度（RTF）と出力テキストを比較する。正解文がないため精度は
「相互差異率＋目視」で評価する（句読点・空白は正規化して比較）。

使い方:
    reazonspeech-env\\Scripts\\python.exe bench\\bench_asr_sensevoice.py [wav ...]
    （wav省略時はリポジトリ直下の 20260708.wav）

前提: SenseVoice のONNXモデル一式（sherpa-onnx配布物）を --sv-dir に置く。
    https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models
    sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2
"""
import argparse
import os
import re
import sys
import time
import wave

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

DEFAULT_SV_DIR = os.path.join(
    os.environ.get("TEMP", ""), "sensevoice_test",
    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17")


def load_wav_16k(path):
    """wav → float32 mono 16kHz（48k/32kは整数間引き＋平均の簡易LPF）"""
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr == 16000:
        return x
    if sr % 16000 == 0:
        k = sr // 16000
        x = x[: len(x) // k * k].reshape(-1, k).mean(axis=1)
        return x.astype(np.float32)
    raise SystemExit(f"未対応サンプルレート: {sr}（16k/32k/48kのみ）")


def vad_segments(samples):
    """Silero VAD で発話セグメントに分割（エンジンの分割に近い設定）"""
    import sherpa_onnx
    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = os.path.join(ROOT, "silero_vad.onnx")
    cfg.silero_vad.threshold = 0.5
    cfg.silero_vad.min_silence_duration = 0.3   # engineの silence_ms=300 相当
    cfg.silero_vad.min_speech_duration = 0.25
    try:
        cfg.silero_vad.max_speech_duration = 12  # engineの max_utt=12s 相当
    except AttributeError:
        pass
    cfg.sample_rate = 16000
    vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=300)
    win = cfg.silero_vad.window_size
    for i in range(0, len(samples) - win + 1, win):
        vad.accept_waveform(samples[i:i + win])
    vad.flush()
    segs = []
    while not vad.empty():
        segs.append((vad.front.start / 16000.0,
                     np.array(vad.front.samples, dtype=np.float32)))
        vad.pop()
    return segs


def recognize(rec, samples):
    t0 = time.perf_counter()
    s = rec.create_stream()
    s.accept_waveform(16000, samples)
    rec.decode_stream(s)
    return s.result.text.strip(), time.perf_counter() - t0


_PUNCT = re.compile(r"[、。，．,.!?！？・\s]")


def normalize(text):
    """句読点・空白を除去して比較用に正規化（SVは句読点込みで出すため）"""
    return _PUNCT.sub("", text)


def char_diff_rate(a, b):
    """正規化後の文字列間の編集距離 / 長い方の長さ（相互差異率）"""
    a, b = normalize(a), normalize(b)
    if not a and not b:
        return 0.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] / max(len(a), len(b))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wavs", nargs="*",
                    default=[os.path.join(ROOT, "20260708.wav")])
    ap.add_argument("--sv-dir", default=DEFAULT_SV_DIR)
    ap.add_argument("--threads", type=int, default=4)
    args = ap.parse_args()

    import sherpa_onnx
    sv = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=os.path.join(args.sv_dir, "model.int8.onnx"),
        tokens=os.path.join(args.sv_dir, "tokens.txt"),
        num_threads=args.threads, use_itn=True, language="ja")
    from asr_model import load_model
    k2 = load_model(precision="int8-fp32", num_threads=args.threads)

    tot = {"dur": 0.0, "k2": 0.0, "sv": 0.0}
    diffs = []
    for path in args.wavs:
        print(f"\n=== {os.path.basename(path)} ===")
        samples = load_wav_16k(path)
        segs = vad_segments(samples)
        print(f"VADセグメント数: {len(segs)}")
        for start, seg in segs:
            dur = len(seg) / 16000.0
            t_k2, dt_k2 = recognize(k2, seg)
            t_sv, dt_sv = recognize(sv, seg)
            tot["dur"] += dur
            tot["k2"] += dt_k2
            tot["sv"] += dt_sv
            d = char_diff_rate(t_k2, t_sv)
            diffs.append(d)
            print(f"\n[{start:6.1f}s +{dur:4.1f}s] 差異 {d*100:4.1f}% "
                  f"(k2 {dt_k2:.2f}s / SV {dt_sv:.2f}s)")
            print(f"  k2: {t_k2}")
            print(f"  SV: {t_sv}")

    print("\n" + "=" * 60)
    print(f"合計音声: {tot['dur']:.1f}s")
    print(f"k2 : 処理 {tot['k2']:.2f}s / RTF {tot['k2']/tot['dur']:.3f}")
    print(f"SV : 処理 {tot['sv']:.2f}s / RTF {tot['sv']/tot['dur']:.3f}")
    print(f"相互差異率: 平均 {np.mean(diffs)*100:.1f}% / 最大 {max(diffs)*100:.1f}%")
    print("※差異は「どちらかが誤り」の上限。句読点・空白は除外して比較。")


if __name__ == "__main__":
    main()
