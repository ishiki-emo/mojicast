# CTranslate2 の MKL なしビルド（Windows）

翻訳エンジン CTranslate2 の公式 wheel（PyPI の `ctranslate2==4.8.1`）は、Windows 版に
**Intel oneMKL**（`ctranslate2.dll` に静的リンク）と **Intel OpenMP**（`libiomp5md.dll`）を含む。
どちらも Intel Simplified Software License（OSI 非承認）で、SignPath Foundation の署名条件
（同梱物がすべて OSI 承認ライセンス）を満たさない。そこで CTranslate2 を同じ v4.8.1 のソースから
MKL なしでビルドし、Windows 版の配布物ではそれを使う。

- ビルドスクリプト: [tools/signing/ct2_nomkl/build.ps1](../../tools/signing/ct2_nomkl/build.ps1)（ローカル・CI 共通）
- CI: [release-build-windows.yml](../../.github/workflows/release-build-windows.yml)（同じジョブ内でビルドして差し替え）、
  [build-ctranslate2-windows.yml](../../.github/workflows/build-ctranslate2-windows.yml)（wheel だけ作る）
- 計測: [bench/ct2_mkl_compare](../../bench/ct2_mkl_compare/README.md)（MKL を外した根拠・切り替え後の確認）
- macOS 版は対象外（公式 wheel が MKL を含まず Apple Accelerate を使うため。そのまま）

## 1. 何が変わるか

| | 公式 wheel | 自前ビルド（`4.8.1+nomkl`） |
|---|---|---|
| CTranslate2 のソース | v4.8.1 | 同じ v4.8.1（コミット `0d8bcd36`。手を加えない） |
| 行列演算 | Intel CPU: oneMKL / それ以外: oneDNN | **全 CPU で oneDNN 3.1.1**（公式と同じ版・同じオプション・静的リンク） |
| OpenMP ランタイム | Intel OpenMP（libiomp5md.dll） | **LLVM OpenMP 19.1.7（libomp.dll）**。ソースからビルド |
| コンパイラ | MSVC（cl） | clang-cl（Visual Studio 同梱の LLVM）。FMA の自動合成は切る |
| CPU 自動判別（ISA dispatch） | 有効 | 有効（同じ） |
| CUDA / cuDNN | 入っている（Mojicast は使わない・spec で除外） | 入れない |
| wheel の版 | 4.8.1 | 4.8.1+nomkl（`==4.8.1` を満たす） |
| ライセンス文 | — | wheel の dist-info\licenses に CTranslate2・oneDNN・LLVM OpenMP を同梱 |

AMD 機は公式 wheel でも MKL を使わず oneDNN で動いている（`Use Intel MKL: false`）。
MKL を外して計算方法が変わるのは Intel 機だけ。

### 1.1 CMake オプション

CTranslate2:

```
-DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5
-DWITH_MKL=OFF -DWITH_DNNL=ON -DOPENMP_RUNTIME=COMP -DENABLE_CPU_DISPATCH=ON
-DWITH_CUDA=OFF -DWITH_CUDNN=OFF -DWITH_RUY=OFF -DBUILD_CLI=OFF -DBUILD_TESTS=OFF
-DCMAKE_SHARED_LINKER_FLAGS="/LIBPATH:<libomp>\lib libomp.lib"
```

oneDNN 3.1.1（公式 wheel の `python/tools/prepare_build_environment_windows.sh` と同じ）:

```
-DONEDNN_LIBRARY_TYPE=STATIC -DONEDNN_CPU_RUNTIME=OMP -DONEDNN_GPU_RUNTIME=NONE
-DONEDNN_BUILD_EXAMPLES=OFF -DONEDNN_BUILD_TESTS=OFF -DONEDNN_ENABLE_WORKLOAD=INFERENCE
-DONEDNN_ENABLE_PRIMITIVE=CONVOLUTION;REORDER -DONEDNN_BUILD_GRAPH=OFF
```

LLVM OpenMP 19.1.7（`openmp-19.1.7.src.tar.xz` ＋ `cmake-19.1.7.src.tar.xz`。SHA256 は build.ps1 に固定）:

```
-DLIBOMP_ENABLE_SHARED=ON -DLIBOMP_USE_ITT_NOTIFY=OFF -DLIBOMP_OMPT_SUPPORT=OFF -DLIBOMP_OMPD_SUPPORT=OFF
-DOPENMP_ENABLE_LIBOMPTARGET=OFF -DOPENMP_ENABLE_OMPT_TOOLS=OFF -DLIBOMP_INSTALL_ALIASES=OFF
```

共通: Ninja ＋ clang-cl、`CFLAGS`/`CXXFLAGS` に `/clang:-ffp-contract=off`。
実際の値は wheel と一緒に出る `build-info.json` に残る（比較ツールもこれを結果に写す）。

## 2. OpenMP ランタイムの選定

CTranslate2 の `OPENMP_RUNTIME` は `INTEL`（libiomp5md・非 OSI）/ `COMP`（コンパイラ付属）/ `NONE`
（並列化なし・遅すぎる）。`COMP` の中身はコンパイラで決まる。

| 候補 | ライセンス | 結果 |
|---|---|---|
| MSVC の vcomp140.dll（cl ＋ `/openmp`） | VC++ 再頒布パッケージ（非 OSI・System Library 扱いの余地あり） | **不採用**。計算後の空回りを止める設定が無い |
| **LLVM の libomp.dll（clang-cl ＋ `/openmp`）** | **Apache-2.0 WITH LLVM-exception（OSI）** | **採用** |
| MSVC の `/openmp:llvm`（libomp140.x86_64.dll） | VS の `debug_nonredist` にしか無い（再配布不可） | 対象外 |

Mojicast は `KMP_BLOCKTIME=5` で、計算後に OpenMP のスレッドが空回りする時間を 200ms → 5ms に
縮めている（translate.py。配信中の CPU 負荷を英訳で 86% 減らした設定）。libomp は Intel OpenMP が
オープンソース化されたもので、この設定がそのまま効く。vcomp140 には相当する設定が無い。

開発機（Ryzen 7 9800X3D・4スレッド・1文訳して1秒空けるまでの CPU 時間・6文×2）:

| | 英訳 int8 | 中国語訳 |
|---|---|---|
| 公式 wheel（Intel OpenMP・KMP_BLOCKTIME=5） | 108ms | 712ms |
| 自前・vcomp140 | 400ms（3.7倍） | 988ms（1.4倍） |
| 自前・vcomp140 ＋ `OMP_WAIT_POLICY=PASSIVE` | 96ms | 608ms（ただし英訳の時間が 22→54ms に悪化） |
| **自前・libomp（KMP_BLOCKTIME=5）** | **98ms** | **693ms** |

`build.ps1 -OpenMP msvc` で vcomp140 版も作れる（比較用。配布には使わない）。

### 2.1 libomp を cl のまま使えない理由

cl の `/openmp` が出すコードは vcomp の入口（`_vcomp_fork` など）を呼ぶ。Intel の libiomp5md は
この入口も持っているので公式 wheel は cl ＋ libiomp5md で作れているが、LLVM の libomp は持っていない
（`__kmpc_*` だけ）。そのため CTranslate2 と oneDNN を clang-cl でコンパイルする。

### 2.2 FMA の自動合成を切る理由

clang は既定で `a*b+c` を FMA 命令にまとめる（`-ffp-contract=on`）。MSVC はまとめない。丸めが1段
変わるので、開発機の10文で英訳 int8・中・韓の訳文が各1文変わった（公式 wheel と比べて）。
`-ffp-contract=off` で公式と同じ計算順にそろえる。

## 3. ビルド手順

必要なもの: Visual Studio 2022（「C++ によるデスクトップ開発」＋「C++ Clang ツール」）、
CMake 3.18 以上（4.x 可）、git、Python 3.11 x64。GitHub Actions の `windows-2022` には全部入っている。

```powershell
# Python は配布物と同じ 3.11.15（uv の場合）
$py = (uv python find 3.11.15).Trim()
powershell -ExecutionPolicy Bypass -File tools\signing\ct2_nomkl\build.ps1 -Python $py `
    -WorkDir E:\ct2-build -OutDir E:\ct2-dist `
    -ProbeModel models_conv\fugumt-ja-en-ct2     # 任意: モデルを読んで MKL 不使用をログで確かめる
```

段階: ソース取得（CTranslate2 は git の tag ＋コミット ID を照合、oneDNN と LLVM は tar の SHA256 を照合）
→ libomp → oneDNN → CTranslate2 → wheel → 確認。

**確認で落とすもの**（どれかに当たるとスクリプトが失敗する）:

- `ctranslate2.dll` が `libiomp5md` / `mkl` に依存している、または libomp.dll に依存していない、vcomp にも依存している
- `ctranslate2.dll` に MKL のシンボル文字列が残っている
- wheel の中に libiomp5md / cudnn / mkl がある、OpenMP の DLL が想定と違う
- wheel にライセンス文（CTranslate2・oneDNN・LLVM OpenMP）が無い
- 素の venv に入れて import・`get_supported_compute_types` が通らない
- `-ProbeModel` 指定時: int8 と float32 で読み込み・1文訳せない、`Use Intel MKL: false` が出ない

### 3.1 所要時間

| 環境 | ソース取得 | libomp | oneDNN | CTranslate2 | wheel ＋ 確認 | 合計 |
|---|---|---|---|---|---|---|
| 開発機（Ryzen 7 9800X3D・16 スレッド・新規フォルダ・2026-11-10） | 11秒 | 37秒 | 83秒 | 18秒 | 58秒 | **208秒** |
| GitHub Actions `windows-2022`（4 vCPU） | 未計測 | | | | | 見込み 10〜15分 |

コンパイルの大半は oneDNN（CPU 並列が効く）。CI は 4 vCPU なので開発機の 4 倍前後と見込む
（wheel 作成の pip と確認は並列化されないので大きくは伸びない）。CI の実測は一度走らせたら追記する。
release-build-windows.yml の `timeout-minutes` は余裕を見て 60 → 120 にした。
キャッシュは使っていない（毎回ソースから。SignPath の「CI 上でソースから」に沿わせるため）。

## 4. 配布物への組み込み

### CI（署名用のリリースビルド）

release-build-windows.yml の入力 `ctranslate2`（既定 `nomkl`）。ロックファイルで公式 wheel を入れた後、
同じジョブで build.ps1 を走らせ `uv pip install --no-deps --reinstall` で差し替える。
「Sanity check」で `dist\Mojicast` に `iomp` / `mkl_` / `vcomp` の名前のファイルが無いこと、
`_internal\ctranslate2\libomp.dll` があることを確かめる。`build-info.json` も artifact に入れる。

### 手元ビルド（今のリリース手順）

```powershell
.\reazonspeech-env\Scripts\python.exe -m pip install --no-deps --force-reinstall E:\ct2-dist\ctranslate2-4.8.1+nomkl-cp311-cp311-win_amd64.whl
.\reazonspeech-env\Scripts\python.exe -c "import ctranslate2; print(ctranslate2.__version__)"   # 4.8.1+nomkl
```

そのあと PyInstaller → build_bundle.ps1。build_bundle.ps1 は同梱された ctranslate2 の種類を表示し、
公式版（libiomp5md.dll あり）のままなら CREDITS.md とずれる旨を警告する。

**注意: torch と同じ環境に入れない。** ctranslate2 は torch が入っていると import 時に読み込む。
torch の Intel OpenMP（libiomp5md）と libomp は同系統のランタイムで、二重に初期化されると
`OMP: Error #15` で落ちる（開発用の reazonspeech-env には変換作業用の torch が入っている）。
配布物（spec で torch を除外）と CI の環境（ロックファイルに torch なし）では起きない。
手元で試すときは torch の無い venv を使うか、変換作業のときだけ公式 wheel に戻す。

## 5. 公式ビルドへ戻す方法

- **CI**: release-build-windows.yml を `ctranslate2: official` で実行する（自前ビルドの段階を飛ばし、
  ロックファイルの公式 wheel のまま作る）
- **手元**: `python -m pip install --no-deps --force-reinstall ctranslate2==4.8.1`（PyPI の公式 wheel に戻る）
- **CREDITS.md**: 「LLVM OpenMP ランタイム (libomp)」の行を、元の2行
  （`Intel oneMKL（CTranslate2 に静的リンク）` / `Intel OpenMP (libiomp5md)`・Intel Simplified Software License）に戻す
  （git の履歴にある）。LICENSE_INVENTORY.md の対応欄も戻す
- アプリのコードは変えていないので、ほかに戻すものは無い（translate.py の `KMP_BLOCKTIME` は両方で効く）

## 6. 版を上げるとき

1. build.ps1 の `$CT2_TAG` / `$CT2_COMMIT`（`git ls-remote https://github.com/OpenNMT/CTranslate2 refs/tags/vX.Y.Z`）
2. 新しい版の `python/tools/prepare_build_environment_windows.sh` を読み、oneDNN の版とオプションが
   変わっていれば `$ONEDNN_VERSION` / `$ONEDNN_SHA256` / `$ONEDNN_CMAKE_OPTIONS` を合わせる
3. requirements.txt とロックファイルの `ctranslate2==` を合わせる
4. LLVM を上げるなら `$LLVM_VERSION` と2つの SHA256（GitHub の llvm-project リリースの tar.xz）
5. build.ps1 を `-ProbeModel` 付きで通し、bench/ct2_mkl_compare で公式 wheel と比べる

## 7. 人の確認（自前ビルドに切り替えた後）

🤖 の作業は済み、👤 が実機で行う。

### 7.1 Intel 2台と AMD で再測定

1. wheel を用意する（CI の build-ctranslate2-windows.yml の artifact、または手元で build.ps1）
2. `powershell -ExecutionPolicy Bypass -File bench\ct2_mkl_compare\build.ps1 -CustomWheel <whl>` で Zip を作る
   （Zip の `ct2_custom\` に自前ビルドが入る）
3. N100・i7-13700HX・AMD 機で `ct2_mkl_compare.exe` を実行（Intel は 6 回・AMD は 4 回走る。20〜50 分）
4. `result_*.txt/.json` を `bench/ct2_mkl_compare/results/日付_CPU_custom.{txt,json}` として入れる

見るところ:

- `[custom]` の `OpenMP: libomp.dll` で、`Intel の DLL` 欄が無いこと
- 訳文の一致: `custom` が `mkl`（Intel）/ `dnnl`（AMD）と何文違うか。公式どうし（mkl と dnnl）は
  前回 200/200。custom で数文変わる場合は「訳文が変わった文」を読んで、意味が変わっていないか見る
- 倍率: `custom` が `dnnl` とほぼ同じ（±5%）なら OpenMP の置き換えの影響は無い。`mkl` との差は前回の
  0.90〜1.30 倍と同程度のはず
- CPU/文: `custom` が `dnnl` / `mkl` より大きく増えていないこと（空回りの確認）
- ピークメモリ: `custom` ≒ `dnnl`

### 7.2 通しでの字幕遅延

自前ビルドを入れた Mojicast（手元ビルド、または CI の artifact の Zip）で、いつもの配信設定
（英訳 int8 ＋ 中国語訳など普段使う組み合わせ）のまま 10 分ほど話す。

- 確定字幕が出てから訳文が出るまでの間が、公式版と比べて体感で遅れないか
- タスクマネージャーで Mojicast の CPU 使用率が公式版と同程度か（話していない時に下がるか）
- ログ（save_log）に翻訳のエラーが出ていないか

Intel 機（特に N100 のような遅い CPU）で1回はやる。

### 7.3 訳文の回帰比較

7.1 の結果の「訳文が変わった文」で足りる（200文 × 4ケース）。足りない場合は、普段の配信ログから
別の文を抜いて（`bench\ct2_mkl_compare\make_sentences.py`）同じツールで比べる。

## 8. 懸念点

- **ビルドの保守**: CTranslate2 や oneDNN を上げるたびに、公式 wheel のビルド設定（oneDNN の版とオプション）
  を追う必要がある。clang-cl でのビルドは CTranslate2 の公式 CI では試されていない
- **コンパイラの違い**: 公式は MSVC、こちらは clang-cl。FMA の合成は切ったが、それ以外の最適化の差で
  まれに訳文が変わる可能性は残る（7.1 で確認）
- **libomp と他の OpenMP の同居**: 同じプロセスに Intel OpenMP が入ると落ちる（4 の torch の件）。
  今の配布物には他に OpenMP を使う部品は無い（onnxruntime / sherpa-onnx / NumPy の OpenBLAS は独自のスレッド）。
  今後 OpenMP を使う部品を足すときは注意
- **Visual Studio 同梱の clang の版**: CI イメージの更新で clang の版が変わりうる（ビルド結果のハッシュが
  変わる）。`build-info.json` に版を残している。厳密に固定したくなったら LLVM の公式リリースを取得して使う
- **Intel 機での速さ**: MKL なしは Intel 機で平均 0.90〜1.30 倍の時間（英訳 int8 が最大 1.30 倍）。
  N100 で英訳 int8 は 1文 70→82ms 程度で、字幕の遅れとしては小さいと見ているが、7.2 で確かめる
- **OpenMP の版**: LLVM 19 の libomp は Intel OpenMP より新しいが、`KMP_BLOCKTIME` 以外の環境変数
  （`KMP_AFFINITY` など）の既定が違う可能性はある。Mojicast は `KMP_BLOCKTIME` しか設定していない
