# -*- coding: utf-8 -*-
"""ja-ko専用モデルの ONNX(int8) 実測: 速度・メモリ・int8劣化

bench_ko_models.py で日韓専用 sappho192/aihub-ja-ko-translator が
M2M-100/NLLB より明確に高品質と分かった。ただし EncoderDecoder のため
CTranslate2 非対応。公式リポジトリの onnxq/（int8量子化済み）を
素の onnxruntime で回し、アプリに載せられるかを見る。
transformers はトークナイザにしか使わない（採用時は要置換）。

測定はモデルごとに別プロセスで行う（同一プロセスだとRSSが混ざるため）。
  --mode m2m   … 現行 M2M-100 418M int8 (CTranslate2)
  --mode onnx  … ja-ko ONNX int8 (onnxruntime)
  --mode torch … ja-ko torch fp32 (参考・int8劣化の対照)

実行: reazonspeech-env\\Scripts\\python.exe bench\\bench_jako_onnx.py
"""
import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes

sys.stdout.reconfigure(encoding="utf-8")
BENCH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH)
sys.path.insert(0, ROOT)

HF_JAKO = "sappho192/aihub-ja-ko-translator"
ONNX_DIR = os.path.join(BENCH, "jako_onnx_int8")
START = EOS = 1          # decoder_start_token_id / eos_token_id（config.json）

CORPUS = [
    "あの友達が人間の友達が飼ってる",
    "猫にモテるかっこうまいからいや真偽は不明だから",
    "わからん",
    "認識がやっとされるようになりました",
    "おるか様の誕生日グッズの追加発注したものが全部そろったので",
    "かつ翻訳もしてくれる",
    "みなさんこんばんは、今日も配信を始めます。",
    "コメントありがとう、めっちゃ嬉しいです。",
    "このボス強すぎませんか、もう10回も負けてます。",
    "明日は夜の9時から配信予定です、ぜひ見に来てください。",
    "そういえば昨日面白いことがあって、散歩してたら猫がついてきちゃったんですよ。",
]
SHORT, LONG = CORPUS[7], CORPUS[10]


class _PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t)]


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_psapi = ctypes.WinDLL("psapi", use_last_error=True)
# argtypes/restype を明示しないと 64bit のプロセスハンドルが int へ切り詰められ、
# 呼び出しが静かに失敗して全項目 0 が返る
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.GetCurrentProcess.argtypes = []
_psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
_psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE,
                                        ctypes.POINTER(_PMC), wintypes.DWORD]


def mem_mb():
    """このプロセスの実メモリ（WorkingSet / Private）。ネイティブ確保も含む"""
    p = _PMC()
    p.cb = ctypes.sizeof(p)
    if not _psapi.GetProcessMemoryInfo(_kernel32.GetCurrentProcess(),
                                       ctypes.byref(p), p.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return p.WorkingSetSize / 1e6, p.PrivateUsage / 1e6


def dir_size_mb(d):
    return sum(os.path.getsize(os.path.join(r, f))
               for r, _, fs in os.walk(d) for f in fs) / 1e6


def median_ms(fn, arg, n=5):
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn(arg)
        ts.append((time.perf_counter() - t0) * 1000)
    return sorted(ts)[len(ts) // 2]


# ---------------------------------------------------------------- 各モード
def build_m2m():
    import translate as T
    T.load_translator_zh()
    return (lambda s: T.translate_m2m(s, "ja", "ko"),
            dir_size_mb(T._resolve_dir_m2m(download=False)))


def build_onnx():
    import shutil
    import numpy as np
    import onnxruntime as ort
    from huggingface_hub import hf_hub_download
    from transformers import BertJapaneseTokenizer, PreTrainedTokenizerFast

    os.makedirs(ONNX_DIR, exist_ok=True)
    for f in ("onnxq/encoder_model.onnx", "onnxq/decoder_model_merged.onnx"):
        dst = os.path.join(ONNX_DIR, os.path.basename(f))
        if not os.path.exists(dst):
            shutil.copyfile(hf_hub_download(HF_JAKO, f), dst)

    so = ort.SessionOptions()
    so.intra_op_num_threads, so.inter_op_num_threads = 4, 1
    enc = ort.InferenceSession(os.path.join(ONNX_DIR, "encoder_model.onnx"),
                               so, providers=["CPUExecutionProvider"])
    dec = ort.InferenceSession(
        os.path.join(ONNX_DIR, "decoder_model_merged.onnx"), so,
        providers=["CPUExecutionProvider"])
    nlayer = sum(1 for i in dec.get_inputs() if i.name.endswith(".key"))
    sp = BertJapaneseTokenizer.from_pretrained("cl-tohoku/bert-base-japanese-v2")
    tp = PreTrainedTokenizerFast.from_pretrained("skt/kogpt2-base-v2")

    def run(text, max_new=96):
        if not text or not text.strip():
            return ""
        b = sp(text, return_tensors="np", truncation=True, max_length=128)
        h = enc.run(None, {"input_ids": b["input_ids"].astype("int64"),
                           "attention_mask": b["attention_mask"].astype("int64")})[0]
        past = {f"past_key_values.{i}.{k}": np.zeros((1, 12, 0, 64), np.float32)
                for i in range(nlayer) for k in ("key", "value")}
        ids, use_cache, out = np.array([[START]], "int64"), np.array([False]), []
        for _ in range(max_new):
            r = dec.run(None, {"input_ids": ids, "encoder_hidden_states": h,
                               "use_cache_branch": use_cache, **past})
            nxt = int(r[0][0, -1].argmax())
            if nxt == EOS and out:
                break
            out.append(nxt)
            past = {f"past_key_values.{i}.{k}": r[1 + i * 2 + j]
                    for i in range(nlayer) for j, k in enumerate(("key", "value"))}
            ids, use_cache = np.array([[nxt]], "int64"), np.array([True])
        return tp.decode(out, skip_special_tokens=True).strip()

    return run, dir_size_mb(ONNX_DIR)


def build_torch():
    from transformers import (BertJapaneseTokenizer, EncoderDecoderModel,
                              PreTrainedTokenizerFast)
    sp = BertJapaneseTokenizer.from_pretrained("cl-tohoku/bert-base-japanese-v2")
    tp = PreTrainedTokenizerFast.from_pretrained("skt/kogpt2-base-v2")
    m = EncoderDecoderModel.from_pretrained(HF_JAKO)
    m.eval()

    def run(text, max_new=96):
        b = sp(text, return_tensors="pt", truncation=True, max_length=128)
        o = m.generate(**b, max_new_tokens=max_new, num_beams=1)
        return tp.decode(o[0], skip_special_tokens=True).strip()

    return run, 1059.0   # model.safetensors（fp32）


BUILDERS = {"m2m": build_m2m, "onnx": build_onnx, "torch": build_torch}


def run_one(mode):
    base_ws, base_pv = mem_mb()
    t0 = time.perf_counter()
    fn, size = BUILDERS[mode]()
    load_s = time.perf_counter() - t0
    fn("ウォームアップします。")
    ws, pv = mem_mb()
    res = {"mode": mode, "load_s": round(load_s, 2), "size_mb": round(size),
           "ws_mb": round(ws - base_ws), "pv_mb": round(pv - base_pv),
           "short_ms": round(median_ms(fn, SHORT), 1),
           "long_ms": round(median_ms(fn, LONG), 1),
           "out": [fn(s) for s in CORPUS]}
    print("@@RESULT@@" + json.dumps(res, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=list(BUILDERS) + ["all"], default="all")
    a = ap.parse_args()
    if a.mode != "all":
        return run_one(a.mode)

    results = {}
    for m in ("m2m", "onnx", "torch"):
        print(f"[{m}] 計測中...", flush=True)
        p = subprocess.run([sys.executable, os.path.abspath(__file__),
                            "--mode", m], capture_output=True, text=True,
                           encoding="utf-8", cwd=ROOT)
        line = next((l for l in p.stdout.splitlines()
                     if l.startswith("@@RESULT@@")), None)
        if not line:
            print(f"  失敗: {p.stderr.strip().splitlines()[-3:]}")
            continue
        results[m] = json.loads(line[len("@@RESULT@@"):])

    print("\n" + "=" * 78)
    print("■ 速度・メモリ・サイズ（別プロセス計測）")
    print("=" * 78)
    print(f"{'':24}{'M2M(現行)':>16}{'ja-ko ONNX int8':>18}{'ja-ko torch fp32':>18}")
    rows = [("モデルサイズ(MB)", "size_mb"), ("ロード(s)", "load_s"),
            ("メモリ WorkingSet(MB)", "ws_mb"), ("メモリ Private(MB)", "pv_mb"),
            ("短文(ms)", "short_ms"), ("長文(ms)", "long_ms")]
    for label, key in rows:
        cells = "".join(f"{results.get(m, {}).get(key, '-'):>18}"
                        for m in ("m2m", "onnx", "torch"))
        print(f"{label:24}{cells}")

    if "onnx" in results and "torch" in results:
        same = sum(a == b for a, b in zip(results["onnx"]["out"],
                                          results["torch"]["out"]))
        print(f"\n■ int8劣化: ONNX int8 と torch fp32 の一致 "
              f"{same}/{len(CORPUS)} 文")
        print("=" * 78)
        for i, src in enumerate(CORPUS):
            o, t = results["onnx"]["out"][i], results["torch"]["out"][i]
            mark = "  " if o == t else "≠ "
            print(f"\n{mark}原文  : {src}")
            print(f"  ONNX  : {o}")
            if o != t:
                print(f"  torch : {t}")


if __name__ == "__main__":
    main()
