# -*- coding: utf-8 -*-
"""韓国語訳: M2M直接(現行) vs ピボット ja→en→ko の比較ベンチ

アプリには組み込まない PoC。ja→ko の直接モデルは OPUS-MT に存在しないため
(en→ko はある)、既に高品質と実測できている FuguMT ja→en を1段目に流用し、
2段目に Helsinki-NLP/opus-mt-tc-big-en-ko を繋ぐ構成が成立するかを見る。

測定: 訳文の並置比較 / 1行レイテンシ(中央値) / ロード時間・モデルサイズ

実行: reazonspeech-env\Scripts\python.exe bench\bench_pivot_ko.py
前提: 初回は opus-mt-tc-big-en-ko を CT2(int8) へ自動変換する
      (torch/transformers/ct2-transformers-converter の入った dev venv が必要)
"""
import os
import shutil
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
BENCH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH)
sys.path.insert(0, ROOT)

import ctranslate2
import sentencepiece as spm
import translate as T

HF_EN_KO = "Helsinki-NLP/opus-mt-tc-big-en-ko"
EN_KO_DIR = os.path.join(BENCH, "opus_en_ko_ct2_int8")

# 実配信で崩れた実例（2026-08-22 の視聴者配信キャプチャ）。
# 現行M2Mが「別の話を捏造する」条件を意図的に含めてある:
#   - 言い直しの混入 / 句読点なしで複数文が連結
#   - 「〜ので」で終わる未完結節
#   - ユーザー辞書の固有名詞（M2Mでは消失した）
#   - 単語だけの相槌（現行は「알지 못」と途中で切れた）
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

_enko = None
_sp_enko_src = None
_sp_enko_tgt = None


def _sp_load(path):
    """非ASCIIパス対策で model_proto 渡し（translate.py と同じ理由）"""
    with open(path, "rb") as f:
        return spm.SentencePieceProcessor(model_proto=f.read())


def ensure_en_ko():
    """opus-mt-tc-big-en-ko → CT2(int8)。無ければ変換する（初回のみ・数分）"""
    if os.path.exists(os.path.join(EN_KO_DIR, "model.bin")):
        return
    print(f"en-ko: CTranslate2 (int8) へ変換中... ({HF_EN_KO})")
    conv = os.path.join(os.path.dirname(sys.executable),
                        "ct2-transformers-converter.exe")
    if not os.path.exists(conv):
        conv = "ct2-transformers-converter"
    subprocess.run([conv, "--model", HF_EN_KO, "--output_dir", EN_KO_DIR,
                    "--quantization", "int8", "--force"], check=True)
    # SentencePiece も同じフォルダへ（このフォルダだけで自己完結させる）
    import huggingface_hub as hf
    for f in ("source.spm", "target.spm"):
        dst = os.path.join(EN_KO_DIR, f)
        if not os.path.exists(dst):
            shutil.copyfile(hf.hf_hub_download(HF_EN_KO, f), dst)
    print(f"en-ko: → {EN_KO_DIR}")


def load_en_ko(num_threads: int = 4):
    global _enko, _sp_enko_src, _sp_enko_tgt
    if _enko is not None:
        return
    ensure_en_ko()
    _sp_enko_src = _sp_load(os.path.join(EN_KO_DIR, "source.spm"))
    _sp_enko_tgt = _sp_load(os.path.join(EN_KO_DIR, "target.spm"))
    _enko = ctranslate2.Translator(EN_KO_DIR, device="cpu",
                                   inter_threads=1, intra_threads=num_threads)


def translate_en_ko(text: str, max_new_tokens: int = 96) -> str:
    if not text or not text.strip():
        return ""
    if _enko is None:
        load_en_ko()
    tokens = _sp_enko_src.encode(text, out_type=str)[:510] + ["</s>"]
    res = _enko.translate_batch([tokens], beam_size=1,
                                repetition_penalty=1.2,
                                no_repeat_ngram_size=3,
                                max_decoding_length=max_new_tokens)
    out = [t for t in res[0].hypotheses[0]
           if not (t.startswith("__") and t.endswith("__"))
           and t not in ("</s>", "<pad>", "<unk>")]
    return _sp_enko_tgt.decode(out).strip()


def pivot_ko(text: str) -> tuple[str, str]:
    """ja → en(FuguMT) → ko(opus-mt)。中間の英語も返す（誤差の出所を見るため）"""
    en = T.translate(text)
    if not en.strip():
        return "", ""
    return en, translate_en_ko(en)


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
    print("韓国語訳 比較ベンチ: M2M直接(現行) vs ピボット ja→en→ko")
    print("=" * 78)

    t0 = time.perf_counter()
    T.load_translator_zh()
    load_m2m_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    T.load_translator()
    load_fugu_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    load_en_ko()
    load_enko_s = time.perf_counter() - t0
    print(f"\nロード: M2M {load_m2m_s:.1f}s / FuguMT {load_fugu_s:.1f}s / "
          f"en-ko {load_enko_s:.1f}s")
    print(f"サイズ: M2M {dir_size_mb(T._resolve_dir_m2m(download=False)):.0f}MB"
          f" / FuguMT {dir_size_mb(T._resolve_dir(download=False)):.0f}MB"
          f" / en-ko {dir_size_mb(EN_KO_DIR):.0f}MB")

    for title, corpus in (("実配信で崩れた実例", CORPUS_LIVE),
                          ("対照群（句読点あり・完結文）", CORPUS_CLEAN)):
        print("\n" + "=" * 78)
        print(f"■ {title}")
        print("=" * 78)
        for src in corpus:
            direct = T.translate_m2m(src, "ja", "ko")
            en, pivot = pivot_ko(src)
            print(f"\n  原文  : {src}")
            print(f"  直接  : {direct}")
            print(f"  中間en: {en}")
            print(f"  ピボ  : {pivot}")

    print("\n" + "=" * 78)
    print("■ レイテンシ（中央値・n=5）")
    print("=" * 78)
    for label, src in (("短文", CORPUS_CLEAN[1]), ("長文", CORPUS_CLEAN[4])):
        d = median_ms(lambda s: T.translate_m2m(s, "ja", "ko"), src)
        p = median_ms(lambda s: pivot_ko(s), src)
        print(f"  {label}: 直接 {d:6.1f}ms / ピボット {p:6.1f}ms "
              f"(×{p / d:.2f})  «{src[:24]}»")


if __name__ == "__main__":
    main()
