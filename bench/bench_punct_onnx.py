# -*- coding: utf-8 -*-
"""句読点BERT: torch(現行) vs ONNX Runtime の比較ベンチ（PoC・アプリ非組込）

測定: ロード時間 / 1行レイテンシ / 句読点判定の一致
実行: reazonspeech-env\Scripts\python.exe bench\bench_punct_onnx.py
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
BENCH = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(BENCH))

CORPUS = [
    "こんにちはこれはてすとです",
    "みなさんこんばんは今日も配信を始めます",
    "コメントありがとうめっちゃ嬉しいです",
    "ちょっと待ってね設定を確認します",
    "このボス強すぎませんかもう10回も負けてます",
    "明日は夜の9時から配信予定ですぜひ見に来てください",
    "今日は新しい機能のテストをしていきますみなさんのコメントもどんどん読んでいきますのでよろしくお願いします",
    "そういえば昨日面白いことがあって散歩してたら猫がついてきちゃったんですよ",
]
SHORT, LONG = CORPUS[0], CORPUS[6]
ONNX_PATH = os.path.join(BENCH, "punct_bert.onnx")


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
    import torch
    import numpy as np

    # ---- 現行: torch ----
    t0 = time.perf_counter()
    import punct
    punct.load_punctuator()
    t_cur_load = time.perf_counter() - t0

    # ---- ONNX へエクスポート（初回のみ） ----
    if not os.path.exists(ONNX_PATH):
        print("ONNXエクスポート中...")
        dummy_ids = torch.ones(1, 16, dtype=torch.long)
        dummy_mask = torch.ones(1, 16, dtype=torch.long)
        torch.onnx.export(
            punct._model, (dummy_ids, dummy_mask), ONNX_PATH,
            input_names=["input_ids", "attention_mask"],
            output_names=["logits"],
            dynamic_axes={"input_ids": {0: "b", 1: "s"},
                          "attention_mask": {0: "b", 1: "s"},
                          "logits": {0: "b", 1: "s"}},
            opset_version=17)
        print(f"  → {ONNX_PATH} ({os.path.getsize(ONNX_PATH)/1e6:.0f}MB)")

    # ---- ONNX Runtime 版 add_punctuation（punct.py と同じ判定ロジック） ----
    import onnxruntime as ort
    t0 = time.perf_counter()
    so = ort.SessionOptions()
    so.intra_op_num_threads = 4
    sess = ort.InferenceSession(ONNX_PATH, so, providers=["CPUExecutionProvider"])
    t_ort_load = time.perf_counter() - t0

    tok = punct._tokenizer

    def sigmoid(x):
        return 1.0 / (1.0 + np.exp(-x))

    def onnx_punct(text, comma=0.1, period=0.1):
        text = text.replace("、", "").replace("。", "")
        if not text:
            return text
        result = ""
        for i in range(0, len(text), 256):
            chunk = text[i:i + 256]
            inp = tok(" ".join(list(chunk)), padding="longest",
                      truncation=True, max_length=512, return_tensors="np")
            logits = sess.run(None, {"input_ids": inp["input_ids"].astype(np.int64),
                                     "attention_mask": inp["attention_mask"].astype(np.int64)})[0]
            probs = sigmoid(logits[0]).T
            import torch as _t
            result += punct._rebuild(_t.tensor(inp["input_ids"][0]),
                                     probs[0] > comma, probs[1] > period)
        return result

    # ---- レイテンシ ----
    for name, fn, tload in [("torch(現行)", punct.add_punctuation, t_cur_load),
                            ("ONNX Runtime", onnx_punct, t_ort_load)]:
        fn(SHORT)
        s = median_ms(fn, SHORT)
        l = median_ms(fn, LONG)
        print(f"{name:14} ロード {tload:4.1f}s   短文 {s:4.0f}ms   長文 {l:4.0f}ms")

    # ---- 判定一致 ----
    same = 0
    for s in CORPUS:
        a, b = punct.add_punctuation(s), onnx_punct(s)
        if a == b:
            same += 1
        else:
            print(f"  差分 JA : {s}\n    torch: {a}\n    onnx : {b}")
    print(f"\n句読点の完全一致: {same}/{len(CORPUS)}")


if __name__ == "__main__":
    main()
