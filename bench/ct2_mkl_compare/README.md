# Mojicast 翻訳エンジン計測ツール

Mojicast の翻訳エンジンから、ライセンスの都合で外したい部品（Intel MKL・Intel OpenMP）があります。
外した版（自前ビルド）に切り替えても翻訳が遅くならないか・重くならないかを、いろいろなパソコンで
確かめるためのツールです。

- Mojicast 本体の設定やファイルには一切触りません
- インターネットには、翻訳モデルのダウンロード（初回だけ・Hugging Face）以外では接続しません
- 中に入っている文（`sentences.txt`）は、癒色えもの配信の書き起こしです

## やってほしいこと

1. Zip を好きな場所に展開する
2. Mojicast・ゲーム・配信ソフトなど、重いアプリを閉じる
3. `ct2_mkl_compare.exe` をダブルクリック
   - 「Windows によって PC が保護されました」と出たら「詳細情報」→「実行」
   - インストール版の Mojicast があれば、そのモデルを使います。無ければ初回に約 0.7GB ダウンロードします
4. 30〜90 分ほど待つ（遅いパソコンほど長くかかります。途中で窓を閉じないでください）
5. 終わると同じフォルダに `result_日時.txt` と `result_日時.json` ができるので、この 2 つを送ってください

### 結果ファイルに入るもの

CPU の名前、Windows のバージョン、翻訳にかかった時間、訳文、メモリ使用量、CPU 使用時間、
読み込まれた翻訳エンジンの部品（DLL）の名前。
ユーザー名やファイルの場所など、個人を特定する情報は入りません（部品の場所は、このツールの
フォルダからの相対で記録します）。

### Zip 版の Mojicast のモデルを使いたいとき

コマンドプロンプトで、Mojicast のフォルダの `models` を指定して実行します。

```
ct2_mkl_compare.exe --models "C:\...\Mojicast\models"
```

---

## 開発メモ

申請（SignPath Foundation）で「MKL を外した根拠」として示す計測。結果は [results/](results/)。

### 何と何を比べるか

| variant | 中身 | 意味 |
|---|---|---|
| `mkl` | 公式 wheel・`CT2_USE_MKL=1` | Intel 機の現行の動き（MKL＋Intel OpenMP） |
| `dnnl` | 公式 wheel・`CT2_USE_MKL=0` | MKL を外した場合（oneDNN＋Intel OpenMP）。AMD 機の現行の動き |
| `custom_passive` | 自前ビルド（[tools/signing/ct2_nomkl](../../tools/signing/ct2_nomkl/build.ps1)）＋`OMP_WAIT_POLICY=PASSIVE` | 採用候補の形（oneDNN＋MSVC OpenMP・計算後に眠る） |
| `custom` | 自前ビルド・`OMP_WAIT_POLICY` 指定なし | vcomp 既定（計算後に空回り）。PASSIVE の効果を見るための比較用 |
| `llvm` | 自前ビルド（`build.ps1 -OpenMP llvm`）・`OMP_WAIT_POLICY` 指定なし | cl `/openmp:llvm` ＋ 上流 LLVM libomp（Apache-2.0）。Intel OpenMP と同じ `KMP_BLOCKTIME=5` が効く。2026-11-11 に3台で計測済み（dnnl と同じ速さ・CT2_NOMKL_BUILD.md 9.4） |

- 既定: Intel 機は `mkl,dnnl[,custom_passive,custom]`、それ以外は `dnnl[,custom_passive,custom]`。
  上流 libomp 版もあるときは `mkl,dnnl,llvm,custom_passive`（AMD は `dnnl,llvm,custom_passive`）。
  vcomp の空回りあり（custom）は 2026-11-10 に3台で測り済みなので省き、1回の計測時間を前回と同じに保つ
  （MKL は AMD では元から使われず、強制すると極端に遅い）。`--variants dnnl,custom_passive` のように選べる。
  先頭が比較の基準
- **スレッド数の比較**: 本計測のあと、基準と自前ビルド2種を `intra_threads` 1 / 2 で1回ずつ
  （先頭50文 `--sweep-sentences`・CPU/文は先頭10文 `--sweep-cpu-sentences`）測り、4 スレッドの本計測と
  並べる。時間・CPU/文・訳文の一致（同じ variant の 4 スレッドと / 基準の 4 スレッドと）を出す。
  `--sweep-sentences 0` で省く
- 自前ビルドは `--custom <wheel かフォルダ>`（vcomp 版）／`--custom-llvm <同>`（上流 libomp 版）、
  または exe（compare.py）の隣の `ct2_custom\`／`ct2_custom_llvm\`。
  PyInstaller 同梱の ctranslate2 より優先して読むよう `sys.meta_path` に差し込む（読み込まれた場所を
  子プロセスが確かめ、違えば失敗にする）
- 各 variant を往復の順で2回ずつ別プロセスで走らせ、1文ごとに速い方を採る
- 結果に残すもの: 使われた GEMM 実装（`CT2_VERBOSE=1` のログ）、ctranslate2 の版と DLL の SHA256、
  プロセスに読み込まれた OpenMP の DLL（フルパス）、自前ビルドの `build-info.json`（CMake オプション、
  コンパイラ、vcomp140 の版とハッシュ）、子プロセスで効いていた `OMP_WAIT_POLICY`
- **CPU/文**: 先頭20文（`--cpu-sentences`）で、1文訳して1秒空けるまでのプロセス CPU 時間。
  Windows では CPU サイクル数（`QueryProcessCycleTime`）を校正した周波数で割って求める（`os.times` は 15.6ms 刻みで
  数 ms の計算を正しく数えられないため・2026-11-10 から）。それより前の結果（results/ の 2026-11-10_*）の
  CPU/文 は os.times によるもので参考値（docs/signing/CT2_NOMKL_BUILD.md 9.3）
  OpenMP のスレッドが計算後に空回りすると、速さは変わらずここだけ増える。Mojicast は Intel の
  OpenMP に `KMP_BLOCKTIME=5` を設定している（translate.py）。MSVC の OpenMP（vcomp140）にはこの設定が
  無く、開発機で英訳の CPU/文 が 94ms → 396ms に増える。`OMP_WAIT_POLICY=PASSIVE` で 86ms に戻るが、
  1文が 26 → 58ms に延びる（docs/signing/CT2_NOMKL_BUILD.md 2.1）。この兼ね合いを custom / custom_passive と
  スレッド数の比較で見る
- 訳文: M2M（中韓）は数値計算の順序が変わると数 % 変わることがある。2026-11-10 の Intel 2台では
  MKL あり/なしで 4ケースとも 200/200 一致

### 作り方・動かし方

- Zip: `powershell -ExecutionPolicy Bypass -File bench\ct2_mkl_compare\build.ps1 -CustomWheel <vcomp 版 whl> -CustomLlvmWheel <llvm 版 whl>`
  （`dist\ct2_mkl_compare-win-x64.zip` ができる。省いた方の自前ビルドは測らない。wheel の取り違えは同梱時に止める）
- 開発実行: `reazonspeech-env\Scripts\python.exe bench\ct2_mkl_compare\compare.py --limit 6 --custom <whl>`
  （開発用の環境には torch が入っていて、torch 由来の libiomp5md.dll も読み込まれる。結果の
  「Intel の DLL」欄はその分も出るので、パスで見分ける。配布版には torch は入らない）
- `results/` の命名: `日付_CPU_内容.{txt,json}`
