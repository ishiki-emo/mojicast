# -*- coding: utf-8 -*-
"""英語ピボットを他の言語へ広げられるか（zh / zh_tw / id / ko）

韓国語で成立した ja→en(FuguMT)→ko(Argos) を、他の翻訳先でも比べる。
現行の M2M-100 直接と同じ入力で並べ、v0.9.6 の弱点（訳せない行・「配信」の誤訳）が
言語ごとにどう変わるかを見る。

繁体字は Argos に無いので、現行と同じく簡体字を OpenCC で変換する
（translate.convert_zh_variant）。ピボットでもそこは変わらない。

Argos のパッケージは中身が CTranslate2 モデル＋SentencePiece なので、
argostranslate ライブラリ無しで既存基盤から直に読める。1.9 系には bpe.model も
同梱されるが SentencePiece 形式ではないので、sentencepiece.model の方を使う。

実行: reazonspeech-env\\Scripts\\python.exe bench\\bench_pivot_langs.py [--limit 150]
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

ARGOS = os.path.join(BENCH, "argos")
CASES = [
    "こんばんは、今日も配信を始めます。",
    "全然大丈夫じゃない",
    "わからん",
    "コメントありがとう、めっちゃ嬉しいです。",
    "このボス強すぎませんか、もう10回も負けてます。",
    "そういえば昨日面白いことがあって、散歩してたら猫がついてきちゃったんですよ。",
]
_STRIP = re.compile(r"^\s*(\[[^\]]*\]|\d{1,2}:\d{2}(:\d{2})?)\s*")


def find_pkg(pair):
    """bench/argos/<pair>/ 配下から model/ と spm を持つフォルダを探す"""
    base = os.path.join(ARGOS, pair)
    for root, _dirs, files in os.walk(base):
        # SentencePiece 形式のものだけ扱う。en_id は bpe.model しか持たず
        # （subword-nmt 形式）、この経路では読めないのでスキップされる
        spm_name = "sentencepiece.model" if "sentencepiece.model" in files else None
        if spm_name and os.path.isdir(os.path.join(root, "model")):
            return root, spm_name
    return None, None


class ArgosPair:
    """Argos の en→X を CTranslate2 で回す（beam 前提のモデルなので greedy にしない）"""

    def __init__(self, pair, threads=4, beam=4):
        d, spm_name = find_pkg(pair)
        if not d:
            raise FileNotFoundError(pair)
        with open(os.path.join(d, spm_name), "rb") as f:
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=150)
    a = ap.parse_args()

    T.load_translator()
    T.load_translator_zh()
    pairs = {}
    for code, folder in (("ko", "en_ko"), ("zh", "en_zh"), ("id", "en_id")):
        try:
            pairs[code] = ArgosPair(folder)
        except FileNotFoundError:
            print(f"  （{folder} は未取得）")

    print("■ 手元の文で比較（M2M直接 / ピボット）\n")
    for code, ar in pairs.items():
        print("=" * 78)
        print(f"■ 翻訳先 {code}   Argos {ar.size:.0f}MB")
        print("=" * 78)
        for s in CASES:
            en = T.translate(s)
            print(f"\n  原文: {s}")
            print(f"    M2M : {T.translate_m2m(s, 'ja', code)}")
            print(f"    ピボ: {ar(en)}")
        print()

    # 繁体字は簡体字を OpenCC で変換（現行と同じ流儀）
    if "zh" in pairs:
        print("=" * 78)
        print("■ 繁体字（Argos に無いので簡体字を OpenCC 変換）")
        print("=" * 78)
        for s in CASES[:3]:
            zh = pairs["zh"](T.translate(s))
            print(f"\n  原文  : {s}")
            print(f"    簡体: {zh}")
            print(f"    台湾: {T.convert_zh_variant(zh, 'zh_tw')}")
            print(f"    香港: {T.convert_zh_variant(zh, 'zh_hk')}")

    lines = load_lines(a.limit)
    print("\n" + "=" * 78)
    print(f"■ 実配信ログ {len(lines)}行での訳せなかった率と速度")
    print("=" * 78)
    for code, ar in pairs.items():
        t0 = time.perf_counter()
        m_empty = sum(1 for s in lines if not T.translate_m2m(s, "ja", code))
        t_m = time.perf_counter() - t0
        t0 = time.perf_counter()
        p_empty = sum(1 for s in lines if not ar(T.translate(s)))
        t_p = time.perf_counter() - t0
        n = len(lines)
        print(f"  {code}: M2M直接 {m_empty:3d}/{n} ({m_empty / n:5.1%}) "
              f"{t_m / n * 1000:6.1f}ms  →  ピボット {p_empty:3d}/{n} "
              f"({p_empty / n:5.1%}) {t_p / n * 1000:6.1f}ms")


if __name__ == "__main__":
    main()
