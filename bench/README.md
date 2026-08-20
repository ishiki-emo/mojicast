# bench/ — ONNX / CTranslate2 移行のPoCベンチ

「torch/transformers 排除」移行の実測材料。**移行は2026-07-13に本実装済み**
（punct.py=ONNX / translate.py=CT2）。旧torch実装は `legacy_punct.py` / `legacy_translate.py`
として保存してあり、`regression_diff.py` が実配信ログで新旧を比較する。

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

---

# メモリ実測（ユースケース別）

## ツール
```powershell
# ① 認識プロセス側（モデルのメモリ）: ケースごとに別プロセスで実測
.\reazonspeech-env\Scripts\python.exe bench\bench_memory.py --wav 20260708.wav
.\reazonspeech-env\Scripts\python.exe bench\bench_memory.py --cases standard,standard+en

# ② アプリ全体（本体＋GUI窓のWebView2）: 起動中のプロセスツリーをサンプリング
.\bench\mem_watch.ps1 -Seconds 60 -Csv mem.csv
```
`bench_memory.py` はコンポーネント（VAD / 認識 / 句読点 / 翻訳 / 音イベント）を
1つずつ足しながら RSS を測る。`--cases` の候補はスクリプト冒頭の `CASES` を参照。

## 実測結果（2026-08-19・Windows 11 / 16C・int8-fp32既定・推論3周後の常駐RSS）

| ユースケース | 構成 | 常駐RSS | コミット |
|---|---|---|---|
| 素の起動（モデル無し） | — | **30MB** | 501MB |
| 標準（日本語字幕） | VAD+k2+句読点 | **741MB** | 1206MB |
| 標準（句読点int8・現行既定） | 同上 | **507MB** | 972MB |
| 標準＋英訳（句読点int8） | +FuguMT | **925MB** | 1389MB |
| 標準＋英訳 | +FuguMT | **1167MB** | 1632MB |
| 標準＋中国語訳 | +M2M100 | **1432MB** | 1902MB |
| 標準＋音イベント演出 | +AudioTagging | **856MB** | 1328MB |
| コラボ（2話者） | 標準＋VAD2本目 | **748MB** | 1214MB |
| 多言語（SenseVoice） | VAD+SenseVoice | **383MB** | 850MB |
| 多言語＋中国語訳 | +M2M100 | **1065MB** | 1537MB |
| 全部盛り | 全機能同時 | **1801MB** | 2274MB |

コンポーネント単体（起動直後からの増分）:

| 部品 | 増分RSS | 備考 |
|---|---|---|
| VAD（silero） | 47MB | 話者ごとに1本。2本目は +9MB のみ |
| 認識 k2 int8-fp32（既定） | 285MB | |
| 認識 k2 int8 | 276MB | |
| 認識 k2 fp32 | 696MB | 既定比 +410MB |
| 認識 SenseVoice | 344MB | 句読点BERT不要なので実質は最軽量構成 |
| 句読点BERT（ONNX int8・既定） | 154MB | 重み104MB。2026-08-20に既定化 |
| 句読点BERT（ONNX fp32・高精度） | 390MB | 重み346MB。単体で最大の常駐 |
| 翻訳 FuguMT（CT2） | 460MB | |
| 翻訳 M2M100（CT2） | 727MB | |
| 音イベント検出 | 122MB（推論後183MB） | |

読み方・注意:
- **常駐RSS** が実際に物理メモリを占める量。**コミット** は ONNX Runtime /
  CTranslate2 が予約する仮想メモリ込みで、素の起動でも 501MB 出る（実使用ではない）。
- GUI窓（WebView2）は別プロセスのため `bench_memory.py` には含まれない。
  利用者のタスクマネージャに出る値は `mem_watch.ps1` の合計を見ること。
- 内訳は「その順に足した増分」なので、単体測定値との差はアロケータの再利用ぶん。

## 句読点BERT の int8 量子化（2026-08-20 実施）

`tools/convert_models.py` の `quantize_punct()`（onnxruntime の動的量子化。fp32 の
.onnx さえあれば torch は不要）。**per-channel が必須**で、per-tensor だと判定一致率が
半減する。実配信ログ948行で fp32 と比較した結果:

| | fp32（高精度モード） | int8 per-tensor | **int8 per-channel（採用）** |
|---|---|---|---|
| ファイル | 364MB | 109MB | **109MB** |
| 常駐RSS 増分 | +390MB | +152MB | **+154MB** |
| 1行あたり | 約11ms | 4.5ms | **約5ms** |
| ロード | 0.4s | 0.2s | **0.2s** |
| fp32との完全一致 | — | 50.3% | **89.5%** |

不一致100件の内訳は 末尾の「。」が消える69 / 文中の句読点が減る25 / 位置ずれ4 /
増える2 で、**本文が変わるものは0件**。int8 は確率が一律に下がる方向へずれるため
閾値を下げると一致率は戻るが、0.06 で 91.6% が頭打ちのうえ `2008年`→`2、0、08年` 型の
数字誤爆が増えるので、閾値は既定の 0.1 のままとした。

設定の「高精度モード」（`precision`）が認識と句読点をまとめて切り替える。

## 効きそうな削減案（未実施）
- 標準構成で fp32 認識を選んでいる利用者は int8-fp32 へ誘導（-410MB・精度差ほぼ無し）。
