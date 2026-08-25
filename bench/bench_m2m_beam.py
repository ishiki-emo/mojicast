# -*- coding: utf-8 -*-
"""M2M-100 の beam_size を実配信ログ全体で評価する（bench_fugumt_beam.py の M2M 版）

英訳（FuguMT）は 5e955cb で beam=8 にした（捏造した罵倒語 12→5件・+4.7ms/文）。
M2M-100 は今も beam_size=1（greedy）のままなので、同じ投資が中国語・韓国語でも
割に合うかを測る。

M2M 側の弱点は英訳と違って「訳が出ない」こと（v0.9.6 実測: 韓国語10.1% / 中国語2.2%）。
_decode_m2m が <unk> を含む訳と文字を含まない断片を空にして原文へ倒すため、
**空の件数が減るか**が主指標になる。罵倒語ガードは英訳専用なので測らない。

M2M は FuguMT より大きい（418M・常駐727MB）ので、beam を上げたときの速度の代償も
英訳の +4.7ms では済まない可能性がある。#11「複数翻訳の同時表示」の前提にも効く。

実測 2026-08-24（実配信ログ1600行 / int8 / 16C・スレッド4）:

    ja→ko  beam=1 148.7ms  訳が出ない 10.4%  反復暴走 24
           beam=2 163.2ms              3.3%           16
           beam=4 187.4ms              1.6%            1
           beam=8 222.6ms              1.1%            2
    ja→zh  beam=1 130.0ms              2.3%            1
           beam=4 158.2ms              1.8%            0
           beam=8 182.8ms              1.8%            1

beam=1 の 10.4% / 2.3% は v0.9.6 の実測（韓国語10.1% / 中国語2.2%）とほぼ一致する。
**韓国語は beam=4 が分岐点**（8 は +35ms 払って 0.5pt しか縮まらず暴走も減らない）。
中国語は元が 2.3% で伸びしろが小さいので beam=1 のままでよい。#17「英語ピボットは
韓国語限定で効く」と同じ構図で、M2M の日→韓だけが弱くそこだけ手当てで伸びる。

**注意: healthy() は捏造を見抜けない**。「訳が出た＝健全」で数えているが、訳文を
読むと符号が反転する例がある:

    二十五すごい。隅田さんの
      beam=1  25 훌륭한 혜택, 김정은   ← 隅田さんが「金正恩」に化ける
      beam=8  （訳が出ない）           ← 原文へ倒れる＝こちらが正しい
    今日はね適切にミュートができるように（ja→zh）
      beam=1  （訳が出ない）
      beam=8  今天,你可以得到合适的摩托车。 ← 「適切なオートバイ」＝捏造

「悪化」と数えた行がビームによる捏造の抑止だったり、「改善」の中身が誤訳での穴埋め
だったりする。数字で当たりを付けたあと、**必ず訳文を目で見ること**。

実行: reazonspeech-env\Scripts\python.exe bench\bench_m2m_beam.py [--limit 9999] [--langs ko,zh]
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
# 反復暴走の判定は bench_m2m_repetition.py / bench_fugumt_beam.py と同じ
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


def translate_beam(text, beam, tgt):
    """translate_m2m() と同じ経路。beam だけ差し替え、判定材料も返す

    戻り値: (訳文, <unk>が出たか, 反復暴走したか)
    """
    model_tgt = "zh" if tgt in T._ZH_VARIANT_CONFIGS else tgt
    rp = 1.2 if model_tgt in ("ko", "zh") else 1.0
    src_text = text
    if model_tgt == "zh":
        for pat, zh in T._STREAM_TERMS_ZH:
            src_text = pat.sub(zh, src_text)
    elif model_tgt == "id":
        for pat, indonesian in T._STREAM_TERMS_ID:
            src_text = pat.sub(indonesian, src_text)
    tokens = T._sp_m2m.encode(src_text, out_type=str)[:510]
    source = ["__ja__"] + tokens + ["</s>"]
    res = T._m2m.translate_batch(
        [source], target_prefix=[[f"__{model_tgt}__"]], beam_size=beam,
        repetition_penalty=rp, no_repeat_ngram_size=3, max_decoding_length=96)
    h = res[0].hypotheses[0]
    out = T._decode_m2m(h)
    return out, ("<unk>" in h), is_runaway(out)


def healthy(row):
    """訳が出ていて、反復暴走もしていない"""
    return bool(row[0]) and not row[2]


def run_lang(lines, tgt, show):
    n = len(lines)
    print("\n" + "=" * 78)
    print(f"■ ja → {tgt}（{n}行）")
    print("=" * 78)
    out = {}
    for beam in BEAMS:
        t0 = time.perf_counter()
        rows = [translate_beam(s, beam, tgt) for s in lines]
        el = time.perf_counter() - t0
        out[beam] = rows
        same = (sum(1 for x, y in zip(rows, out[1]) if x[0] == y[0]) / n
                if beam != 1 else 1.0)
        empty = sum(1 for r in rows if not r[0])
        print(f"  beam={beam}: {el / n * 1000:6.1f}ms/文 / "
              f"訳が出ない {empty:3d} ({empty / n:4.1%}) / "
              f"<unk> {sum(1 for r in rows if r[1]):3d} / "
              f"反復暴走 {sum(1 for r in rows if r[2]):3d} / "
              f"平均{sum(len(r[0]) for r in rows) / n:5.1f}字 / "
              f"beam=1と同じ {same:5.1%}")

    for b in (4, 8):
        worse = [i for i in range(n) if healthy(out[1][i]) and not healthy(out[b][i])]
        better = [i for i in range(n) if not healthy(out[1][i]) and healthy(out[b][i])]
        print(f"\n  beam=1 → beam={b}: 悪化 {len(worse)}件 / 改善 {len(better)}件")
        for i in better[:3]:
            print(f"    改善 {lines[i][:30]}")
            print(f"      b1: {out[1][i][0][:52] or '（訳が出ない）'}")
            print(f"      b{b}: {out[b][i][0][:52]}")
        for i in worse[:3]:
            print(f"    悪化 {lines[i][:30]}")
            print(f"      b1: {out[1][i][0][:52]}")
            print(f"      b{b}: {out[b][i][0][:52] or '（訳が出ない）'}")

    print(f"\n  訳が変わった例（先頭{show}件）")
    shown = 0
    for i, s in enumerate(lines):
        a, b = out[1][i][0], out[8][i][0]
        if a == b or not a or not b:
            continue
        print(f"    原文: {s[:40]}")
        print(f"      b1: {a[:60]}")
        print(f"      b8: {b[:60]}")
        shown += 1
        if shown >= show:
            break


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=9999, help="1ログあたりの行数上限")
    ap.add_argument("--langs", default="ko,zh", help="翻訳先（カンマ区切り）")
    ap.add_argument("--show", type=int, default=6)
    a = ap.parse_args()

    T.load_translator_zh()
    lines = load_lines(a.limit)
    print(f"■ 実配信ログ {len(lines)}行 / beam {BEAMS}")
    for tgt in [s.strip() for s in a.langs.split(",") if s.strip()]:
        run_lang(lines, tgt, a.show)


if __name__ == "__main__":
    main()
