# -*- coding: utf-8 -*-
"""自前ビルドの ctranslate2 を読み込めるか確かめる（build.ps1 の「確認」から呼ぶ）

環境変数 CT2_PROBE_MODEL に変換済みモデルのフォルダがあれば、読み込んで1文訳す。
CT2_VERBOSE=1 と合わせると、使われた行列演算の実装（"Use Intel MKL: false" 等）が stderr に出る。
"""
import os
import sys

import ctranslate2

pkg = os.path.dirname(ctranslate2.__file__)
print("version", ctranslate2.__version__)
print("cpu", sorted(ctranslate2.get_supported_compute_types("cpu")))
print("files", sorted(os.listdir(pkg)))

model = os.environ.get("CT2_PROBE_MODEL")
if model:
    for ct in ("int8", "float32"):
        tr = ctranslate2.Translator(model, device="cpu", compute_type=ct, intra_threads=2)
        res = tr.translate_batch([["▁", "</s>"]], max_decoding_length=4)
        print("translate", ct, res[0].hypotheses[0])
        del tr
sys.stdout.flush()
