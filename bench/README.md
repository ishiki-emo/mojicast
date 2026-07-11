# bench/ — ONNX / CTranslate2 移行のPoCベンチ（アプリ非組込）

将来の「torch/transformers 排除」移行の実測材料。アプリ本体には一切組み込んでいない。

## 準備（dev venv に追加が必要なもの・requirements には含めない）
```powershell
.\reazonspeech-env\Scripts\pip.exe install ctranslate2 onnxruntime onnx onnxscript
# FuguMT を CT2 形式へ変換
.\reazonspeech-env\Scripts\ct2-transformers-converter.exe --model staka/fugumt-ja-en --output_dir bench\fugumt_ct2_fp32 --force
.\reazonspeech-env\Scripts\ct2-transformers-converter.exe --model staka/fugumt-ja-en --output_dir bench\fugumt_ct2_int8 --quantization int8 --force
```

## 実行
```powershell
.\reazonspeech-env\Scripts\python.exe bench\bench_translate_ct2.py   # 翻訳: 現行 vs CT2
.\reazonspeech-env\Scripts\python.exe bench\bench_punct_onnx.py      # 句読点: 現行 vs ONNX（初回にエクスポート）
```

## 実測結果（2026-07-11・16C CPU / スレッド4）

翻訳（FuguMT）:
| エンジン | ロード | 短文 | 長文 | サイズ | 訳文 |
|---|---|---|---|---|---|
| transformers(現行) | 6.8s | 99ms | 166ms | 118MB | 基準 |
| CT2 fp32 | 0.3s | 52ms | 94ms | 117MB | **12/12 完全一致** |
| CT2 int8 | 0.1s | 20ms | 29ms | 60MB | 5/12 一致（差分は同格の言い換え） |

句読点（BERT）:
| エンジン | ロード | 短文 | 長文 | 判定 |
|---|---|---|---|---|
| torch(現行) | 4.9s | 22ms | 45ms | 基準 |
| ONNX Runtime | 0.7s | 9ms | 20ms | **8/8 完全一致** |

## 結論（移行時の推奨構成）
- 句読点: ONNX fp32（判定完全一致・2倍速・ロード7倍速）
- 翻訳: CT2 fp32 なら完全一致で2倍速 / int8 なら5倍速+60MBで言い換え許容
- torch/transformers を排除でき、配布サイズ・ファイル数・起動時間が大幅減
- 本移行時は実配信ログでの回帰diffを追加実施のこと
