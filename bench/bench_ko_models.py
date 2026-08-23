# -*- coding: utf-8 -*-
"""韓国語訳モデルの比較ベンチ: M2M-100 418M(現行) vs NLLB-200 600M vs ja-ko専用

アプリには組み込まない PoC。ja→ko の品質をどう底上げするかの実測材料。

【経緯】当初はピボット ja→en(FuguMT)→ko を試したが不成立。2段目に使える
Helsinki-NLP/opus-mt-tc-big-en-ko は公開されている重み自体が壊れており
（"I don't know." → "US 9001 호텔"）、transformers で直接推論しても同じ。
CT2変換でも量子化でもなくモデル本体の問題。DL数も ko-en の 1/40 と極端に
少なく、実質使われていない。詳細は bench/bench_pivot_ko.py。

そこで ja→ko 直接の候補を比較する:
  A. M2M-100 418M int8       … 現行。多言語汎用
  B. NLLB-200 distilled 600M … M2M100系アーキなので既存コードの流用が効く
  C. sappho192/aihub-ja-ko   … 日韓専用（EncoderDecoder・torch必須のため参考値）

実行: reazonspeech-env\Scripts\python.exe bench\bench_ko_models.py
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
BENCH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH)
sys.path.insert(0, ROOT)

import ctranslate2
import translate as T

NLLB_CT2 = "JustFrederik/nllb-200-distilled-600M-ct2-int8"
NLLB_DIR = os.path.join(BENCH, "nllb600m_ct2_int8")
JAKO = "sappho192/aihub-ja-ko-translator"

# 実配信で崩れた実例（2026-08-22 の視聴者配信キャプチャ）。
# 現行M2Mが「別の話を捏造する」条件を意図的に含めてある:
#   言い直しの混入 / 句読点なしで複数文が連結 / 「〜ので」で終わる未完結節 /
#   ユーザー辞書の固有名詞（M2Mでは消失した）/ 単語だけの相槌（「알지 못」と途中で切れた）
CORPUS_LIVE = [
    "あの友達が人間の友達が飼ってる",
    "猫にモテるかっこうまいからいや真偽は不明だから",
    "わからん",
    "認識がやっとされるようになりました",
    "おるか様の誕生日グッズの追加発注したものが全部そろったので",
    "かつ翻訳もしてくれる",
]
# 対照群: 句読点あり・完結した通常文（劣化していないかの確認用）
CORPUS_CLEAN = [
    "みなさんこんばんは、今日も配信を始めます。",
    "コメントありがとう、めっちゃ嬉しいです。",
    "このボス強すぎませんか、もう10回も負けてます。",
    "明日は夜の9時から配信予定です、ぜひ見に来てください。",
    "そういえば昨日面白いことがあって、散歩してたら猫がついてきちゃったんですよ。",
]

_nllb = None
_nllb_tok = None


def load_nllb(num_threads: int = 4):
    global _nllb, _nllb_tok
    if _nllb is not None:
        return
    from huggingface_hub import snapshot_download
    from transformers import AutoTokenizer
    d = NLLB_DIR
    if not os.path.exists(os.path.join(d, "model.bin")):
        print(f"nllb: ダウンロード中... ({NLLB_CT2})")
        snapshot_download(NLLB_CT2, local_dir=d)
    # PoCなので tokenizer は transformers を使う。採用するなら M2M と同じく
    # sentencepiece 直叩きへ置き換える（アプリは transformers 非依存のため）
    _nllb_tok = AutoTokenizer.from_pretrained(d, src_lang="jpn_Jpan")
    _nllb = ctranslate2.Translator(d, device="cpu", inter_threads=1,
                                   intra_threads=num_threads)


def translate_nllb(text: str, tgt: str = "kor_Hang",
                   max_new_tokens: int = 96) -> str:
    if not text or not text.strip():
        return ""
    if _nllb is None:
        load_nllb()
    src = _nllb_tok.convert_ids_to_tokens(_nllb_tok.encode(text))
    res = _nllb.translate_batch([src], target_prefix=[[tgt]], beam_size=1,
                                repetition_penalty=1.2, no_repeat_ngram_size=3,
                                max_decoding_length=max_new_tokens)
    out = res[0].hypotheses[0]
    if out and out[0] == tgt:
        out = out[1:]
    return _nllb_tok.decode(_nllb_tok.convert_tokens_to_ids(out),
                            skip_special_tokens=True).strip()


_jako = None
_jako_src = None
_jako_tgt = None


def load_jako():
    """日韓専用モデル（参考値）。EncoderDecoder なので CT2 非対応・torch で回す"""
    global _jako, _jako_src, _jako_tgt
    if _jako is not None:
        return
    from transformers import (AutoTokenizer, BertJapaneseTokenizer,
                              EncoderDecoderModel, PreTrainedTokenizerFast)
    _jako_src = BertJapaneseTokenizer.from_pretrained("cl-tohoku/bert-base-japanese-v2")
    _jako_tgt = PreTrainedTokenizerFast.from_pretrained("skt/kogpt2-base-v2")
    _jako = EncoderDecoderModel.from_pretrained(JAKO)
    _jako.eval()


def translate_jako(text: str, max_new_tokens: int = 96) -> str:
    if _jako is None:
        load_jako()
    emb = _jako_src(text, return_tensors="pt", add_special_tokens=True,
                    truncation=True, max_length=128)
    out = _jako.generate(**emb, max_new_tokens=max_new_tokens, num_beams=1)
    return _jako_tgt.decode(out[0], skip_special_tokens=True).strip()


def median_ms(fn, arg, n=5):
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn(arg)
        ts.append((time.perf_counter() - t0) * 1000)
    return sorted(ts)[len(ts) // 2]


def dir_size_mb(d):
    total = 0
    for root, _, files in os.walk(d):
        for f in files:
            total += os.path.getsize(os.path.join(root, f))
    return total / 1e6


def main():
    print("=" * 78)
    print("韓国語訳 比較: M2M-100 418M(現行) vs NLLB-200 600M vs ja-ko専用")
    print("=" * 78)

    t0 = time.perf_counter()
    T.load_translator_zh()
    t_m2m = time.perf_counter() - t0
    t0 = time.perf_counter()
    load_nllb()
    t_nllb = time.perf_counter() - t0

    jako_ok = True
    try:
        t0 = time.perf_counter()
        load_jako()
        t_jako = time.perf_counter() - t0
    except Exception as e:
        jako_ok = False
        print(f"\n[C] ja-ko専用モデルはスキップ: {type(e).__name__}: {e}")

    print(f"\nロード: M2M {t_m2m:.1f}s / NLLB {t_nllb:.1f}s"
          + (f" / ja-ko {t_jako:.1f}s" if jako_ok else ""))
    print(f"サイズ: M2M {dir_size_mb(T._resolve_dir_m2m(download=False)):.0f}MB"
          f" / NLLB {dir_size_mb(NLLB_DIR):.0f}MB")

    for title, corpus in (("実配信で崩れた実例", CORPUS_LIVE),
                          ("対照群（句読点あり・完結文）", CORPUS_CLEAN)):
        print("\n" + "=" * 78)
        print(f"■ {title}")
        print("=" * 78)
        for src in corpus:
            print(f"\n  原文    : {src}")
            print(f"  A:M2M   : {T.translate_m2m(src, 'ja', 'ko')}")
            print(f"  B:NLLB  : {translate_nllb(src)}")
            if jako_ok:
                try:
                    print(f"  C:ja-ko : {translate_jako(src)}")
                except Exception as e:
                    print(f"  C:ja-ko : <ERR {type(e).__name__}>")

    print("\n" + "=" * 78)
    print("■ レイテンシ（中央値・n=5）")
    print("=" * 78)
    for label, src in (("短文", CORPUS_CLEAN[1]), ("長文", CORPUS_CLEAN[4])):
        a = median_ms(lambda s: T.translate_m2m(s, "ja", "ko"), src)
        b = median_ms(translate_nllb, src)
        line = f"  {label}: M2M {a:6.1f}ms / NLLB {b:6.1f}ms (×{b / a:.2f})"
        if jako_ok:
            try:
                c = median_ms(translate_jako, src)
                line += f" / ja-ko {c:6.1f}ms (×{c / a:.2f})"
            except Exception:
                pass
        print(line + f"  «{src[:24]}»")


if __name__ == "__main__":
    main()
