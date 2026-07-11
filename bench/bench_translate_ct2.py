# -*- coding: utf-8 -*-
"""FuguMT 翻訳: transformers(現行) vs CTranslate2(fp32/int8) の比較ベンチ

アプリには組み込まない PoC。将来の ONNX/CT2 移行判断の実測材料を取る。
測定: ロード時間 / 1行レイテンシ(中央値) / 訳文の一致率と差分

実行: reazonspeech-env\Scripts\python.exe bench\bench_translate_ct2.py
前提: bench\fugumt_ct2_fp32, bench\fugumt_ct2_int8 (ct2-transformers-converter で変換済み)
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
BENCH = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(BENCH))   # translate.py を import するため

# 配信っぽい実文サンプル（短文〜長文・グロッサリ置換後想定のラテン名入り）
CORPUS = [
    "こんにちは。",
    "みなさんこんばんは、今日も配信を始めます。",
    "ISHIKI Emoです、よろしくお願いします。",
    "今日は新しいゲームをやっていきます。",
    "コメントありがとう、めっちゃ嬉しいです。",
    "ちょっと待ってね、設定を確認します。",
    "このボス強すぎませんか、もう10回も負けてます。",
    "Mojicastの新機能を紹介します、英訳辞書が使えるようになりました。",
    "明日は夜の9時から配信予定です、ぜひ見に来てください。",
    "今日は新しい機能のテストをしていきます、みなさんのコメントもどんどん読んでいきますので、よろしくお願いします。",
    "そういえば昨日面白いことがあって、散歩してたら猫がついてきちゃったんですよ。",
    "それでは今日はこのへんで終わります、おつかれさまでした。",
]
SHORT = CORPUS[1]
LONG = CORPUS[9]


def median_ms(fn, arg, n=7):
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn(arg)
        ts.append((time.perf_counter() - t0) * 1000)
    return sorted(ts)[len(ts) // 2]


def main():
    import warnings
    warnings.filterwarnings("ignore")

    # ---- 現行: transformers/torch（アプリと同一経路） ----
    t0 = time.perf_counter()
    import translate as cur
    cur.load_translator()
    t_cur_load = time.perf_counter() - t0
    cur_fn = cur.translate

    # ---- CT2: 共通トークナイザ ----
    from transformers import MarianTokenizer
    tok = MarianTokenizer.from_pretrained("staka/fugumt-ja-en")

    import ctranslate2

    def make_ct2(path):
        tr = ctranslate2.Translator(path, device="cpu",
                                    inter_threads=1, intra_threads=4)
        def fn(text):
            src = tok.convert_ids_to_tokens(tok.encode(text))
            res = tr.translate_batch([src], beam_size=1, max_decoding_length=96)
            out = res[0].hypotheses[0]
            return tok.decode(tok.convert_tokens_to_ids(out),
                              skip_special_tokens=True).strip()
        return tr, fn

    t0 = time.perf_counter()
    _tr32, ct2_fp32 = make_ct2(os.path.join(BENCH, "fugumt_ct2_fp32"))
    ct2_fp32(SHORT)   # 初回JITぶんはロードに含める
    t_32_load = time.perf_counter() - t0

    t0 = time.perf_counter()
    _tr8, ct2_int8 = make_ct2(os.path.join(BENCH, "fugumt_ct2_int8"))
    ct2_int8(SHORT)
    t_8_load = time.perf_counter() - t0

    # ---- レイテンシ ----
    engines = [("transformers(現行)", cur_fn, t_cur_load),
               ("CT2 fp32", ct2_fp32, t_32_load),
               ("CT2 int8", ct2_int8, t_8_load)]
    print(f"{'エンジン':20} {'ロード':>7} {'短文':>8} {'長文':>8}")
    for name, fn, tload in engines:
        fn(SHORT)  # ウォームアップ
        s = median_ms(fn, SHORT)
        l = median_ms(fn, LONG)
        print(f"{name:20} {tload:6.1f}s {s:7.0f}ms {l:7.0f}ms")

    # ---- 訳文比較 ----
    print("\n=== 訳文の一致率（現行を基準） ===")
    base = [cur_fn(s) for s in CORPUS]
    for name, fn, _ in engines[1:]:
        outs = [fn(s) for s in CORPUS]
        same = sum(1 for a, b in zip(base, outs) if a == b)
        print(f"\n[{name}] 完全一致 {same}/{len(CORPUS)}")
        for src, a, b in zip(CORPUS, base, outs):
            if a != b:
                print(f"  JA : {src}")
                print(f"  現行: {a}")
                print(f"  CT2 : {b}")


if __name__ == "__main__":
    main()
