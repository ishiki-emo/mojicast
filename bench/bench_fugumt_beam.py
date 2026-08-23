# -*- coding: utf-8 -*-
"""FuguMT の beam_size を実配信ログ全体で評価する

現行は速度優先で beam_size=1（greedy）。手元の数文では beam=4 で明確に良くなった
（「強すぎませんか」の反語を理解し、beam=1 が捏造していた "I'm afraid" が消える）。
一部が良くなって他が悪化していないかを、実配信ログ全体で確かめる。

ピボット構成（ja→en→ko）では**英訳の改善がそのまま他言語へ波及する**ので、
1段目に投資する価値が大きい。

自動で測れるもの:
  - 速度
  - v0.9.6 で入れたガードの発火数（罵倒語の捏造 / <unk> による空）
  - 反復暴走（#7 の判定を流用）
  - beam=1 からの変化率、文長
  - **beam を上げて新たに壊れた行**（改善だけでなく悪化も数える）

実行: reazonspeech-env\\Scripts\\python.exe bench\\bench_fugumt_beam.py [--limit 9999]
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

import translate as T

BEAMS = [1, 2, 4, 8]
# 反復暴走の判定は bench_m2m_repetition.py と同じ（同一文字の連続／同一チャンクの反復）
_RUNAWAY_CHAR = re.compile(r"(.)\1{7,}")
_RUNAWAY_CHUNK = re.compile(r"(.{2,12}?)\1{2,}")
_STRIP = re.compile(r"^\s*(\[[^\]]*\]|\d{1,2}:\d{2}(:\d{2})?)\s*")


def is_runaway(s):
    return bool(_RUNAWAY_CHAR.search(s) or _RUNAWAY_CHUNK.search(s))


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


def translate_beam(text, beam):
    """translate() と同じ経路。beam だけ差し替え、ガードの発火も返す

    戻り値: (訳文, <unk>が出たか, 罵倒語を捏造したか, 反復暴走したか)
    """
    src = T._apply_stream_terms(text)
    tokens = T._sp_src.encode(src, out_type=str)[:511] + ["</s>"]
    res = T._translator.translate_batch(
        [tokens], beam_size=beam, repetition_penalty=1.2,
        no_repeat_ngram_size=3, max_decoding_length=96)
    h = res[0].hypotheses[0]
    unk = "<unk>" in h
    en = T._fix_case(T._sp_tgt.decode(
        [t for t in h if t not in ("</s>", "<pad>", "<unk>")]).strip())
    insult = T._fabricated_insult(src, en)
    return ("" if insult else en), unk, insult, is_runaway(en)


def healthy(row):
    """訳が出ていて、罵倒語の捏造も反復暴走もしていない"""
    return bool(row[0]) and not row[2] and not row[3]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=9999)
    ap.add_argument("--show", type=int, default=10)
    a = ap.parse_args()

    T.load_translator()
    lines = load_lines(a.limit)
    n = len(lines)
    print(f"■ 実配信ログ {n}行\n")

    out = {}
    for beam in BEAMS:
        t0 = time.perf_counter()
        rows = [translate_beam(s, beam) for s in lines]
        el = time.perf_counter() - t0
        out[beam] = rows
        same = (sum(1 for x, y in zip(rows, out[1]) if x[0] == y[0]) / n
                if beam != 1 else 1.0)
        print(f"  beam={beam}: {el / n * 1000:5.1f}ms/文 / "
              f"捏造した罵倒語 {sum(1 for r in rows if r[2]):3d} / "
              f"<unk> {sum(1 for r in rows if r[1]):3d} / "
              f"反復暴走 {sum(1 for r in rows if r[3]):3d} / "
              f"訳が出ない {sum(1 for r in rows if not r[0]):3d} / "
              f"平均{sum(len(r[0]) for r in rows) / n:5.1f}字 / "
              f"beam=1と同じ {same:5.1%}")

    print("\n" + "=" * 78)
    print("■ beam=1 → beam=4 で健全性がどう変わったか")
    print("=" * 78)
    worse = [i for i in range(n) if healthy(out[1][i]) and not healthy(out[4][i])]
    better = [i for i in range(n) if not healthy(out[1][i]) and healthy(out[4][i])]
    print(f"  悪化（健全→壊れた）: {len(worse)}件")
    for i in worse[:5]:
        print(f"    {lines[i][:34]}")
        print(f"      b1: {out[1][i][0][:56]}")
        print(f"      b4: {out[4][i][0][:56] or '（捨てられた）'}")
    print(f"\n  改善（壊れた→健全）: {len(better)}件")
    for i in better[:5]:
        print(f"    {lines[i][:34]}")
        print(f"      b1: {out[1][i][0][:56] or '（捨てられた）'}")
        print(f"      b4: {out[4][i][0][:56]}")

    print("\n" + "=" * 78)
    print(f"■ 訳が変わった例（先頭{a.show}件）")
    print("=" * 78)
    shown = 0
    for i, s in enumerate(lines):
        e1, e4 = out[1][i][0], out[4][i][0]
        if e1 == e4 or not e1 or not e4:
            continue
        print(f"\n  原文: {s[:44]}")
        print(f"    b1: {e1[:66]}")
        print(f"    b4: {e4[:66]}")
        shown += 1
        if shown >= a.show:
            break


if __name__ == "__main__":
    main()
