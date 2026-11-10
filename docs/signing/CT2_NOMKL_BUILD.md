# CTranslate2 の MKL なしビルド（Windows）

翻訳エンジン CTranslate2 の公式 wheel（PyPI の `ctranslate2==4.8.1`）は、Windows 版に
**Intel oneMKL**（`ctranslate2.dll` に静的リンク）と **Intel OpenMP**（`libiomp5md.dll`）を含む。
どちらも Intel Simplified Software License（OSI 非承認）で、SignPath Foundation の署名条件
（同梱物がすべて OSI 承認ライセンス）を満たさない。そこで CTranslate2 を同じ v4.8.1 のソースから
MKL なしでビルドし、Windows 版の配布物で使う案を進めている。

**状態（2026-11-10）: 案は「MSVC＋vcomp140＋`OMP_WAIT_POLICY=PASSIVE`」。採用は Intel 2台＋AMD の実測（7章）のあと。
それまでリリースの既定は公式 wheel のまま**（release-build-windows.yml の `ctranslate2: official`、CREDITS.md も公式版の記載）。

- ビルドスクリプト: [tools/signing/ct2_nomkl/build.ps1](../../tools/signing/ct2_nomkl/build.ps1)（ローカル・CI 共通）
- CI: [build-ctranslate2-windows.yml](../../.github/workflows/build-ctranslate2-windows.yml)（毎回フルビルド・時間の実測とキャッシュ作り）、
  [release-build-windows.yml](../../.github/workflows/release-build-windows.yml)（`ctranslate2: nomkl` のとき同じジョブ内で差し替え）
- 計測: [bench/ct2_mkl_compare](../../bench/ct2_mkl_compare/README.md)
- macOS 版は対象外（公式 wheel が MKL を含まず Apple Accelerate を使うため。そのまま）

## 1. 何が変わるか

| | 公式 wheel | 自前ビルド（`4.8.1+nomkl`） |
|---|---|---|
| CTranslate2 のソース | v4.8.1 | 同じ v4.8.1（コミット `0d8bcd36`。手を加えない） |
| 行列演算 | Intel CPU: oneMKL / それ以外: oneDNN | **全 CPU で oneDNN 3.1.1**（公式と同じ版・同じオプション・静的リンク） |
| OpenMP ランタイム | Intel OpenMP（libiomp5md.dll） | **MSVC OpenMP（vcomp140.dll）** |
| コンパイラ | MSVC（cl） | MSVC（cl。同じ） |
| CPU 自動判別（ISA dispatch） | 有効 | 有効（同じ） |
| CUDA / cuDNN | 入っている（Mojicast は使わない・spec で除外） | 入れない |
| wheel の版 | 4.8.1 | 4.8.1+nomkl（`==4.8.1` を満たす） |
| ライセンス文 | — | wheel の dist-info\licenses に CTranslate2・oneDNN を同梱 |
| 計算後の待ち方 | `KMP_BLOCKTIME=5`（translate.py） | `OMP_WAIT_POLICY=PASSIVE`（translate.py が vcomp を見つけたときだけ） |

AMD 機は公式 wheel でも MKL を使わず oneDNN で動いている（`Use Intel MKL: false`）。
MKL を外して計算方法が変わるのは Intel 機だけ。OpenMP の部品が変わるのは全機種。

### 1.1 CMake オプション

CTranslate2:

```
-DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5
-DWITH_MKL=OFF -DWITH_DNNL=ON -DOPENMP_RUNTIME=COMP -DENABLE_CPU_DISPATCH=ON
-DWITH_CUDA=OFF -DWITH_CUDNN=OFF -DWITH_RUY=OFF -DBUILD_CLI=OFF -DBUILD_TESTS=OFF
```

oneDNN 3.1.1（公式 wheel の `python/tools/prepare_build_environment_windows.sh` と同じ。tar の SHA256 は build.ps1 に固定）:

```
-DONEDNN_LIBRARY_TYPE=STATIC -DONEDNN_CPU_RUNTIME=OMP -DONEDNN_GPU_RUNTIME=NONE
-DONEDNN_BUILD_EXAMPLES=OFF -DONEDNN_BUILD_TESTS=OFF -DONEDNN_ENABLE_WORKLOAD=INFERENCE
-DONEDNN_ENABLE_PRIMITIVE=CONVOLUTION;REORDER -DONEDNN_BUILD_GRAPH=OFF
```

共通: Visual Studio 2022 の開発環境（vcvars64）を取り込み、Ninja ＋ cl。`OPENMP_RUNTIME=COMP` で
CTranslate2 の CMake が `/openmp` を付け、cl が vcomp140 をリンクする。wheel には VS 2022 の
再頒布フォルダ（`VC\Redist\MSVC\<版>\x64\Microsoft.VC143.OpenMP`）の vcomp140.dll を入れる。
実際の値は wheel と一緒に出る `build-info.json` に残る（比較ツールもこれを結果に写す）。

## 2. OpenMP ランタイムの選定

CTranslate2 の `OPENMP_RUNTIME` は `INTEL`（libiomp5md・非 OSI）/ `COMP`（コンパイラ付属）/ `NONE`
（並列化なし・遅すぎる）。`COMP` の中身はコンパイラで決まる。

| 候補 | ライセンス | 結果 |
|---|---|---|
| **① LLVM の libomp.dll（clang-cl ＋ `/openmp`）** | Apache-2.0 WITH LLVM-exception（OSI） | **不採用**。int8 の訳文が公式とずれる（2.3） |
| **② MSVC の vcomp140.dll（cl ＋ `/openmp`）＋ PASSIVE** | VC++ 再頒布パッケージ（下記） | **案**。訳文は公式と一致。採用は実測のあと |
| MSVC の `/openmp:llvm`（libomp140.x86_64.dll） | VS の `debug_nonredist` にしか無い（再配布不可） | 対象外 |

vcomp140.dll は VC++ 再頒布パッケージの一部で、配布物に既に入っている MSVCP140 / VCRUNTIME140 と
同じ扱い（Microsoft のソフトウェア ライセンス条項の「再頒布可能コード」）。OSI 承認ではないため、
SignPath には「MSVC でビルドした以上必ず付くランタイム（System Library 相当）」として説明する
（[SIGNPATH_APPLICATION.draft.md](SIGNPATH_APPLICATION.draft.md)）。通るかは申請してみないと分からない（8章）。

### 2.1 空回りと PASSIVE

OpenMP は計算後もしばらくスレッドを空回りさせて次の仕事を待つ。Mojicast は1文訳したら次の発話まで
間が空くので、その空回りがまるごと CPU 負荷になる。公式 wheel（Intel OpenMP）は `KMP_BLOCKTIME=5` で
5ms に縮めている（translate.py）。vcomp140 にはこれに当たる設定が無く、`OMP_WAIT_POLICY=PASSIVE`
（待たずに眠る）しかない。

開発機（Ryzen 7 9800X3D・4スレッド・1文訳して1秒空けるまで・6文×2・2026-11-10）:

| | 英訳 int8 1文 / CPU | 中国語訳 1文 / CPU | 訳文 |
|---|---|---|---|
| 公式 wheel（Intel OpenMP・KMP_BLOCKTIME=5） | 23ms / 94ms | 161ms / 646ms | — |
| 自前・vcomp 既定（空回りあり） | 26ms / 396ms | 171ms / 969ms | 200/200 一致 |
| 自前・vcomp ＋ PASSIVE（translate.py が設定） | 58ms / 86ms | 201ms / 587ms | 200/200 一致 |

PASSIVE で CPU は公式と同等以下に戻るが、1文の時間が延びる（1文の中のデコード1歩ごとにスレッドが
寝起きするため）。どこまで許せるかと、スレッド数を減らせば両方抑えられるか（7.1）を実測で決める。

### 2.2 PASSIVE の影響範囲（確認済み）

- **vcomp を読む部品**: v1.1.0 の配布物（`_internal` の DLL・pyd 214 個）を調べ、OpenMP の DLL を
  インポートしているのは `ctranslate2.dll` だけ。onnxruntime.dll（sherpa-onnx 同梱）の「VComp」という
  文字列は ONNX の Compress 演算子の名前で、インポート表に vcomp は無い。NumPy の OpenBLAS も独自のスレッド
- **設定のタイミング**: translate.py は import された時点（モジュールの先頭）で、ctranslate2 を import せずに
  パッケージのフォルダの DLL を見て vcomp か判定し、`OMP_WAIT_POLICY` を設定する。ctranslate2 の import は
  関数の中（load_translator など）だけで、アプリの他の場所からは import していない。
  `tests/test_ct2_nomkl.py` で「設定された時点で ctranslate2 が未読込」「利用者の指定を上書きしない」
  「Intel OpenMP には設定しない」を確かめている。プロセス内で設定した値が vcomp に効くことは CPU 時間で
  確かめた（上の表の PASSIVE 行は外から指定した場合の 59ms / 94ms と同じ）
- **音声認識の速さ**: sherpa-onnx 1.13.8・k2 1スレッド・78秒13発話（bench/bench_sherpa_version.py）を
  `OMP_WAIT_POLICY` なし / PASSIVE で2回ずつ。decode 時間 greedy 1.45・1.55s / 1.48・1.40s、hotwords
  1.70・2.14s / 1.70・1.85s、SenseVoice 2.12・2.15s / 1.99・2.17s でばらつきの範囲。認識文の差分 0/13

### 2.3 ① clang-cl ＋ libomp を不採用にした調査

libomp は Intel OpenMP がオープンソース化されたもので `KMP_BLOCKTIME=5` がそのまま効き、CPU 時間は
公式と同等だった（英訳 98ms・中国語訳 693ms）。しかし int8 の訳文が公式と **英 26/200・中 24/200・韓 46/200**
文変わった（float32 は一致）。

- cl の `/openmp` が出すコードは vcomp の入口（`_vcomp_fork` など）を呼ぶ。Intel の libiomp5md はこの入口も
  持つので公式は cl ＋ libiomp5md で作れているが、LLVM の libomp は持っていない（`__kmpc_*` だけ）。
  そのため libomp を使うには CTranslate2 と oneDNN を clang-cl でコンパイルするしかない
- 原因は CTranslate2 の layer_norm の `FFAST_MATH` 区間。clang-cl は `_MSC_VER` を定義するので
  `__pragma(float_control(precise, off))` の分岐に入り、総和の並べ替え（ベクトル化の順序）が MSVC と変わる。
  int8 は量子化の境目で1段の丸めの差が別のトークンに化ける
- 試して効かなかったもの: `-ffp-contract=off`（FMA の合成を止める。差は減らず）、fast-math 区間を切る
  （さらに差が増えた。公式は fast-math の並べ替え込みの値なので、合わせるには MSVC と同じ並べ替えが要る）
- ビルドの記録（再開するとき用）: LLVM OpenMP 19.1.7（`openmp-19.1.7.src.tar.xz` ＋ `cmake-19.1.7.src.tar.xz`）を
  `-DLIBOMP_USE_ITT_NOTIFY=OFF -DLIBOMP_OMPT_SUPPORT=OFF -DLIBOMP_OMPD_SUPPORT=OFF` 等でビルド。
  VS の ClangCL ジェネレータでは C++17 が効かないので Ninja、アセンブラは llvm-ml ではなく ml64、
  CTranslate2 のリンクに `CMAKE_SHARED_LINKER_FLAGS="/LIBPATH:<libomp>\lib libomp.lib"` が要った。
  build.ps1 の履歴（コミット b7109ae）に全手順がある
- 開発機に torch が入っていると、ctranslate2 の import で torch の libiomp5md と libomp が二重に初期化され
  `OMP: Error #15` で落ちた（vcomp では起きない）

## 3. ビルド手順

必要なもの: Visual Studio 2022（「C++ によるデスクトップ開発」）、CMake 3.18 以上（4.x 可）、git、
Python 3.11 x64。GitHub Actions の `windows-2022` には全部入っている。

```powershell
# Python は配布物と同じ 3.11.15（uv の場合）
$py = (uv python find 3.11.15).Trim()
powershell -ExecutionPolicy Bypass -File tools\signing\ct2_nomkl\build.ps1 -Python $py `
    -WorkDir E:\ct2-build -OutDir E:\ct2-dist `
    -ProbeModel models_conv\fugumt-ja-en-ct2     # 任意: モデルを読んで MKL 不使用をログで確かめる
```

段階: ソース取得（CTranslate2 は git の tag ＋コミット ID を照合、oneDNN は tar の SHA256 を照合）
→ oneDNN → CTranslate2 → wheel → 確認。

**確認で落とすもの**（どれかに当たるとスクリプトが失敗する）:

- ソースが指定のコミットでない、`version.py` 以外に変更がある
- `ctranslate2.dll` が `libiomp5md` / `mkl` / `libomp` に依存している、または vcomp140 に依存していない
- `ctranslate2.dll` に MKL のシンボル文字列が残っている
- wheel の中に libiomp5md / cudnn / mkl / libomp がある、vcomp140.dll が無い
- wheel にライセンス文（CTranslate2・oneDNN）が無い
- 素の venv に入れて import・`get_supported_compute_types` が通らない
- `-ProbeModel` 指定時: int8 と float32 で読み込み・1文訳せない、`Use Intel MKL: false` が出ない

### 3.1 所要時間

| 環境 | ソース取得 | oneDNN | CTranslate2 | wheel | 確認 | 合計 |
|---|---|---|---|---|---|---|
| 開発機（Ryzen 7 9800X3D・16 スレッド・新規フォルダ・2026-11-10） | 11秒 | 107秒 | 20秒 | 45秒 | 12秒 | **196秒** |
| GitHub Actions `windows-2022`（4 vCPU） | （3.2 の実測を記入） | | | | | |

コンパイルの大半は oneDNN。wheel 作成は pip が CTranslate2 の Python 拡張をビルドする分。

### 3.2 CI での使い回し（キャッシュ）

ビルドの入力が同じなら、前回の wheel を GitHub Actions のキャッシュから使う。鍵は
`ct2-nomkl-msvc-<MSVC の版>-redist-<再頒布の版>-py3.11.15-<build.ps1 と probe.py のハッシュ>`。

- CTranslate2・oneDNN の版、CMake オプション、確認の内容はすべて build.ps1 に書いてあるので、
  どれかを変えればハッシュが変わり、ビルドし直しになる
- MSVC と再頒布パッケージの版は `build.ps1 -PrintToolchain` で取る（CI イメージの更新で VS が上がれば鍵が変わる）
- build-ctranslate2-windows.yml は毎回キャッシュを使わずにビルドして保存する（時間の実測を兼ねる）。
  build.ps1 を変えて chore/signpath-prep に push すると走る
- release-build-windows.yml は `ctranslate2: nomkl` のとき鍵で探し、あれば使う。無ければビルドして保存する。
  `ct2_rebuild: true` でキャッシュを無視してビルドし直す。どちらの場合も SHA256SUMS.txt と照合してから入れる
- キャッシュはブランチごと（既定ブランチのものは全ブランチから見える）。7日使わないと消える

**署名に出すビルドでは `ct2_rebuild: true` にする。** SignPath の Origin verification は「その実行で、
そのコミットのソースからビルドされた」ことを見る。キャッシュの wheel も同じスクリプトで CI が作ったものだが、
別の実行の成果物を持ち込む形になるので、署名用は毎回ビルドする（+4〜15分程度）。

## 4. 配布物への組み込み

### CI（署名用のリリースビルド）

release-build-windows.yml の入力 `ctranslate2`（既定 `official`）を `nomkl` にして実行する。
ロックファイルで公式 wheel を入れた後、同じジョブで build.ps1 の wheel を
`uv pip install --no-deps --reinstall` で差し替える。「Sanity check」で `dist\Mojicast` に
`iomp` / `mkl_` / `libomp` / `libgomp` の名前のファイルが無いこと、`_internal\ctranslate2\vcomp140.dll` が
あることを確かめる。`build-info.json` も artifact に入れる。

### 手元ビルド（今のリリース手順）

```powershell
.\reazonspeech-env\Scripts\python.exe -m pip install --no-deps --force-reinstall E:\ct2-dist\ctranslate2-4.8.1+nomkl-cp311-cp311-win_amd64.whl
.\reazonspeech-env\Scripts\python.exe -c "import ctranslate2; print(ctranslate2.__version__)"   # 4.8.1+nomkl
```

そのあと PyInstaller → build_bundle.ps1。build_bundle.ps1 は同梱された ctranslate2 の種類
（libiomp5md.dll があれば公式版、vcomp140.dll があれば MKL なし版）と CREDITS.md の記載（`libiomp5md` の
有無）を比べ、合わなければ警告する（どちらの向きでも）。

### 切り替えるときに変えるもの

1. release-build-windows.yml の `ctranslate2` の既定を `nomkl` に
2. CREDITS.md の「Intel oneMKL」「Intel OpenMP (libiomp5md)」の2行を消す（oneDNN の行は残す）。
   vcomp140 は「Microsoft Visual C++ / Universal CRT ランタイム」の行に含まれる
   （由来の欄に「CTranslate2 の並列実行（vcomp140）」を足す）
3. LICENSE_INVENTORY.md の対応欄
4. `python -m unittest tests.test_ct2_nomkl`（CREDITS と既定が合っているかを見る）

## 5. 公式ビルドへ戻す方法

- **CI**: release-build-windows.yml を `ctranslate2: official`（今の既定）で実行する
- **手元**: `python -m pip install --no-deps --force-reinstall ctranslate2==4.8.1`（PyPI の公式 wheel に戻る）
- **CREDITS.md / LICENSE_INVENTORY.md**: 4 の「切り替えるときに変えるもの」を逆に（git の履歴にある）
- アプリのコードは戻さなくてよい（translate.py は同梱の ctranslate2 を見て、vcomp のときだけ PASSIVE にする。
  `KMP_BLOCKTIME` は Intel OpenMP にだけ効く）

## 6. 版を上げるとき

1. build.ps1 の `$CT2_TAG` / `$CT2_COMMIT`（`git ls-remote https://github.com/OpenNMT/CTranslate2 refs/tags/vX.Y.Z`）
2. 新しい版の `python/tools/prepare_build_environment_windows.sh` を読み、oneDNN の版とオプションが
   変わっていれば `$ONEDNN_VERSION` / `$ONEDNN_SHA256` / `$ONEDNN_CMAKE_OPTIONS` を合わせる
3. requirements.txt とロックファイルの `ctranslate2==` を合わせる
4. build.ps1 を `-ProbeModel` 付きで通し、bench/ct2_mkl_compare で公式 wheel と比べる（訳文の一致を見る）

## 7. 人の確認（採用を決めるための実測）

🤖 の作業は済み、👤 が実機で行う。

### 7.1 Intel 2台と AMD で測る（PASSIVE あり/なし・スレッド数）

1. wheel を用意する（CI の build-ctranslate2-windows.yml の artifact、または手元で build.ps1）
2. `powershell -ExecutionPolicy Bypass -File bench\ct2_mkl_compare\build.ps1 -CustomWheel <whl>` で Zip を作る
   （Zip の `ct2_custom\` に自前ビルドが入る）
3. N100・i7-13700HX・AMD 機で `ct2_mkl_compare.exe` を実行
   - Intel: mkl / dnnl / custom_passive / custom を各2回（8回）＋スレッド比較 6回
   - AMD: dnnl / custom_passive / custom を各2回（6回）＋スレッド比較 6回
   - N100 で1時間前後、速い機種で20〜30分
4. `result_*.txt/.json` を `bench/ct2_mkl_compare/results/日付_CPU_vcomp.{txt,json}` として入れる

見るところ:

- `[custom_passive]` の `OMP_WAIT_POLICY: PASSIVE`、`OpenMP: vcomp140.dll`、`Intel の DLL` 欄が無いこと
- 訳文の一致: custom / custom_passive が基準（Intel は mkl、AMD は dnnl）と何文違うか。
  開発機（AMD）では dnnl と 200/200 一致。Intel では mkl と dnnl の差（前回 200/200）以上に変わらないこと
- **custom_passive の倍率と CPU/文**: 現行（Intel は mkl、AMD は dnnl）と比べる。CPU/文が現行並みで、
  1文の時間の増え（英訳で数十 ms）が許せるか
- **スレッド数の比較**: custom_passive を 1 / 2 スレッドにしたとき、時間と CPU/文が 4 スレッドの現行と比べて
  どうなるか。開発機の試運転（4文）では、英訳 int8 は custom_passive 1 スレッドが 26ms（4 スレッドの 43ms より速い・
  dnnl 4 スレッドは 16ms）、韓国語訳は 1 スレッドで 415ms（4 スレッド 261ms）。スレッドを減らすと PASSIVE の
  寝起きが減る代わりに計算が遅くなるので、翻訳の種類ごとに最適が違う可能性がある
- 一致(同) が 4 スレッドと比べて変わるか（int8 はスレッド数で総和の順序が変わりうる）
- ピークメモリ: custom ≒ dnnl

決めること: (a) vcomp＋PASSIVE を採るか、(b) 採るならスレッド数を翻訳の種類ごとに変えるか、
(c) 遅れが大きすぎるなら MKL なし版を見送る（公式 wheel のまま・SignPath は別の道を探す）。

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

- **vcomp140 のライセンス**: OSI 承認ではない（VC++ 再頒布パッケージ）。MSVC でビルドしたものに必ず付く
  ランタイムと同列（MSVCP140 などは既に同梱）だが、SignPath が OpenMP ランタイムまで System Library と
  認めるかは未確認。認められなければ①（libomp）に戻って訳文のずれを受け入れるか、別の方法を探す
- **PASSIVE の遅れ**: 1文の中のデコード1歩ごとにスレッドが寝起きするので、遅い CPU ほど遅れが大きい
  可能性がある（N100 で要確認）。vcomp には「数 ms だけ待つ」設定が無く、中間は取れない
- **ビルドの保守**: CTranslate2 や oneDNN を上げるたびに、公式 wheel のビルド設定（oneDNN の版とオプション）
  を追う必要がある
- **他の OpenMP との同居**: 今の配布物で OpenMP を使うのは ctranslate2.dll だけ（2.2）。今後 OpenMP を使う
  部品を足すと、`OMP_WAIT_POLICY=PASSIVE` がその部品にも効く（プロセス全体の環境変数のため）
- **Intel 機での速さ**: MKL なしは Intel 機で平均 0.90〜1.30 倍の時間（英訳 int8 が最大 1.30 倍・前回の測定）。
  これに PASSIVE の遅れが重なる
- **CI イメージの更新**: VS の版が上がるとビルド結果のハッシュが変わる（キャッシュの鍵も変わる）。
  `build-info.json` に MSVC と再頒布パッケージの版を残している
