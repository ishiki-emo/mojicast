# -*- coding: utf-8 -*-
"""翻訳モデルの比較: M2M-100 418M（現行） vs SMaLL-100

SMaLL-100 は M2M-100 12B の蒸留版（330M・MIT）。アーキは同じ M2M100 で
CTranslate2 にそのまま変換でき、語彙も M2M と同じ 128,112。
デコーダが3層と浅い（M2M は12層）ぶん速くて小さいはず。

見たいのは v0.9.6 で分かった弱点が縮むか:
  - 韓国語は10.1%の行が <unk> で訳せていない（中国語2.2%）
  - 「配信」が ['配','信'] に割れて「배달（配達）」になる
  - 「全然大丈夫じゃない」→「没事,没事。」と意味が反転する

呼び出し方が M2M と違う点に注意:
  M2M        source=[__ja__] + tokens + </s>, target_prefix=[[__zh__]]
  SMaLL-100  source=[__zh__] + tokens + </s>（訳先をソース側の先頭に置く）

実行: reazonspeech-env\\Scripts\\python.exe bench\\bench_small100.py [--target ko]
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

SMALL100_DIR = os.path.join(BENCH, "small100_ct2_int8")

# v0.9.6 で実際に壊れていた文。翻訳先ごとの弱点がそのまま出る
CASES = [
    "こんばんは、今日も配信を始めます。",
    "全然大丈夫じゃない",
    "わからん",
    "花粉症。もう私は",
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


class Small100:
    """SMaLL-100 を CTranslate2 で回す（訳先トークンはソース側の先頭）"""

    def __init__(self, d=SMALL100_DIR, threads=4):
        with open(os.path.join(d, "sentencepiece.bpe.model"), "rb") as f:
            self.sp = spm.SentencePieceProcessor(model_proto=f.read())
        self.tr = ctranslate2.Translator(d, device="cpu", inter_threads=1,
                                         intra_threads=threads)
        self.size = sum(os.path.getsize(os.path.join(d, f))
                        for f in os.listdir(d)) / 1e6

    def translate(self, text, tgt, max_new_tokens=96):
        if not text or not text.strip():
            return ""
        tokens = self.sp.encode(text, out_type=str)[:510]
        source = [f"__{tgt}__"] + tokens + ["</s>"]
        res = self.tr.translate_batch([source], beam_size=1,
                                      repetition_penalty=1.2,
                                      no_repeat_ngram_size=3,
                                      max_decoding_length=max_new_tokens)
        h = res[0].hypotheses[0]
        if "<unk>" in h:
            return ""          # 現行と同じ扱い（訳せない行は原文へ倒す）
        out = [t for t in h
               if not (t.startswith("__") and t.endswith("__"))
               and t not in ("</s>", "<pad>")]
        s = self.sp.decode(out).strip()
        return "" if "<unk>" in s or not re.search(r"\w", s) else s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="ko")
    ap.add_argument("--limit", type=int, default=250)
    a = ap.parse_args()

    if not os.path.exists(os.path.join(SMALL100_DIR, "model.bin")):
        print(f"SMaLL-100 が未変換: {SMALL100_DIR}")
        return

    T.load_translator_zh()
    small = Small100()
    m2m_size = sum(os.path.getsize(os.path.join(r, f))
                   for r, _, fs in os.walk(T._resolve_dir_m2m(download=False))
                   for f in fs) / 1e6
    print(f"モデル: M2M-100 {m2m_size:.0f}MB / SMaLL-100 {small.size:.0f}MB")
    print(f"翻訳先: {a.target}\n")

    print("=" * 78)
    print("■ v0.9.6 で壊れていた文")
    print("=" * 78)
    for s in CASES:
        print(f"\n  原文  : {s}")
        print(f"  M2M   : {T.translate_m2m(s, 'ja', a.target)!r}")
        print(f"  SMaLL : {small.translate(s, a.target)!r}")

    lines = load_lines(a.limit)
    print("\n" + "=" * 78)
    print(f"■ 実配信ログ {len(lines)}行での訳せなかった率と速度")
    print("=" * 78)
    for name, fn in (("M2M-100 ", lambda s: T.translate_m2m(s, "ja", a.target)),
                     ("SMaLL-100", lambda s: small.translate(s, a.target))):
        t0 = time.perf_counter()
        empty = sum(1 for s in lines if not fn(s))
        el = time.perf_counter() - t0
        print(f"  {name}: 訳が出ない {empty:4d}/{len(lines)} "
              f"({empty / len(lines):5.1%}) / {el / len(lines) * 1000:5.1f}ms/文")


if __name__ == "__main__":
    main()
