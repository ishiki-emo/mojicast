# -*- coding: utf-8 -*-
"""多言語ASRの比較: SenseVoice(現行) vs Whisper small(CTranslate2)

SenseVoice は中・英・日・韓・広東語の5言語。Whisper small は100言語で、
インドネシア語やタイ語なども入る。サイズは 240MB vs 486MB。
「SenseVoice を置き換えるか」「その他言語担当として併用するか」を決めるため、
**SenseVoice が既に対応している言語で精度が勝てるか**を同一条件で測る。

音声は google/fleurs（実話者＋正解テキスト・CC-BY-4.0）。合成音声は使わない。
メモリはモデルごとに別プロセスで測る（同一プロセスだと混ざるため）。

事前に bench/fleurs_<lang>/ へ dev.tsv と dev.tar.gz を置くこと
（bench_whisper_id.py のダウンロード部と同じ手順）。

実行: reazonspeech-env\\Scripts\\python.exe bench\\bench_asr_multilingual.py
      [--lang ko] [--n 30]
"""
import argparse
import ctypes
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import time
from ctypes import wintypes

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
BENCH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH)
sys.path.insert(0, ROOT)
SAMPLE_RATE = 16000

# FLEURS の言語コード → (bench側フォルダ, SenseVoice/Whisper の言語指定)
LANGS = {
    "ko": ("fleurs_ko", "ko"),
    "zh": ("fleurs_cmn", "zh"),
    "id": ("fleurs_id", "id"),
}


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


class _FILETIME(ctypes.Structure):
    _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]


# argtypes を書かないと 64bit のプロセスハンドルが int へ切り詰められて落ちる
_k32.GetProcessTimes.restype = wintypes.BOOL
_k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + \
    [ctypes.POINTER(_FILETIME)] * 4


def cpu_sec():
    """このプロセスが使ったCPU時間（全スレッド合計）。

    実時間だけ見ても「何コア分を食っているか」は分からない。音声時間で割れば
    コア換算の占有率になる（0.5 なら1コアの半分をずっと使う相当）。
    """
    c, e, k, u = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
    _k32.GetProcessTimes(_k32.GetCurrentProcess(), ctypes.byref(c),
                         ctypes.byref(e), ctypes.byref(k), ctypes.byref(u))
    to_s = lambda f: ((f.hi << 32) | f.lo) / 1e7   # 100ns 単位
    return to_s(k) + to_s(u)


# --- 誤り率 ---
_PUNC = re.compile(r"[^\w\s]", re.U)


def _tokens(text, char_level):
    """中国語・日本語は語の区切りが無いので文字単位で測る（CER）"""
    t = _PUNC.sub(" ", text.lower())
    return list(t.replace(" ", "")) if char_level else t.split()


def err_rate(ref, hyp, char_level):
    r, h = _tokens(ref, char_level), _tokens(hyp, char_level)
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


def load_samples(folder, n):
    import soundfile as sf

    base = os.path.join(BENCH, folder)
    refs = {}
    with open(os.path.join(base, "dev.tsv"), encoding="utf-8") as f:
        for line in f:
            col = line.rstrip("\n").split("\t")
            if len(col) >= 3:
                refs.setdefault(col[1], col[2])
    out = []
    with tarfile.open(os.path.join(base, "dev.tar.gz")) as tar:
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
            out.append((pcm, refs[name]))
            if len(out) >= n:
                break
    return out


# ---------------------------------------------------------------- 各エンジン
def run_sensevoice(samples, lang):
    import asr_model

    base = mem_mb()
    t0 = time.perf_counter()
    rec, _caps = asr_model.load_by_config("sensevoice", language=lang)
    load_s = time.perf_counter() - t0
    used = mem_mb() - base

    hyps, infer = [], 0.0
    cpu0 = cpu_sec()
    for pcm, _ref in samples:
        t0 = time.perf_counter()
        st = rec.create_stream()
        st.accept_waveform(SAMPLE_RATE, pcm)
        rec.decode_stream(st)
        hyps.append(st.result.text.strip())
        infer += time.perf_counter() - t0
    cpu = cpu_sec() - cpu0
    d = asr_model._resolve_sensevoice(download=False)
    size = sum(os.path.getsize(os.path.join(r, f))
               for r, _, fs in os.walk(d) for f in fs) / 1e6
    return dict(load_s=load_s, mem=used, infer=infer, hyps=hyps, size=size, cpu=cpu)


def run_whisper(samples, lang):
    import ctranslate2
    import transformers

    model_dir = os.path.join(BENCH, "whisper_small_ct2")
    base = mem_mb()
    t0 = time.perf_counter()
    model = ctranslate2.models.Whisper(model_dir, device="cpu",
                                       compute_type="int8")
    proc = transformers.WhisperProcessor.from_pretrained("openai/whisper-small")
    load_s = time.perf_counter() - t0
    used = mem_mb() - base

    prompt = proc.tokenizer.convert_tokens_to_ids(
        ["<|startoftranscript|>", f"<|{lang}|>", "<|transcribe|>",
         "<|notimestamps|>"])
    hyps, infer = [], 0.0
    cpu0 = cpu_sec()
    for pcm, _ref in samples:
        t0 = time.perf_counter()
        feat = proc(pcm, sampling_rate=SAMPLE_RATE,
                    return_tensors="np").input_features
        res = model.generate(ctranslate2.StorageView.from_array(feat),
                             [prompt], beam_size=1)
        hyps.append(proc.decode(res[0].sequences_ids[0],
                                skip_special_tokens=True).strip())
        infer += time.perf_counter() - t0
    cpu = cpu_sec() - cpu0
    size = sum(os.path.getsize(os.path.join(r, f))
               for r, _, fs in os.walk(model_dir) for f in fs) / 1e6
    return dict(load_s=load_s, mem=used, infer=infer, hyps=hyps, size=size, cpu=cpu)


def run_whisper_sherpa(samples, lang):
    """同じ Whisper small を sherpa-onnx で回す。

    CTranslate2版は特徴抽出とトークナイザに transformers が要るが、こちらは
    sherpa-onnx が内蔵しているので**追加依存なしで載る**（ASRの経路も既存のまま）。
    int8 は encoder 112MB + decoder 262MB。
    """
    import sherpa_onnx

    d = os.path.join(BENCH, "whisper_small_sherpa")
    base = mem_mb()
    t0 = time.perf_counter()
    rec = sherpa_onnx.OfflineRecognizer.from_whisper(
        encoder=os.path.join(d, "small-encoder.int8.onnx"),
        decoder=os.path.join(d, "small-decoder.int8.onnx"),
        tokens=os.path.join(d, "small-tokens.txt"),
        language=lang, task="transcribe", num_threads=4)
    load_s = time.perf_counter() - t0
    used = mem_mb() - base

    hyps, infer = [], 0.0
    cpu0 = cpu_sec()
    for pcm, _ref in samples:
        t0 = time.perf_counter()
        st = rec.create_stream()
        st.accept_waveform(SAMPLE_RATE, pcm)
        rec.decode_stream(st)
        hyps.append(st.result.text.strip())
        infer += time.perf_counter() - t0
    cpu = cpu_sec() - cpu0
    size = sum(os.path.getsize(os.path.join(r, f))
               for r, _, fs in os.walk(d) for f in fs) / 1e6
    return dict(load_s=load_s, mem=used, infer=infer, hyps=hyps, size=size, cpu=cpu)


ENGINES = {"sensevoice": run_sensevoice, "whisper": run_whisper,
           "whisper_sherpa": run_whisper_sherpa}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="ko", choices=list(LANGS))
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--engine")          # 子プロセス用
    ap.add_argument("--show", type=int, default=4)
    a = ap.parse_args()

    folder, code = LANGS[a.lang]
    samples = load_samples(folder, a.n)

    if a.engine:                          # 子: 1エンジンだけ測って JSON を返す
        r = ENGINES[a.engine](samples, code)
        print("@@R@@" + json.dumps(r, ensure_ascii=False))
        return

    char_level = a.lang in ("zh", "ja")
    audio_s = sum(len(p) for p, _ in samples) / SAMPLE_RATE
    print(f"■ {a.lang}  FLEURS dev {len(samples)}件 / 音声 {audio_s:.0f}秒")
    print(f"  誤り率は{'文字' if char_level else '単語'}単位"
          f"（{'CER' if char_level else 'WER'}）\n")

    res = {}
    for name in ENGINES:
        print(f"  [{name}] 計測中...", flush=True)
        p = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--lang", a.lang,
             "--n", str(a.n), "--engine", name],
            capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
        line = next((l for l in p.stdout.splitlines()
                     if l.startswith("@@R@@")), None)
        if not line:
            print(f"    失敗: {p.stderr.strip().splitlines()[-3:]}")
            continue
        res[name] = json.loads(line[len("@@R@@"):])

    print("\n" + "=" * 74)
    print(f"{'':14}{'SenseVoice(現行)':>18}{'Whisper CT2':>16}{'Whisper sherpa':>17}")
    print("=" * 74)
    rows = [("モデルサイズ", lambda r: f"{r['size']:.0f}MB"),
            ("ロード", lambda r: f"{r['load_s']:.1f}s"),
            ("メモリ", lambda r: f"+{r['mem']:.0f}MB"),
            ("推論(合計)", lambda r: f"{r['infer']:.1f}s"),
            ("実時間比", lambda r: f"{r['infer'] / audio_s:.3f}x"),
            ("1発話", lambda r: f"{r['infer'] / len(samples) * 1000:.0f}ms"),
            ("CPU時間", lambda r: f"{r['cpu']:.1f}s"),
            ("コア換算の占有", lambda r: f"{r['cpu'] / audio_s:.3f}コア")]
    for label, fn in rows:
        cells = "".join(f"{fn(res[e]) if e in res else '-':>17}"
                        for e in ENGINES)
        print(f"{label:14}{cells}")

    for e in ENGINES:
        if e not in res:
            continue
        errs = words = 0
        for (_pcm, ref), hyp in zip(samples, res[e]["hyps"]):
            v, n = err_rate(ref, hyp, char_level)
            errs += v * n
            words += n
        res[e]["rate"] = errs / words
    cells = "".join(f"{res[e]['rate']:.1%}".rjust(17) if e in res else "-".rjust(17)
                    for e in ENGINES)
    print(f"{'誤り率':14}{cells}")

    print("\n  ── 認識結果の例 ──")
    for i in range(min(a.show, len(samples))):
        print(f"\n    正解      : {samples[i][1][:64]}")
        for e in ENGINES:
            if e in res:
                print(f"    {e:10}: {res[e]['hyps'][i][:64]}")


if __name__ == "__main__":
    main()
