"""Uncached, repeated VAD/ASR cost measurements with production partial scheduling.

CPU seconds / audio seconds is single-core-equivalent load, not Task Manager %.
Audio arrives on a simulated 32 ms clock; latency is an estimate, not GUI timing.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import statistics
import sys
import time

os.environ['HF_HUB_OFFLINE'] = '1'
import numpy as np
import sherpa_onnx
import soundfile as sf
from scipy.signal import resample_poly
from math import gcd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import CaptionEngine, SAMPLE_RATE as SR, WINDOW_SIZE as WS, _should_emit_partial
import asr_model


def load(path):
    x, sr = sf.read(path, dtype='float32', always_2d=True)
    g = gcd(sr, SR)
    return resample_poly(x.mean(axis=1), SR // g, sr // g).astype(np.float32)


def build(minimum):
    c = sherpa_onnx.VadModelConfig()
    c.silero_vad.model = str(ROOT / 'silero_vad.onnx')
    c.silero_vad.threshold = 0.5
    c.silero_vad.min_silence_duration = 0.3
    c.silero_vad.min_speech_duration = minimum
    c.silero_vad.window_size = WS
    c.sample_rate = SR
    return sherpa_onnx.VoiceActivityDetector(c, buffer_size_in_seconds=30)


def run(engine, samples, minimum, partials):
    vad = build(minimum)  # exclude model construction from steady-state timing
    x = np.pad(samples, (0, SR))
    x = np.pad(x, (0, (-len(x)) % WS))
    last_len, gap = 0, int(.4 * SR)
    finish = 0.
    final_ms, partial_ms, lag_ms, rows = [], [], [], []
    cpu_start, wall_start = time.process_time(), time.perf_counter()
    for offset in range(0, len(x), WS):
        arrival = (offset + WS) / SR
        block_start = time.perf_counter()
        begin = max(arrival, finish)
        vad.accept_waveform(x[offset:offset + WS])
        while not vad.empty():
            start = vad.front.start
            segment = np.array(vad.front.samples, dtype=np.float32)
            vad.pop()
            t = time.perf_counter()
            text = engine._recognize(segment)  # deliberately no result cache
            final_ms.append((time.perf_counter() - t) * 1000)
            done = begin + time.perf_counter() - block_start
            end = (start + len(segment)) / SR
            rows.append({'start': start / SR, 'end': end, 'text': text,
                         'estimated_final_delay_ms': (done - end) * 1000})
            last_len, gap = 0, int(.4 * SR)
        if vad.is_speech_detected():
            cur = np.array(vad.current_segment.samples, dtype=np.float32)
            if _should_emit_partial({'final_only': not partials}, len(cur), last_len, gap):
                last_len = len(cur)
                t = time.perf_counter()
                engine._recognize(cur[-6 * SR:])
                dt = time.perf_counter() - t
                partial_ms.append(dt * 1000)
                gap = max(int(.4 * SR), int(dt * 2 * SR))
            if len(cur) >= 12 * SR:
                vad.flush()
        finish = begin + time.perf_counter() - block_start
        lag_ms.append((finish - arrival) * 1000)
    assert vad.empty(), 'Unprocessed final segment at end of fixture'
    return {'audio_seconds': len(samples) / SR, 'fed_seconds': len(x) / SR,
            'wall_seconds': time.perf_counter() - wall_start,
            'cpu_seconds': time.process_time() - cpu_start,
            'final_ms': final_ms, 'partial_ms': partial_ms,
            'max_estimated_backlog_ms': max(lag_ms), 'segments': rows}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--short-dir', required=True)
    p.add_argument('--fast-dir', required=True)
    p.add_argument('--repeats', type=int, default=3)
    p.add_argument('--output', default=str(ROOT / 'bench/vad_cost_results.json'))
    args = p.parse_args()
    corpora = {'recording': [('20260708.wav', load(ROOT / '20260708.wav'))]}
    for name, folder in [('short', args.short_dir), ('fast', args.fast_dir)]:
        folder = Path(folder)
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8-sig'))
        corpora[name] = [(f"{row['expected']} (rate={row['rate']})",
                          np.pad(load(folder / row['file']), (SR, SR))) for row in manifest]
    rng = np.random.default_rng(20261105)
    clicks = np.zeros(10 * SR, np.float32)
    for i in range(1, 10):
        clicks[i * SR:i * SR + 80] = rng.normal(0, .2, 80)
    corpora['noise'] = [('silence', np.zeros(10 * SR, np.float32)),
                        ('white_noise', rng.normal(0, .01, 10 * SR).astype(np.float32)),
                        ('clicks', clicks)]
    engine = CaptionEngine()
    engine._model, engine._asr_caps = asr_model.load_by_config('k2-ja', precision='int8-fp32')
    # Warm up both model paths. All measured recognition calls still execute.
    for _ in range(2):
        run(engine, corpora['recording'][0][1][:4 * SR], .15, True)
    report = {'sherpa_onnx': sherpa_onnx.__version__, 'logical_cpus': os.cpu_count(),
              'repeats': args.repeats, 'threshold': .5, 'silence_seconds': .3,
              'interval_seconds': .4, 'asr': 'k2-ja int8-fp32; one thread; no hotwords',
              'excluded': ['model loading', 'punctuation', 'translation', 'UI', 'audio device'],
              'latency': 'Simulated serial worker on 32 ms arrivals; final delay relative to VAD segment end, not annotated speech end.',
              'fixtures': {k: [{'label': label, 'seconds': len(x) / SR,
                                'sha256_float32': hashlib.sha256(x.tobytes()).hexdigest()}
                               for label, x in values] for k, values in corpora.items()},
              'runs': []}
    order = [(minimum, partials) for minimum in (.25, .15, .10, .075, .05)
             for partials in (True, False)]
    shuffle = random.Random(20261105)
    for repeat in range(args.repeats):
        shuffle.shuffle(order)
        for minimum, partials in order:
            for name, fixtures in corpora.items():
                runs = [dict(label=label, **run(engine, x, minimum, partials)) for label, x in fixtures]
                report['runs'].append({'repeat': repeat, 'minimum': minimum,
                                       'partials': partials, 'corpus': name, 'fixtures': runs})
            print(f'repeat={repeat + 1} min={minimum} partials={partials} done', flush=True)
            Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    summary = []
    for minimum, partials in sorted(order):
        for corpus in corpora:
            matches = [r for r in report['runs'] if (r['minimum'], r['partials'], r['corpus']) == (minimum, partials, corpus)]
            durations = [sum(f['audio_seconds'] for f in r['fixtures']) for r in matches]
            wall = [sum(f['wall_seconds'] for f in r['fixtures']) for r in matches]
            cpu = [sum(f['cpu_seconds'] for f in r['fixtures']) for r in matches]
            finals = [v for r in matches for f in r['fixtures'] for v in f['final_ms']]
            delays = [s['estimated_final_delay_ms'] for r in matches for f in r['fixtures'] for s in f['segments']]
            summary.append({'minimum': minimum, 'partials': partials, 'corpus': corpus,
                            'audio_seconds': durations[0], 'wall_median': statistics.median(wall),
                            'wall_min': min(wall), 'wall_max': max(wall),
                            'cpu_median': statistics.median(cpu),
                            'one_core_percent': statistics.median(cpu) / durations[0] * 100,
                            'rtf': statistics.median(wall) / durations[0],
                            'final_count': [sum(len(f['final_ms']) for f in r['fixtures']) for r in matches],
                            'partial_count': [sum(len(f['partial_ms']) for f in r['fixtures']) for r in matches],
                            'nonempty_fixture_count': [sum(any(s['text'] for s in f['segments']) for f in r['fixtures']) for r in matches],
                            'final_decode_median_ms': statistics.median(finals) if finals else None,
                            'estimated_final_delay_p95_ms': float(np.percentile(delays, 95)) if delays else None,
                            'max_estimated_backlog_ms': max(f['max_estimated_backlog_ms'] for r in matches for f in r['fixtures'])})
    report['summary'] = summary
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
