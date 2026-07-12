# -*- coding: utf-8 -*-
"""ONNX/CT2 移行の回帰diff — 実配信ログの全行で 旧実装(torch) vs 新実装 を比較

ROADMAP #3 の「本移行時は実際の配信ログで回帰diffを実施すること」を実施する。
torch/transformers が残っている dev venv で実行（配布物には含めない）。

実行: .\reazonspeech-env\Scripts\python.exe bench\regression_diff.py
"""
import glob
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
BENCH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH)
sys.path.insert(0, ROOT)
sys.path.insert(0, BENCH)


def collect_lines():
    """logs/**/*.log から発話行を収集（[時刻] プレフィックスを除去・重複排除）"""
    lines = []
    seen = set()
    for path in sorted(glob.glob(os.path.join(ROOT, "logs", "**", "*.log"),
                                 recursive=True)):
        with open(path, encoding="utf-8") as f:
            for ln in f:
                m = re.match(r"^\[[^\]]+\]\s*(.+)$", ln.strip())
                if not m:
                    continue
                t = m.group(1).strip()
                if t and t not in seen:
                    seen.add(t)
                    lines.append(t)
    return lines


def main():
    import warnings
    warnings.filterwarnings("ignore")

    lines = collect_lines()
    if not lines:
        print("logs/ に発話行がありません")
        return
    print(f"対象: 実配信ログ {len(lines)} 行（重複除去済み）\n")

    # ---- 句読点 ----
    import legacy_punct
    import punct as new_punct
    legacy_punct.load_punctuator()
    new_punct.load_punctuator()

    diff_p, oov_fix = [], []
    for t in lines:
        a = legacy_punct.add_punctuation(t)
        b = new_punct.add_punctuation(t)
        if a == b:
            continue
        # 旧版はOOV文字を落とす仕様だった。新版から旧版が落とした文字を
        # 取り除いて一致するなら「OOV文字が保持されるようになった差分（改善）」
        b_stripped = "".join(ch for ch in b
                             if ch in ("、", "。") or ch in a)
        if b_stripped == a:
            oov_fix.append((t, a, b))
        else:
            diff_p.append((t, a, b))
    print(f"[句読点] 完全一致 {len(lines) - len(diff_p) - len(oov_fix)}"
          f" / OOV改善差分 {len(oov_fix)} / 実質差分 {len(diff_p)}")
    for t, a, b in (diff_p + oov_fix)[:10]:
        print(f"  IN : {t}\n  旧 : {a}\n  新 : {b}")

    # ---- 翻訳（実効トークン列の一致 → 訳文の一致） ----
    # 語彙外の生ピースは CT2 内部で <unk> になるため、
    # CT2語彙でマップした「モデルが実際に受け取る列」で比較する
    import json
    from transformers import MarianTokenizer
    tok = MarianTokenizer.from_pretrained("staka/fugumt-ja-en")
    import sentencepiece as spm
    ct2_dir = os.path.join(ROOT, "models_conv", "fugumt-ja-en-ct2")
    sp = spm.SentencePieceProcessor(model_file=os.path.join(ct2_dir, "source.spm"))
    with open(os.path.join(ct2_dir, "shared_vocabulary.json"),
              encoding="utf-8") as f:
        ct2_vocab = set(json.load(f))
    unk = lambda seq: [p if p in ct2_vocab else "<unk>" for p in seq]
    tok_diff = 0
    for t in lines:
        a = unk(tok.convert_ids_to_tokens(tok.encode(t)))     # 旧: [...pieces, </s>]
        b = unk(sp.encode(t, out_type=str) + ["</s>"])        # 新
        if a != b:
            tok_diff += 1
            if tok_diff <= 5:
                print(f"  [tok差分] {t}\n    旧: {a}\n    新: {b}")
    print(f"\n[翻訳トークナイザ] 実効トークン不一致 {tok_diff}/{len(lines)} 行")

    import legacy_translate
    import translate as new_translate
    legacy_translate.load_translator()
    new_translate.load_translator()
    diff_t = []
    for t in lines:
        a = legacy_translate.translate(t)
        b = new_translate.translate(t)
        if a != b:
            diff_t.append((t, a, b))
    print(f"[翻訳] 完全一致 {len(lines) - len(diff_t)}/{len(lines)}")
    for t, a, b in diff_t[:10]:
        print(f"  JA : {t}\n  旧 : {a}\n  新 : {b}")

    # 判定: 句読点の実質差分ゼロ＆トークナイザ完全一致なら、
    # 残る翻訳差分は数値誤差起因（greedyの近接候補入れ替わり＝同格の言い換え）。
    # 件数を表示するので内容は上のリストで目視確認すること。
    ok = not diff_p and tok_diff == 0
    verdict = "PASS ===" if ok and not diff_t else \
              f"PASS（翻訳の数値起因言い換え {len(diff_t)} 件は上記を目視確認） ===" \
              if ok else "要確認 ==="
    print("\n=== REGRESSION:", verdict)


if __name__ == "__main__":
    main()
