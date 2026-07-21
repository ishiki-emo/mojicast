# -*- coding: utf-8 -*-
"""推論モデルの変換ツール（開発マシン専用・配布物には含めない）

句読点BERT → ONNX / FuguMT → CTranslate2 に変換し、models_conv/ へ出力する。
出力一式を Hugging Face のモデルリポジトリへアップロードすると、
アプリ（punct.py / translate.py）がそこからダウンロードして使う。

実行（torch/transformers が入っている dev venv で）:
    .\reazonspeech-env\Scripts\python.exe tools\convert_models.py

出力:
    models_conv/punct/punct_bert.onnx        句読点BERT（fp32・単一ファイル）
    models_conv/punct/vocab.txt              文字語彙（tohoku-nlp BERT char v3）
    models_conv/fugumt-ja-en-ct2/            FuguMT CT2版（fp32）
    models_conv/fugumt-ja-en-ct2/source.spm  SentencePieceモデル（同梱で自己完結）
    models_conv/fugumt-ja-en-ct2/target.spm
"""
import os
import shutil
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "models_conv")


def convert_punct():
    out_dir = os.path.join(OUT, "punct")
    out_path = os.path.join(out_dir, "punct_bert.onnx")
    os.makedirs(out_dir, exist_ok=True)

    import huggingface_hub as hf
    vocab_src = hf.hf_hub_download("tohoku-nlp/bert-base-japanese-char-v3",
                                   "vocab.txt")
    shutil.copyfile(vocab_src, os.path.join(out_dir, "vocab.txt"))

    if os.path.exists(out_path):
        print(f"punct: スキップ（既存: {out_path}）")
        return
    print("punct: torch モデルをロードして ONNX エクスポート中...")
    import torch
    import onnx
    # 旧torch実装（bench/legacy_punct.py に保存した現行実装）からモデルを構築
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "legacy_punct", os.path.join(ROOT, "bench", "legacy_punct.py"))
    legacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy)
    legacy.load_punctuator()

    tmp_dir = os.path.join(OUT, "_punct_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, "model.onnx")
    dummy_ids = torch.ones(1, 16, dtype=torch.long)
    dummy_mask = torch.ones(1, 16, dtype=torch.long)
    torch.onnx.export(
        legacy._model, (dummy_ids, dummy_mask), tmp_path,
        input_names=["input_ids", "attention_mask"],
        output_names=["logits"],
        dynamic_axes={"input_ids": {0: "b", 1: "s"},
                      "attention_mask": {0: "b", 1: "s"},
                      "logits": {0: "b", 1: "s"}},
        opset_version=17)
    # 外部データを単一ファイルへまとめ直す（配布・DLを1ファイルで済ませる）
    m = onnx.load(tmp_path)
    onnx.save_model(m, out_path, save_as_external_data=False)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f"punct: → {out_path} ({os.path.getsize(out_path) / 1e6:.0f}MB)")


def convert_fugumt():
    out_dir = os.path.join(OUT, "fugumt-ja-en-ct2")
    if os.path.exists(os.path.join(out_dir, "model.bin")):
        print(f"fugumt: スキップ（既存: {out_dir}）")
    else:
        print("fugumt: CTranslate2 (fp32) へ変換中...")
        conv = os.path.join(os.path.dirname(sys.executable),
                            "ct2-transformers-converter.exe")
        subprocess.run([conv, "--model", "staka/fugumt-ja-en",
                        "--output_dir", out_dir, "--force"], check=True)
        print(f"fugumt: → {out_dir}")
    # SentencePiece モデルも同じフォルダへ（アプリはこのフォルダだけで自己完結）
    import huggingface_hub as hf
    for f in ("source.spm", "target.spm"):
        dst = os.path.join(out_dir, f)
        if not os.path.exists(dst):
            shutil.copyfile(hf.hf_hub_download("staka/fugumt-ja-en", f), dst)


def convert_m2m100():
    """M2M-100 418M → CT2 (int8)。多言語翻訳（ja→zh 等）用。
    FuguMTと違い418Mと大きいため int8 量子化で 1.9GB → 約450MB に落とす。"""
    out_dir = os.path.join(OUT, "m2m100-418m-ct2")
    if os.path.exists(os.path.join(out_dir, "model.bin")):
        print(f"m2m100: スキップ（既存: {out_dir}）")
    else:
        print("m2m100: CTranslate2 (int8) へ変換中...")
        conv = os.path.join(os.path.dirname(sys.executable),
                            "ct2-transformers-converter.exe")
        subprocess.run([conv, "--model", "facebook/m2m100_418M",
                        "--output_dir", out_dir,
                        "--quantization", "int8", "--force"], check=True)
        print(f"m2m100: → {out_dir}")
    # SentencePiece モデルも同じフォルダへ（アプリはこのフォルダだけで自己完結）
    import huggingface_hub as hf
    dst = os.path.join(out_dir, "sentencepiece.model")
    if not os.path.exists(dst):
        shutil.copyfile(
            hf.hf_hub_download("facebook/m2m100_418M",
                               "sentencepiece.bpe.model"), dst)


def main():
    os.makedirs(OUT, exist_ok=True)
    convert_punct()
    convert_fugumt()
    convert_m2m100()
    total = 0
    print("\n=== 出力一覧 ===")
    for root, _dirs, files in os.walk(OUT):
        for f in files:
            p = os.path.join(root, f)
            sz = os.path.getsize(p)
            total += sz
            print(f"  {os.path.relpath(p, OUT):40} {sz / 1e6:8.1f}MB")
    print(f"  合計 {total / 1e6:.0f}MB")
    print("\nこのフォルダ一式を HF リポジトリへアップロードしてください"
          "（punct.py / translate.py の _REPO_ID 参照）。")


if __name__ == "__main__":
    main()
