# -*- coding: utf-8 -*-
"""翻訳の言語別レポート — 英・中・韓・インドネシア語を同一条件で測る

これまでの実測は目的ごとにバラバラだった（英訳のビームは4,766行、M2M のビームは
1,600行、CT2 移行時の数値は別データ）。**同じログ・同じ行数・現行の設定**で並べ、
「ローカルでリアルタイム翻訳を作るとどうなるか」の参考値にする。

測るのは本番と同じ関数（`translate()` / `translate_m2m()`）。ビーム幅も
`_BEAM_SIZE` / `_M2M_BEAM` の現行値がそのまま効くので、**利用者が実際に見る品質**が
出る。ベンチ専用の経路は通さない。

失敗モードの分類:
  - 訳が出ない … 字幕には原文（日本語）が出る。内訳は下の2つ
      <unk>     … 語彙外。モデルが読めなかった
      記号のみ  … 反復抑止の副産物で「。」等の断片になった
  - 捏造した罵倒語 … 原文に無い罵倒語（英訳のみ・_fabricated_insult が捨てる）
  - 反復暴走 … 同じ語の繰り返しが止まらない（#7）

実行: reazonspeech-env\Scripts\python.exe bench\bench_translate_langs.py [--limit 9999]
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

LANGS = ["en", "zh", "ko", "id"]
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


def why_empty(text, lang):
    """訳が空になった理由を調べる（本番経路が捨てた後なので、低レベルで測り直す）

    戻り値: "unk" | "symbol" | "insult" | "other"
    """
    if lang == "en":
        src = T._apply_stream_terms(text)
        tokens = T._sp_src.encode(src, out_type=str)[:511] + ["</s>"]
        h = T._translator.translate_batch(
            [tokens], beam_size=T._BEAM_SIZE, repetition_penalty=1.2,
            no_repeat_ngram_size=3, max_decoding_length=96)[0].hypotheses[0]
        if "<unk>" in h:
            return "unk"
        en = T._fix_case(T._sp_tgt.decode(
            [t for t in h if t not in ("</s>", "<pad>", "<unk>")]).strip())
        if T._fabricated_insult(src, en):
            return "insult"
        return "symbol" if en else "other"

    model_tgt = "zh" if lang in T._ZH_VARIANT_CONFIGS else lang
    src_text = text
    if model_tgt == "zh":
        for pat, zh in T._STREAM_TERMS_ZH:
            src_text = pat.sub(zh, src_text)
    elif model_tgt == "id":
        for pat, indonesian in T._STREAM_TERMS_ID:
            src_text = pat.sub(indonesian, src_text)
    tokens = T._sp_m2m.encode(src_text, out_type=str)[:510]
    h = T._m2m.translate_batch(
        [["__ja__"] + tokens + ["</s>"]], target_prefix=[[f"__{model_tgt}__"]],
        beam_size=T._M2M_BEAM.get(model_tgt, 1),
        repetition_penalty=1.2 if model_tgt in ("ko", "zh") else 1.0,
        no_repeat_ngram_size=3, max_decoding_length=96)[0].hypotheses[0]
    return "unk" if "<unk>" in h else "symbol"


def run(lines, lang):
    n = len(lines)
    t0 = time.perf_counter()
    if lang == "en":
        outs = [T.translate(s) for s in lines]
    else:
        outs = [T.translate_m2m(s, "ja", lang) for s in lines]
    el = time.perf_counter() - t0

    empty = [i for i, o in enumerate(outs) if not o]
    reasons = {"unk": 0, "symbol": 0, "insult": 0, "other": 0}
    for i in empty:
        reasons[why_empty(lines[i], lang)] += 1
    runaway = sum(1 for o in outs if o and is_runaway(o))
    chars = sum(len(o) for o in outs if o)
    got = n - len(empty)
    return {
        "lang": lang, "ms": el / n * 1000, "n": n,
        "empty": len(empty), "runaway": runaway,
        "chars": chars / got if got else 0.0, "reasons": reasons,
        "outs": outs,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=9999)
    ap.add_argument("--show", type=int, default=8)
    a = ap.parse_args()

    lines = load_lines(a.limit)
    n = len(lines)
    avg_src = sum(len(s) for s in lines) / n
    print(f"■ 実配信ログ {n}行 / 原文 平均{avg_src:.1f}字")
    print(f"  beam: 英訳={T._BEAM_SIZE} / M2M={T._M2M_BEAM}（既定1）\n")

    T.load_translator()
    T.load_translator_zh()

    res = []
    for lang in LANGS:
        r = run(lines, lang)
        res.append(r)
        print(f"  {lang} 完了 {r['ms']:.1f}ms/文")

    print("\n" + "=" * 78)
    print("■ 速度と失敗モード（同一ログ・現行設定）")
    print("=" * 78)
    print(f"  {'言語':4s} {'モデル':10s} {'ms/文':>8s} {'訳が出ない':>10s} "
          f"{'<unk>':>7s} {'記号のみ':>8s} {'捏造':>5s} {'反復暴走':>8s} {'平均字数':>8s}")
    for r in res:
        model = "FuguMT" if r["lang"] == "en" else "M2M-100"
        rs = r["reasons"]
        print(f"  {r['lang']:4s} {model:10s} {r['ms']:8.1f} "
              f"{r['empty']:6d}({r['empty'] / r['n']:4.1%}) {rs['unk']:7d} "
              f"{rs['symbol']:8d} {rs['insult']:5d} {r['runaway']:8d} {r['chars']:8.1f}")

    print("\n" + "=" * 78)
    print(f"■ 同じ原文を4言語で（先頭{a.show}件・訳が出ない行は「—」）")
    print("=" * 78)
    shown = 0
    for i, s in enumerate(lines):
        if len(s) < 12:
            continue
        print(f"\n  原文: {s[:52]}")
        for r in res:
            print(f"    {r['lang']:3s} {r['outs'][i][:60] or '—'}")
        shown += 1
        if shown >= a.show:
            break


if __name__ == "__main__":
    main()
