# -*- coding: utf-8 -*-
"""sherpa-onnx のバージョン差分ベンチ — 同じ音声を本番と同じ経路で認識し、結果をjsonlに残す

本番(engine.py)と同じく Silero VAD で発話に区切り → 0.9秒の無音パディング → k2 で認識。
greedy（単語登録なし）・modified_beam_search（ホットワードあり）・SenseVoice（多言語モード）を回す。
バージョンごとに別venvで実行し、出力jsonlを --diff で突き合わせる。

実行:
  <venv>\\Scripts\\python.exe bench\\bench_sherpa_version.py --wav 20260708.wav --out a.jsonl
  python bench\\bench_sherpa_version.py --diff a.jsonl b.jsonl
"""
import argparse
import json
import os
import sys
import time
import wave

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_RATE = 16000
PAD_SECONDS = 0.9
HOTWORDS = ["いやしきえも", "もじきゃすと", "はいしん", "こらぼ"]  # vocab.write_hotwords と同じく読みを1行ずつ


def rss_mb():
    import ctypes
    from ctypes import wintypes

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]
    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    gpmi = ctypes.windll.psapi.GetProcessMemoryInfo
    gpmi.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
    gpmi(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
    return pmc.WorkingSetSize / 2**20, pmc.PeakWorkingSetSize / 2**20


def load_wav(path):
    import numpy as np
    with wave.open(path) as w:
        assert w.getsampwidth() == 2, "16bit PCM のみ対応"
        ch, sr = w.getnchannels(), w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr != SAMPLE_RATE:  # 線形補間で十分（両バージョンで同じ入力になればよい）
        n = int(len(x) * SAMPLE_RATE / sr)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    return x


def segment(sherpa_onnx, x, min_silence_ms=500):
    c = sherpa_onnx.VadModelConfig()
    c.silero_vad.model = os.path.join(ROOT, "silero_vad.onnx")
    c.silero_vad.threshold = 0.5
    c.silero_vad.min_silence_duration = min_silence_ms / 1000.0
    c.silero_vad.min_speech_duration = 0.25
    c.silero_vad.window_size = 512
    c.sample_rate = SAMPLE_RATE
    vad = sherpa_onnx.VoiceActivityDetector(c, buffer_size_in_seconds=30)
    segs = []
    for i in range(0, len(x), 512):
        vad.accept_waveform(x[i:i + 512])
        while not vad.empty():
            segs.append(list(vad.front.samples))
            vad.pop()
    vad.flush()
    while not vad.empty():
        segs.append(list(vad.front.samples))
        vad.pop()
    return segs


def run(args):
    import numpy as np
    import sherpa_onnx
    sys.path.insert(0, ROOT)
    import asr_model

    segs = []
    total = 0
    for w in args.wav:  # 複数ファイルは発話を連結して1セットとして扱う
        x = load_wav(w)
        total += len(x)
        segs += segment(sherpa_onnx, x)
    print(f"sherpa-onnx {sherpa_onnx.__version__}  音声 {total/SAMPLE_RATE:.0f}秒  発話 {len(segs)}個")

    hw_path = os.path.join(os.path.dirname(os.path.abspath(args.out)), "_hotwords.txt")
    with open(hw_path, "w", encoding="utf-8") as f:
        f.write("\n".join(HOTWORDS) + "\n")

    out = open(args.out, "w", encoding="utf-8")
    base_rss = rss_mb()[0]
    for mode, hw in (("greedy", ""), ("hotwords", hw_path), ("sensevoice", None)):
        t0 = time.perf_counter()
        if mode == "sensevoice":  # 多言語モード。本番同様パディングなし
            rec = asr_model.load_sensevoice(num_threads=args.threads)
        else:
            rec = asr_model.load_model(hotwords_file=hw, num_threads=args.threads,
                                       precision=args.precision)
        load_s = time.perf_counter() - t0
        dec = 0.0
        for i, s in enumerate(segs):
            s = np.asarray(s, dtype=np.float32)
            if mode != "sensevoice":
                s = np.pad(s, int(PAD_SECONDS * SAMPLE_RATE))
            t = time.perf_counter()
            st = rec.create_stream()
            st.accept_waveform(SAMPLE_RATE, s)
            rec.decode_stream(st)
            dec += time.perf_counter() - t
            out.write(json.dumps({"mode": mode, "i": i, "text": st.result.text.strip()},
                                 ensure_ascii=False) + "\n")
        cur, peak = rss_mb()
        summ = {"mode": mode, "summary": True, "version": sherpa_onnx.__version__,
                "load_s": round(load_s, 2), "decode_s": round(dec, 2),
                "rss_mb": round(cur - base_rss), "peak_mb": round(peak)}
        out.write(json.dumps(summ, ensure_ascii=False) + "\n")
        print(summ)
        del rec
    out.close()


def diff(a_path, b_path):
    def load(p):
        rows = [json.loads(l) for l in open(p, encoding="utf-8")]
        texts = {(r["mode"], r["i"]): r["text"] for r in rows if not r.get("summary")}
        summ = {r["mode"]: r for r in rows if r.get("summary")}
        return texts, summ
    ta, sa = load(a_path)
    tb, sb = load(b_path)
    for mode in sa:
        print(f"[{mode}] {sa[mode]['version']} → {sb[mode]['version']}  "
              f"decode {sa[mode]['decode_s']}s → {sb[mode]['decode_s']}s  "
              f"load {sa[mode]['load_s']}s → {sb[mode]['load_s']}s  "
              f"RSS +{sa[mode]['rss_mb']}MB → +{sb[mode]['rss_mb']}MB")
        keys = sorted(k for k in ta if k[0] == mode)
        changed = [k for k in keys if ta[k] != tb.get(k)]
        print(f"  認識文の差分: {len(changed)}/{len(keys)}行")
        for k in changed[:20]:
            print(f"   #{k[1]}\n    旧: {ta[k]}\n    新: {tb.get(k)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", nargs="+")
    ap.add_argument("--out")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--precision", default="int8-fp32")
    ap.add_argument("--diff", nargs=2)
    a = ap.parse_args()
    diff(*a.diff) if a.diff else run(a)
