# -*- coding: utf-8 -*-
"""中国語翻訳（translate_m2m）の反復暴走・ゴミ出力の回帰チェック（#7）

実配信で壊れた入力（Issue #7 のログ抜粋）を translate_m2m に通し、
反復暴走・記号だけのゴミ出力が再発していないかを検査する。
対照として通常の配信口語も流し、訳が壊れていないか目視できる形で出す。

使い方:
    reazonspeech-env\\Scripts\\python.exe bench\\bench_m2m_repetition.py

終了コード: 0=全て健全 / 1=暴走かゴミ出力が再発

経緯（2026-08-18 実測）: greedy（repetition_penalty=1.0）では実配信3298文中
104件（3.2%）が壊れていた（暴走90・空14）。repetition_penalty=1.2 と
no_repeat_ngram_size=3 の併用で暴走0件・対照12文の劣化なし・速度差なし。
空出力はモデルの限界として残る（ゴミ断片は空に落として字幕に出さない）。
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import translate

# Issue #7 の実配信ログより。greedy では全件が暴走していた
RUNAWAY_CASES = [
    "もうそれならもうカタカナも追加しようっと。",
    "あわんこちゃんやほ。",
    "おっいいよいいよ。",
    "あのみんちゃんみん。",
    "フフッ。生きてんのすごいんだけど。",
    "ちょっと字幕をさ。今全部の字幕がさ。",
    "そう。そう。いやいやこれからですよ。",
    "あっ来た。来た。来たキラ。",
    "サーブンスクイックありがとうクッシーくんに当たってる。",
    "ピッピ。地下二階六％ピッピに一階だと一％。",
    "さっきサブスクしてもらってね。サブスクもちょっと変えたいね。",
]
# greedy では空出力だった入力。「空のまま」は許容（モデルの限界）だが、
# 「。」「<unk>」のような記号だけの断片を返したら失格
EMPTY_CASES = ["うどん", "しゃがんで。", "やばい。やばい。やばい。", "ポジポジポジポジ。"]

CONTROL_CASES = [
    "こんばんは、今日もよろしくお願いします。",
    "今日はゲームの続きからやっていきます。",
    "コメントありがとう、嬉しいです。",
    "次の曲を歌います。リクエストありがとう。",
    "明日は20時から配信予定です。",
    "初見さんいらっしゃい、ゆっくりしていってね。",
    "スーパーチャットありがとうございます。",
]


def is_runaway(out: str) -> bool:
    """同一文字の長い連続、または同一チャンクの3連続以上"""
    return bool(re.search(r"(.)\1{7,}", out) or re.search(r"(.{2,12})\1{2,}", out))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("M2M-100 ロード中…")
    translate.load_translator_zh()
    failed = 0

    print("\n== 暴走していた入力（Issue #7） ==")
    for s in RUNAWAY_CASES:
        out = translate.translate_m2m(s, "ja", "zh")
        bad = is_runaway(out) or (out and not re.search(r"\w", out))
        failed += bad
        print(f"  {'NG' if bad else 'ok'}  {s[:18]:<20} → {out[:40]}")

    print("\n== 空出力だった入力（ゴミ断片を出さないこと） ==")
    for s in EMPTY_CASES:
        out = translate.translate_m2m(s, "ja", "zh")
        bad = bool(out) and not re.search(r"\w", out)
        failed += bad
        print(f"  {'NG' if bad else 'ok'}  {s:<20} → {out!r}")

    print("\n== 対照（通常口語・目視用） ==")
    t0 = time.time()
    for s in CONTROL_CASES:
        out = translate.translate_m2m(s, "ja", "zh")
        bad = is_runaway(out) or not out
        failed += bad
        print(f"  {'NG' if bad else 'ok'}  {s[:18]:<20} → {out[:40]}")
    ms = (time.time() - t0) / len(CONTROL_CASES) * 1000
    print(f"\n速度: {ms:.0f}ms/文   判定: {'FAIL' if failed else 'PASS'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
