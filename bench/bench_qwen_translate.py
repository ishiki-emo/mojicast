# -*- coding: utf-8 -*-
"""小型LLM（Qwen2.5 0.5B）で翻訳できるか試す

専用の翻訳モデル（M2M-100 / SMaLL-100）は語彙が固定で、v0.9.6 で分かった弱点
（`配信`→`배달(配達)`、訳せない行が韓国語で1割、意味の反転）を抱えたままだった。
LLM なら **指示で制約できる**のが違い:
  - 「原文に無いことを足すな」と書ける
  - 未完結な断片を「そのまま訳す」よう指示できる
  - 固有名詞をプロンプトで与えられる

一方、自己回帰生成なので**速度とメモリが不利**。字幕はリアルタイム性が要るので、
そこが実用ラインに乗るかが分かれ目。

実行: reazonspeech-env\\Scripts\\python.exe bench\\bench_qwen_translate.py [--target ko]
"""
import argparse
import ctypes
import os
import sys
import time
from ctypes import wintypes

sys.stdout.reconfigure(encoding="utf-8")
BENCH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH)
sys.path.insert(0, ROOT)

import ctranslate2

import translate as T

QWEN_DIR = os.path.join(BENCH, "qwen25_05b_ct2_int8")
LANG_NAME = {"ko": "Korean", "zh": "Chinese", "en": "English",
             "id": "Indonesian"}

CASES = [
    "こんばんは、今日も配信を始めます。",
    "全然大丈夫じゃない",
    "わからん",
    "コメントありがとう、めっちゃ嬉しいです。",
    "このボス強すぎませんか、もう10回も負けてます。",
    "普通に生きてたと思ったら一家離散して。",
    "おるか様の誕生日グッズの追加発注したものが全部そろったので",
    "そういえば昨日面白いことがあって、散歩してたら猫がついてきちゃったんですよ。",
]


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


class Qwen:
    def __init__(self, d=QWEN_DIR, threads=4):
        import transformers

        self.tok = transformers.AutoTokenizer.from_pretrained(
            "Qwen/Qwen2.5-0.5B-Instruct")
        self.gen = ctranslate2.Generator(d, device="cpu", inter_threads=1,
                                         intra_threads=threads)
        self.size = sum(os.path.getsize(os.path.join(d, f))
                        for f in os.listdir(d)) / 1e6

    def translate(self, text, tgt, max_new_tokens=96):
        """字幕向けの指示。原文に無いことを足させないのが狙い"""
        lang = LANG_NAME.get(tgt, tgt)
        msgs = [
            {"role": "system",
             "content": (f"You translate Japanese live-stream captions into {lang}. "
                         "Output only the translation. Do not add anything that is "
                         "not in the source. If the sentence is cut off, translate "
                         "it as-is without completing it.")},
            {"role": "user", "content": text},
        ]
        prompt = self.tok.apply_chat_template(msgs, tokenize=False,
                                              add_generation_prompt=True)
        tokens = self.tok.convert_ids_to_tokens(self.tok.encode(prompt))
        res = self.gen.generate_batch(
            [tokens], max_length=max_new_tokens, sampling_topk=1,
            include_prompt_in_result=False,
            end_token=[self.tok.eos_token_id,
                       self.tok.convert_tokens_to_ids("<|im_end|>")])
        return self.tok.decode(res[0].sequences_ids[0],
                               skip_special_tokens=True).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="ko")
    a = ap.parse_args()
    if not os.path.exists(os.path.join(QWEN_DIR, "model.bin")):
        print(f"未変換: {QWEN_DIR}")
        return

    T.load_translator_zh()
    base = mem_mb()
    t0 = time.perf_counter()
    q = Qwen()
    load_s = time.perf_counter() - t0
    used = mem_mb() - base
    m2m_size = sum(os.path.getsize(os.path.join(r, f))
                   for r, _, fs in os.walk(T._resolve_dir_m2m(download=False))
                   for f in fs) / 1e6

    print(f"  M2M-100  {m2m_size:.0f}MB")
    print(f"  Qwen2.5  {q.size:.0f}MB  ロード{load_s:.1f}s  メモリ+{used:.0f}MB")
    print(f"  翻訳先: {a.target}\n")
    print("=" * 78)

    t_m2m = t_qwen = 0.0
    for s in CASES:
        t0 = time.perf_counter()
        m = T.translate_m2m(s, "ja", a.target)
        t_m2m += time.perf_counter() - t0
        t0 = time.perf_counter()
        w = q.translate(s, a.target)
        t_qwen += time.perf_counter() - t0
        print(f"\n  原文  : {s}")
        print(f"  M2M   : {m}")
        print(f"  Qwen  : {w}")

    n = len(CASES)
    print("\n" + "=" * 78)
    print(f"  速度: M2M {t_m2m / n * 1000:.0f}ms/文  /  "
          f"Qwen {t_qwen / n * 1000:.0f}ms/文  （{t_qwen / t_m2m:.1f}倍）")


if __name__ == "__main__":
    main()
