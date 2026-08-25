# -*- coding: utf-8 -*-
"""意味の反転（肯定↔否定）がどれだけ起きるかを測る

方針（2026-08-24 えもさん）:
  1. 配信で何を言っているかが**だいたい分かればよい**。海外リスナーも
     機械翻訳と承知の上で見るので、空気感が掴めれば足りる
  2. **明らかな逆転は入れない**。「これ嫌い」と言ったのに「好き」と訳されると、
     VTuber の発言が誤解される。ここだけは避けたい

WER のような一律の精度ではなく、**2 の観点で測る**ベンチ。
原文が否定なのに訳が否定でない（またはその逆）行を「反転の疑い」として数える。

完全な判定はできない（反語「〜じゃない？」など）。あくまで比較用の目安で、
同じ物差しでエンジン同士を並べることに意味がある。

実行: reazonspeech-env\\Scripts\\python.exe bench\\bench_negation_flip.py [--limit 200]
"""
import argparse
import glob
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
BENCH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH)
sys.path.insert(0, ROOT)

import translate as T
from bench_pivot_argos import ArgosEnKo

# 日本語の否定。反語・疑問（「〜じゃない？」「〜ませんか」）は誘いや同意なので除く
_JA_NEG = re.compile(r"(ない|ないです|ません|なかった|ぬ|ずに|じゃなく|ではなく)")
_JA_NEG_EXCEPT = re.compile(r"(じゃない[?？]|ではない[?？]|ませんか|ないですか|"
                            r"ないかな|ないと|なければ|なくちゃ|なきゃ)")
# 韓国語の否定。「안」は空白なしで動詞に付く（안되네요）ので \s は要求しない。
# ただし挨拶の「안녕」に当たるので、そこだけ外す
_KO_NEG = re.compile(r"(안(?!녕)|못\s?[가-힣]|없|지\s?않|아니|말고|마세요|지\s?마)")
# 中国語の否定
_ZH_NEG = re.compile(r"(不|没|無|无|非|别|莫)")

_STRIP = re.compile(r"^\s*(\[[^\]]*\]|\d{1,2}:\d{2}(:\d{2})?)\s*")


def is_neg_ja(s):
    if _JA_NEG_EXCEPT.search(s):
        return None            # 判定しない（反語・条件）
    return bool(_JA_NEG.search(s))


def load_lines(limit_per_file):
    lines = []
    for p in sorted(glob.glob(os.path.join(ROOT, "logs", "*", "*_transcript.txt"))):
        got = []
        with open(p, encoding="utf-8", errors="replace") as f:
            for raw in f:
                s = _STRIP.sub("", raw).strip()
                if len(s) >= 4:
                    got.append(s)
                if len(got) >= limit_per_file:
                    break
        lines += got
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--show", type=int, default=6)
    a = ap.parse_args()

    T.load_translator_zh()
    T.load_translator()
    argos = ArgosEnKo()

    engines = {
        "M2M直接(ko)": lambda s: T.translate_m2m(s, "ja", "ko"),
        "ピボット(ko)": lambda s: argos(T.translate(s)),
        "M2M直接(zh)": lambda s: T.translate_m2m(s, "ja", "zh"),
    }
    neg_of = {"M2M直接(ko)": _KO_NEG, "ピボット(ko)": _KO_NEG,
              "M2M直接(zh)": _ZH_NEG}

    lines = load_lines(a.limit)
    judged = [(s, is_neg_ja(s)) for s in lines]
    judged = [(s, n) for s, n in judged if n is not None]
    n_neg = sum(1 for _s, n in judged if n)
    print(f"■ 実配信ログ {len(lines)}行 → 判定できる {len(judged)}行"
          f"（うち否定文 {n_neg}行）\n")

    for name, fn in engines.items():
        flips_pos, flips_neg, out, shown = 0, 0, 0, []
        for s, neg in judged:
            t = fn(s)
            if not t:
                out += 1        # 訳を出さない＝反転しようがない（原文が出る）
                continue
            tneg = bool(neg_of[name].search(t))
            if neg and not tneg:
                flips_pos += 1  # 否定→肯定（「嫌い」が「好き」になる型）
                if len(shown) < a.show:
                    shown.append(("否定が消えた", s, t))
            elif not neg and tneg:
                flips_neg += 1  # 肯定→否定
                if len(shown) < a.show:
                    shown.append(("否定が湧いた", s, t))
        total = len(judged) - out
        print(f"  {name}")
        print(f"    訳を出さない  : {out:4d}（原文表示になるので反転しない）")
        print(f"    否定→肯定    : {flips_pos:4d}  ← 「嫌い」が「好き」になる型")
        print(f"    肯定→否定    : {flips_neg:4d}")
        print(f"    反転の疑い計  : {flips_pos + flips_neg:4d}/{total} "
              f"({(flips_pos + flips_neg) / max(total, 1):5.1%})")
        for tag, s, t in shown[:3]:
            print(f"      [{tag}] {s[:34]}")
            print(f"                → {t[:40]}")
        print()


if __name__ == "__main__":
    main()
