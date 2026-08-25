# -*- coding: utf-8 -*-
"""インドネシア語の音声認識を faster-whisper(CTranslate2) で測る

SenseVoice は中・英・日・韓・広東語だけで、インドネシア語は認識できない
（翻訳先には id があるので「日本語で話す→インドネシア語字幕」は可能だが逆は不可）。
インドネシア語には ReazonSpeech 級のコーパスが無く、実在するのは Whisper 系。
CTranslate2 は翻訳で既に使っているので、sherpa-onnx を置き換えずに足せる。

測るのは 精度(WER) / 速度(実時間比) / メモリ。small で実用になるか、
medium(1.5GB) が要るかでサイズの判断が変わる。

音声は google/fleurs の id_id dev（実話者＋正解テキスト・CC-BY-4.0）。
合成音声だと実配信と条件が違うので使わない。

実行: reazonspeech-env\\Scripts\\python.exe bench\\bench_whisper_id.py
      [--model bench/whisper_small_ct2] [--n 40] [--compute int8]
"""
import argparse
import ctypes
import os
import re
import sys
import tarfile
import time

from ctypes import wintypes

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
BENCH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH)
FLEURS = os.path.join(BENCH, "fleurs_id")
SAMPLE_RATE = 16000


class _PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("pf", wintypes.DWORD),
                ("pws", ctypes.c_size_t), ("ws", ctypes.c_size_t),
                ("qppp", ctypes.c_size_t), ("qpp", ctypes.c_size_t),
                ("qpnp", ctypes.c_size_t), ("qnp", ctypes.c_size_t),
                ("pfu", ctypes.c_size_t), ("ppfu", ctypes.c_size_t),
                ("priv", ctypes.c_size_t)]


_k32 = ctypes.WinDLL("kernel32")
_psapi = ctypes.WinDLL("psapi")
_k32.GetCurrentProcess.restype = wintypes.HANDLE
_psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE,
                                        ctypes.POINTER(_PMC), wintypes.DWORD]


def mem_mb():
    p = _PMC()
    p.cb = ctypes.sizeof(p)
    _psapi.GetProcessMemoryInfo(_k32.GetCurrentProcess(), ctypes.byref(p), p.cb)
    return p.ws / 1e6


# --- WER: 単語単位の編集距離 / 正解語数 ---
_NORM = re.compile(r"[^\w\s]", re.U)


def normalize(text):
    """比較用に句読点を落として小文字化（字幕では表記より語の一致を見たい）"""
    return _NORM.sub(" ", text.lower()).split()


def wer(ref, hyp):
    r, h = normalize(ref), normalize(hyp)
    if not r:
        return 0.0, 0
    d = np.arange(len(h) + 1)
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
            prev = cur
    return d[len(h)] / len(r), len(r)


def load_samples(n):
    """dev.tar.gz から先頭n件を取り出す（正解は dev.tsv の3列目）"""
    refs = {}
    with open(os.path.join(FLEURS, "dev.tsv"), encoding="utf-8") as f:
        for line in f:
            col = line.rstrip("\n").split("\t")
            if len(col) >= 3:
                refs.setdefault(col[1], col[2])
    import io

    import soundfile as sf   # FLEURS の wav は float32 で、標準の wave では読めない

    out = []
    with tarfile.open(os.path.join(FLEURS, "dev.tar.gz")) as tar:
        for m in tar:
            if not m.name.endswith(".wav"):
                continue
            name = os.path.basename(m.name)
            if name not in refs:
                continue
            pcm, sr = sf.read(io.BytesIO(tar.extractfile(m).read()),
                              dtype="float32", always_2d=False)
            if pcm.ndim > 1:
                pcm = pcm.mean(axis=1)
            if sr != SAMPLE_RATE:
                continue
            out.append((name, pcm, refs[name]))
            if len(out) >= n:
                break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.path.join(BENCH, "whisper_small_ct2"))
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--compute", default="int8")
    ap.add_argument("--show", type=int, default=5)
    a = ap.parse_args()

    import ctranslate2
    import transformers

    print(f"音声を読み込み中（FLEURS id_id dev・{a.n}件）...")
    samples = load_samples(a.n)
    total_audio = sum(len(s[1]) for s in samples) / SAMPLE_RATE
    print(f"  {len(samples)}件 / 音声 計{total_audio:.0f}秒\n")

    base = mem_mb()
    t0 = time.perf_counter()
    model = ctranslate2.models.Whisper(a.model, device="cpu",
                                       compute_type=a.compute)
    proc = transformers.WhisperProcessor.from_pretrained("openai/whisper-small")
    load_s = time.perf_counter() - t0
    used = mem_mb() - base

    size = sum(os.path.getsize(os.path.join(r, f))
               for r, _, fs in os.walk(a.model) for f in fs) / 1e6
    print(f"モデル: {os.path.basename(a.model)}  {size:.0f}MB  "
          f"compute={a.compute}")
    print(f"  ロード {load_s:.1f}s / メモリ +{used:.0f}MB\n")

    errs = words = 0
    infer_s = 0.0
    shown = []
    for name, pcm, ref in samples:
        t0 = time.perf_counter()
        feat = proc(pcm, sampling_rate=SAMPLE_RATE,
                    return_tensors="np").input_features
        sf = ctranslate2.StorageView.from_array(feat)
        prompt = proc.tokenizer.convert_tokens_to_ids(
            ["<|startoftranscript|>", "<|id|>", "<|transcribe|>", "<|notimestamps|>"])
        res = model.generate(sf, [prompt], beam_size=1)
        hyp = proc.decode(res[0].sequences_ids[0], skip_special_tokens=True).strip()
        infer_s += time.perf_counter() - t0
        e, n = wer(ref, hyp)
        errs += e * n
        words += n
        if len(shown) < a.show:
            shown.append((ref, hyp, e))

    print("=" * 76)
    print("■ 結果")
    print("=" * 76)
    print(f"  WER        : {errs / words:.1%}  （単語誤り率・低いほど良い）")
    print(f"  推論時間   : {infer_s:.1f}秒 / 音声 {total_audio:.0f}秒 "
          f"= 実時間比 {infer_s / total_audio:.2f}x")
    print(f"  1発話あたり: {infer_s / len(samples) * 1000:.0f}ms")
    print(f"  メモリ     : +{used:.0f}MB")
    print("\n  ── 認識結果の例 ──")
    for ref, hyp, e in shown:
        print(f"\n    正解: {ref[:70]}")
        print(f"    認識: {hyp[:70]}   (WER {e:.0%})")


if __name__ == "__main__":
    main()
