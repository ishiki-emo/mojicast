# -*- coding: utf-8 -*-
"""修正で字幕の見え方がどう変わるかを、実配信ログから並べて確認する

fix/translate-unk-guard の2つの変更を実際の行に当てて、修正前後を対比する。

  1. <unk> 検知   … 訳せなかった行は訳を出さず原文へフォールバック
                    （engine.py が on_translation(fid, en or src, True) する）
  2. 固有名詞保護 … 英訳辞書の「大文字始まり」を多言語訳にも適用

配信を回さずに見え方を判断するための材料。実際の字幕では、訳が空になった行は
原文（日本語）がそのまま出る。

実行: reazonspeech-env\\Scripts\\python.exe bench\\preview_translate_changes.py
      [--target ko] [--limit 400] [--show 25]
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
import wordstore

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


def old_decode(hypothesis):
    """修正前の挙動: <unk> を黙って取り除いて残りを繋げる"""
    kept = [t for t in hypothesis
            if not (t.startswith("__") and t.endswith("__"))
            and t not in ("</s>", "<pad>", "<unk>")]
    out = T._sp_m2m.decode(kept).replace("<unk>", "").strip()
    return out if re.search(r"\w", out) else ""


def run(text, tgt):
    """1行を訳し、(修正前の訳, 修正後の訳) を返す"""
    tokens = T._sp_m2m.encode(text, out_type=str)[:510]
    res = T._m2m.translate_batch([["__ja__"] + tokens + ["</s>"]],
                                 target_prefix=[[f"__{tgt}__"]], beam_size=1,
                                 repetition_penalty=1.2, no_repeat_ngram_size=3,
                                 max_decoding_length=96)
    h = res[0].hypotheses[0]
    return old_decode(h), T._decode_m2m(h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="ko")
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--show", type=int, default=25)
    a = ap.parse_args()

    lines = load_lines(a.limit)
    T.load_translator_zh()
    gloss = [(ja, en) for ja, en in wordstore.merged_glossary("")
             if en[:1].isupper()]
    print(f"実配信ログ {len(lines)}行 / 翻訳先 {a.target}")
    print(f"多言語に効く辞書エントリ: {gloss or '（なし）'}\n")

    dropped, kept, saved = [], 0, []
    for s in lines:
        before, after = run(s, a.target)
        if before and not after:
            # 固有名詞を英字にすると訳せるようになる行があるので、辞書適用後も見る
            rescued = ""
            if gloss:
                t = s
                for ja, en in gloss:
                    t = t.replace(ja, en)
                if t != s:
                    _, rescued = run(t, a.target)
            (saved if rescued else dropped).append((s, before, rescued))
        elif after:
            kept += 1

    n = len(lines)
    print("=" * 78)
    print("■ 字幕の変化")
    print("=" * 78)
    print(f"  訳がそのまま出る    : {kept:5d} ({kept / n:6.1%})")
    print(f"  訳を出さず原文表示へ: {len(dropped):5d} ({len(dropped) / n:6.1%})")
    if saved:
        print(f"  辞書で救われる行    : {len(saved):5d} ({len(saved) / n:6.1%})")

    print("\n" + "=" * 78)
    print(f"■ 原文表示に変わる行（先頭{a.show}件）")
    print("   ※ 字幕には日本語がそのまま出る。右は修正前に出ていた訳")
    print("=" * 78)
    for s, before, _ in dropped[:a.show]:
        print(f"\n  字幕: {s}")
        print(f"  旧訳: {before}")

    if saved:
        print("\n" + "=" * 78)
        print("■ 辞書の固有名詞で訳せるようになる行")
        print("=" * 78)
        for s, before, rescued in saved[:a.show]:
            print(f"\n  原文  : {s}")
            print(f"  修正前: {before}")
            print(f"  修正後: {rescued}")


if __name__ == "__main__":
    main()
