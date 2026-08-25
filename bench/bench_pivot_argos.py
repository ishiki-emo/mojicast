# -*- coding: utf-8 -*-
"""ピボット再挑戦: M2M直接(現行) vs FuguMT(ja→en) ＋ Argos(en→ko)

v0.9.6 の調査でピボットは一度不成立になった。2段目に使える
Helsinki-NLP/opus-mt-tc-big-en-ko の重みが壊れていたため（bench_pivot_ko.py）。
Argos Translate の en→ko は別系統（OpenNMT 学習・CTranslate2 で配布・121MB）で、
そちらは壊れていない。ならばピボットは成立するのか、を実配信ログで測り直す。

注意: Argos のモデルは **beam 探索前提**で、greedy（beam_size=1）だと崩れる
（"I don't look like you." → ". .. , ."）。現行 Mojicast は速度優先で beam=1 を
使っているので、ここだけ beam=4 にしている。速度はそのぶん不利になる。

実行: reazonspeech-env\\Scripts\\python.exe bench\\bench_pivot_argos.py [--limit 200]
"""
import argparse
import glob
import os
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
BENCH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH)
sys.path.insert(0, ROOT)

import ctranslate2
import sentencepiece as spm

import translate as T

ARGOS_EN_KO = os.path.join(BENCH, "argos", "en_ko", "en_ko")

CASES = [
    "こんばんは、今日も配信を始めます。",
    "全然大丈夫じゃない",
    "わからん",
    "コメントありがとう、めっちゃ嬉しいです。",
    "このボス強すぎませんか、もう10回も負けてます。",
    "普通に生きてたと思ったら一家離散して。",
    "そういえば昨日面白いことがあって、散歩してたら猫がついてきちゃったんですよ。",
]

_STRIP = re.compile(r"^\s*(\[[^\]]*\]|\d{1,2}:\d{2}(:\d{2})?)\s*")


def load_lines(limit_per_file):
    lines = []
    for p in sorted(glob.glob(os.path.join(ROOT, "logs", "*", "*_transcript.txt"))):
        got = []
        with open(p, encoding="utf-8", errors="replace") as f:
            for raw in f:
                s = _STRIP.sub("", raw).strip()
                if len(s) >= 2:
                    got.append(s)
                if len(got) >= limit_per_file:
                    break
        lines += got
    return lines


class ArgosEnKo:
    def __init__(self, d=ARGOS_EN_KO, threads=4, beam=4):
        with open(os.path.join(d, "sentencepiece.model"), "rb") as f:
            self.sp = spm.SentencePieceProcessor(model_proto=f.read())
        self.tr = ctranslate2.Translator(os.path.join(d, "model"), device="cpu",
                                         inter_threads=1, intra_threads=threads)
        self.beam = beam
        self.size = sum(os.path.getsize(os.path.join(r, f))
                        for r, _, fs in os.walk(d) for f in fs) / 1e6

    def __call__(self, en, max_new_tokens=96):
        if not en.strip():
            return ""
        tokens = self.sp.encode(en, out_type=str)[:510] + ["</s>"]
        res = self.tr.translate_batch([tokens], beam_size=self.beam,
                                      repetition_penalty=1.2,
                                      no_repeat_ngram_size=3,
                                      max_decoding_length=max_new_tokens)
        h = res[0].hypotheses[0]
        if "<unk>" in h:
            return ""
        out = [t for t in h if t not in ("</s>", "<pad>")]
        s = self.sp.decode(out).strip()
        return "" if "<unk>" in s or not re.search(r"\w", s) else s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    a = ap.parse_args()
    if not os.path.exists(os.path.join(ARGOS_EN_KO, "sentencepiece.model")):
        print(f"Argos en→ko が未取得: {ARGOS_EN_KO}")
        return

    T.load_translator_zh()
    T.load_translator()
    argos = ArgosEnKo()
    m2m_size = sum(os.path.getsize(os.path.join(r, f))
                   for r, _, fs in os.walk(T._resolve_dir_m2m(download=False))
                   for f in fs) / 1e6
    fugu_size = sum(os.path.getsize(os.path.join(r, f))
                    for r, _, fs in os.walk(T._resolve_dir(download=False))
                    for f in fs) / 1e6
    print(f"  M2M直接    : {m2m_size:.0f}MB")
    print(f"  ピボット   : FuguMT {fugu_size:.0f}MB ＋ Argos {argos.size:.0f}MB "
          f"= {fugu_size + argos.size:.0f}MB\n")

    print("=" * 78)
    print("■ v0.9.6 で壊れていた文")
    print("=" * 78)
    for s in CASES:
        en = T.translate(s)
        print(f"\n  原文  : {s}")
        print(f"  M2M   : {T.translate_m2m(s, 'ja', 'ko')!r}")
        print(f"  中間en: {en}")
        print(f"  ピボ  : {argos(en)!r}")

    lines = load_lines(a.limit)
    print("\n" + "=" * 78)
    print(f"■ 実配信ログ {len(lines)}行")
    print("=" * 78)
    t0 = time.perf_counter()
    m_empty = sum(1 for s in lines if not T.translate_m2m(s, "ja", "ko"))
    t_m2m = time.perf_counter() - t0
    t0 = time.perf_counter()
    p_empty = 0
    for s in lines:
        en = T.translate(s)
        if not en or not argos(en):
            p_empty += 1
    t_piv = time.perf_counter() - t0
    n = len(lines)
    print(f"  M2M直接  : 訳が出ない {m_empty:4d}/{n} ({m_empty / n:5.1%}) "
          f"/ {t_m2m / n * 1000:6.1f}ms/文")
    print(f"  ピボット : 訳が出ない {p_empty:4d}/{n} ({p_empty / n:5.1%}) "
          f"/ {t_piv / n * 1000:6.1f}ms/文  （{t_piv / t_m2m:.1f}倍）")


if __name__ == "__main__":
    main()
