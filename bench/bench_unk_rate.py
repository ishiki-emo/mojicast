# -*- coding: utf-8 -*-
"""M2M翻訳の <unk> 発生率と「捨てた場合」の影響を実配信ログで測る

translate.py は出力の <unk> を黙って削除している（フィルタ＋replace）。
そのため語彙に無い語が混ざると、残骸だけが字幕に出る:
    「うどん」  → tokens ['▁','<unk>','이']        → 「이」
    「しゃがんで。」→ tokens ['▁','<unk>','을','▁','<unk>','다'] → 「을 다」
どちらも韓国語として無意味。ユーザー辞書の固有名詞も同じ経路で消える。

「<unk> を含む訳は捨てて空を返す」に変えると、engine.py の _translate_loop が
原文へフォールバックする（on_translation(fid, en or src, not en)）ので字幕は
消えない。その代わり訳が出ない行が増える。そのトレードオフを数で見る。

実行: reazonspeech-env\\Scripts\\python.exe bench\\bench_unk_rate.py [--limit N]
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

# 行頭のタイムスタンプ等を落として本文だけ取る
_STRIP = re.compile(r"^\s*(\[[^\]]*\]|\d{1,2}:\d{2}(:\d{2})?)\s*")


def load_lines(limit_per_file):
    """(配信日, 本文) のリスト。話題の偏りを見るため配信日を持ち回る"""
    lines = []
    for p in sorted(glob.glob(os.path.join(ROOT, "logs", "*", "*_transcript.txt"))):
        day = os.path.basename(os.path.dirname(p))
        got = []
        with open(p, encoding="utf-8", errors="replace") as f:
            for raw in f:
                s = _STRIP.sub("", raw).strip()
                if len(s) >= 2 and not s.startswith("#"):
                    got.append((day, s))
                if len(got) >= limit_per_file:
                    break
        lines += got
        print(f"  {os.path.basename(p)}: {len(got)}行")
    return lines


def raw_translate(text, tgt):
    """1行を訳し、(修正前の訳, 修正後の訳) を返す"""
    tokens = T._sp_m2m.encode(text, out_type=str)[:510]
    res = T._m2m.translate_batch([["__ja__"] + tokens + ["</s>"]],
                                 target_prefix=[[f"__{tgt}__"]], beam_size=1,
                                 repetition_penalty=1.2, no_repeat_ngram_size=3,
                                 max_decoding_length=96)
    h = res[0].hypotheses[0]
    # 修正前: <unk> を黙って削除して残りを繋げていた
    kept = [t for t in h
            if not (t.startswith("__") and t.endswith("__"))
            and t not in ("</s>", "<pad>", "<unk>")]
    before = T._sp_m2m.decode(kept).replace("<unk>", "").strip()
    if not re.search(r"\w", before):
        before = ""
    return before, T._decode_m2m(h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=300,
                    help="1ファイルあたりの行数（既定300）")
    ap.add_argument("--targets", default="ko,zh")
    a = ap.parse_args()

    print("実配信ログを読み込み中...")
    lines = load_lines(a.limit)
    print(f"  合計 {len(lines)}行\n")
    T.load_translator_zh()

    for tgt in a.targets.split(","):
        n = before_empty = after_empty = newly_dropped = 0
        per_day = {}
        samples = []
        for day, s in lines:
            before, after = raw_translate(s, tgt)
            n += 1
            before_empty += not before
            after_empty += not after
            d = per_day.setdefault(day, [0, 0])
            d[0] += 1
            if before and not after:
                newly_dropped += 1
                d[1] += 1
                if len(samples) < 15:
                    samples.append((s, before))

        print("=" * 78)
        print(f"■ target = {tgt}   （{n}行）")
        print("=" * 78)
        print(f"  修正前に訳が空      : {before_empty:5d} ({before_empty / n:6.1%})")
        print(f"  修正後に訳が空      : {after_empty:5d} ({after_empty / n:6.1%})")
        print(f"  新たに捨てた行      : {newly_dropped:5d} ({newly_dropped / n:6.1%})"
              "  ← 残骸を出すのをやめ、原文表示に変わる分")
        print("\n  ── 配信日ごとの内訳（話題の偏りを見る）──")
        for day, (tot, drop) in sorted(per_day.items()):
            print(f"    {day}: {drop:4d}/{tot:4d} ({drop / tot:6.1%})")
        print("\n  ── 捨てるようになった訳（原文 → 修正前に出ていた訳）──")
        for s, before in samples:
            print(f"    {s[:38]:38} → {before[:34]}")
        print()


if __name__ == "__main__":
    main()
