"""Compare VAD minimum speech durations on identical audio (local models only).

python -B bench/bench_short_speech.py [wav] --output bench/short_speech_results.json
The transcripts are raw k2 output, without replacements or punctuation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import numpy as np
import sherpa_onnx
import soundfile as sf
from scipy.signal import resample_poly
from math import gcd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["HF_HUB_OFFLINE"] = "1"
from engine import CaptionEngine, SAMPLE_RATE, WINDOW_SIZE, VAD_MIN_SPEECH_SEC
import asr_model


def detect(audio, minimum=None):
    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = str(ROOT / "silero_vad.onnx")
    config.silero_vad.threshold = 0.5
    config.silero_vad.min_silence_duration = 0.3
    config.silero_vad.min_speech_duration = minimum if minimum is not None else VAD_MIN_SPEECH_SEC
    config.silero_vad.window_size = WINDOW_SIZE
    config.sample_rate = SAMPLE_RATE
    vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=30)
    if minimum is None:
        vad = CaptionEngine()._build_vad(300)
    segments = []
    # Include trailing silence so the last utterance ends naturally.
    padded = np.pad(audio, (0, SAMPLE_RATE))
    for offset in range(0, len(padded), WINDOW_SIZE):
        chunk = padded[offset:offset + WINDOW_SIZE]
        vad.accept_waveform(np.pad(chunk, (0, WINDOW_SIZE - len(chunk))))
        while not vad.empty():
            segments.append((vad.front.start, np.array(vad.front.samples)))
            vad.pop()
        if vad.is_speech_detected() and len(vad.current_segment.samples) >= 12 * SAMPLE_RATE:
            vad.flush()
    while not vad.empty():
        segments.append((vad.front.start, np.array(vad.front.samples)))
        vad.pop()
    return segments


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", nargs="?", default=str(ROOT / "20260708.wav"))
    parser.add_argument("--output", default=str(ROOT / "bench/short_speech_results.json"))
    parser.add_argument("--short-dir", help="Directory created by generate_short_speech.ps1")
    args = parser.parse_args()
    audio, rate = sf.read(args.wav, dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    divisor = gcd(rate, SAMPLE_RATE)
    audio = resample_poly(audio, SAMPLE_RATE // divisor, rate // divisor).astype(np.float32)
    engine = CaptionEngine()
    engine._model, engine._asr_caps = asr_model.load_by_config("k2-ja", precision="int8-fp32")
    cache = {}

    def recognize(samples):
        key = hashlib.sha256(samples.tobytes()).hexdigest()
        if key not in cache:
            cache[key] = engine._recognize(samples)
        return cache[key]

    report = {
        "source": str(Path(args.wav).resolve()),
        "source_sha256": hashlib.sha256(Path(args.wav).read_bytes()).hexdigest(),
        "seconds": len(audio) / SAMPLE_RATE,
        "sherpa_onnx": sherpa_onnx.__version__,
        "config": {"threshold": 0.5, "silence_seconds": 0.3, "max_utterance_seconds": 12,
                   "asr": "k2-ja", "precision": "int8-fp32", "hotwords": False},
        "recording": {}, "synthetic_controls": {}, "short_tts": [],
    }
    def verify_production(samples):
        expected = detect(samples, VAD_MIN_SPEECH_SEC)
        actual = detect(samples)
        assert len(expected) == len(actual), "Production VAD segment count differs"
        assert all(a == b and np.array_equal(x, y)
                   for (a, x), (b, y) in zip(expected, actual)), "Production VAD audio differs"

    verify_production(audio)
    for minimum in (0.25, 0.15, 0.10):
        begin = time.monotonic()
        rows = []
        for start, samples in detect(audio, minimum):
            rows.append({"start": round(start / SAMPLE_RATE, 4),
                         "duration": round(len(samples) / SAMPLE_RATE, 4),
                         "text": recognize(samples)})
        report["recording"][str(minimum)] = rows
        print(json.dumps({"minimum": minimum, "segments": rows,
                          "elapsed": round(time.monotonic() - begin, 2)}, ensure_ascii=False), flush=True)

    # Known non-speech controls, deterministic; not a substitute for real room noise.
    rng = np.random.default_rng(20261105)
    controls = {"silence": np.zeros(SAMPLE_RATE * 10, dtype=np.float32),
                "white_noise_rms_0.01": rng.normal(0, 0.01, SAMPLE_RATE * 10).astype(np.float32)}
    clicks = np.zeros(SAMPLE_RATE * 10, dtype=np.float32)
    for second in range(1, 10):
        clicks[second * SAMPLE_RATE:second * SAMPLE_RATE + 80] = rng.normal(0, 0.2, 80)
    controls["clicks"] = clicks
    for name, samples in controls.items():
        verify_production(samples)
        report["synthetic_controls"][name] = {
            str(minimum): [{"start": start / SAMPLE_RATE, "text": recognize(segment)}
                           for start, segment in detect(samples, minimum)]
            for minimum in (0.25, 0.15, 0.10)
        }
    if args.short_dir:
        manifest = json.loads((Path(args.short_dir) / "manifest.json").read_text(encoding="utf-8-sig"))
        for item in manifest:
            samples, sr = sf.read(Path(args.short_dir) / item["file"], dtype="float32")
            divisor = gcd(sr, SAMPLE_RATE)
            samples = resample_poly(samples, SAMPLE_RATE // divisor, sr // divisor).astype(np.float32)
            active = np.flatnonzero(np.abs(samples) > 0.005)
            # Keep original TTS timing; leading silence ensures a fresh VAD state.
            samples = np.pad(samples, (SAMPLE_RATE, SAMPLE_RATE))
            verify_production(samples)
            row = dict(item, active_span_seconds=round((active[-1] - active[0] + 1) / SAMPLE_RATE, 4) if len(active) else 0,
                       direct_asr=recognize(samples), results={})
            for minimum in (0.25, 0.15, 0.10):
                row["results"][str(minimum)] = [recognize(segment) for _, segment in detect(samples, minimum)]
            report["short_tts"].append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    report["production_vad_verified"] = {"minimum_seconds": VAD_MIN_SPEECH_SEC,
                                          "matches_comparison_sample_for_sample": True}
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Saved:", args.output, flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
