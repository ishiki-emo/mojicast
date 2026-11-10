# CTranslate2 の MKL なしビルド（Windows）

翻訳エンジン CTranslate2 の公式 wheel（PyPI の `ctranslate2==4.8.1`）は、Windows 版に
**Intel oneMKL**（`ctranslate2.dll` に静的リンク）と **Intel OpenMP**（`libiomp5md.dll`）を含む。
どちらも Intel Simplified Software License（OSI 非承認）で、SignPath Foundation の署名条件
（同梱物がすべて OSI 承認ライセンス）を満たさない。そこで CTranslate2 を同じ v4.8.1 のソースから
MKL なしでビルドし、Windows 版の配布物で使う。

**状態（2026-11-11 決定）: Windows 版の配布物は MKL なしビルド（cl ＋ 上流 LLVM OpenMP の libomp.dll）に切り替えた**
（release-build-windows.yml の既定 `ctranslate2: nomkl`、CREDITS.md も MKL なし版の記載）。3台・4ケースで訳文が公式と一致し、
速さは公式の MKL なし（oneDNN）と同じだった（9.4・9.5）。
MSVC の vcomp140＋`OMP_WAIT_POLICY=PASSIVE`（②）と clang-cl＋libomp（①）は不採用（2章・9章に記録を残す）。

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
| OpenMP ランタイム | Intel OpenMP（libiomp5md.dll） | **上流 LLVM OpenMP 19.1.7（libomp.dll・ソースからビルド）** |
| コンパイラ | MSVC（cl） | MSVC（cl。同じ。`/openmp:llvm`）。libomp だけ clang-cl |
| CPU 自動判別（ISA dispatch） | 有効 | 有効（同じ） |
| CUDA / cuDNN | 入っている（Mojicast は使わない・spec で除外） | 入れない |
| wheel の版 | 4.8.1 | 4.8.1+nomkl（`==4.8.1` を満たす） |
| ライセンス文 | — | wheel の dist-info\licenses に CTranslate2・oneDNN・LLVM OpenMP を同梱 |
| 計算後の待ち方 | `KMP_BLOCKTIME=5`（translate.py） | 同じ（libomp も KMP_BLOCKTIME が効く） |

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
CTranslate2 の CMake が `/openmp` を付けるが、`CMAKE_*_FLAGS_INIT=/openmp:llvm` を足して cl に LLVM 形式の
OpenMP 呼び出し（`__kmpc_*`）を出させ、先にビルドした上流 libomp の `libomp.lib` にリンクする（2.5）。
wheel にはその `libomp.dll` を入れる。
実際の値は wheel と一緒に出る `build-info.json` に残る（cl・clang-cl の版は `compilers`、OpenMP は `openmp_runtime` と
`openmp_dll_sha256`。比較ツールもこれを結果に写す）。

上流 LLVM OpenMP 19.1.7（`openmp-19.1.7.src.tar.xz` ＋ `cmake-19.1.7.src.tar.xz`、SHA256 は build.ps1 に固定）:

```
-DCMAKE_BUILD_TYPE=Release -DLIBOMP_ENABLE_SHARED=ON -DLIBOMP_USE_ITT_NOTIFY=OFF
-DLIBOMP_OMPT_SUPPORT=OFF -DLIBOMP_OMPD_SUPPORT=OFF -DOPENMP_ENABLE_LIBOMPTARGET=OFF
-DOPENMP_ENABLE_OMPT_TOOLS=OFF -DLIBOMP_INSTALL_ALIASES=OFF
```

## 2. OpenMP ランタイムの選定

CTranslate2 の `OPENMP_RUNTIME` は `INTEL`（libiomp5md・非 OSI）/ `COMP`（コンパイラ付属）/ `NONE`
（並列化なし・遅すぎる）。`COMP` の中身はコンパイラで決まる。

| 候補 | ライセンス | 結果 |
|---|---|---|
| **① LLVM の libomp.dll（clang-cl ＋ `/openmp`）** | Apache-2.0 WITH LLVM-exception（OSI） | **不採用**。int8 の訳文が公式とずれる（2.3） |
| **② MSVC の vcomp140.dll（cl ＋ `/openmp`）＋ PASSIVE** | VC++ 再頒布パッケージ（下記） | **不採用**（2026-11-11）。訳文は公式と一致するが 1文あたり +22〜+209ms 遅い（1.2〜3.1 倍・9章） |
| ③ MSVC の `/openmp:llvm`（libomp140.x86_64.dll） | VS の `debug_nonredist` にしか無い（**再頒布不可**） | **不可**。ビルド・訳文・速さは満たした（2.4） |
| **④ cl ＋ `/openmp:llvm` ＋ 上流の libomp.dll** | Apache-2.0 WITH LLVM-exception（OSI） | **採用**（2026-11-11）。3台で訳文 200/200 一致・公式の MKL なし（dnnl）と同じ速さ（2.5・9.4） |

④ は OpenMP まで OSI 承認ライセンスなので、② のように vcomp140.dll（VC++ 再頒布パッケージ・非 OSI）を
「System Library 相当」と説明する必要が無い。残る非 OSI は MSVC ランタイム（VCRUNTIME140 など）と
WebView2 SDK の DLL だけ（[LICENSE_INVENTORY.md](LICENSE_INVENTORY.md)）。

2.1〜2.4 は不採用にした構成の記録。

### 2.1 空回りと PASSIVE

（この節の CPU 時間は os.times で測ったもの。Windows では 15.6ms 刻みなので、12文の平均として見る参考値。9.3）

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
寝起きするため）。3台の実測では遅れが目安を超え、スレッド数を減らしても改善しなかった（9章）。

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

### 2.4 ③ MSVC ＋ `/openmp:llvm`（libomp140）を試した結果

i7-13700HX で ② の中韓の遅れが大きかったため、コンパイラは MSVC のまま OpenMP だけ LLVM 版にする構成を試した。

- **ビルド**: 上流ソース無改変で通る。oneDNN は `-DOpenMP_RUNTIME_MSVC=llvm`（CMake 3.30 以降）。CTranslate2 は
  MSVC のとき CMakeLists.txt で `/openmp` を直接付けるが、`-DCMAKE_CXX_FLAGS_INIT=/openmp:llvm` を足すと cl は
  `/openmp:llvm` を優先する（D9025）。`ctranslate2.dll` の OpenMP の依存は libomp140.x86_64.dll だけになる
- **訳文**: 4ケース（int8 の英・中・韓、英 fp32）とも公式と 200/200 一致（開発機）
- **速さ**: libomp140 は LLVM の libomp なので `KMP_BLOCKTIME=5` が効き、PASSIVE が要らない。
  開発機で英訳 23ms / CPU 109ms、中国語訳 172ms / 697ms（公式 28 / 116、171 / 686）
- **再頒布**: 不可。Microsoft の文書に明記されている
  - /openmp の説明: "Support currently isn't available for production code, since the required libomp DLLs
    aren't redistributable."（https://learn.microsoft.com/en-us/cpp/build/reference/openmp-enable-openmp-2-0-support）
  - VS 2022 Distributable Code: "You may not distribute the contents of the following folders: …\debug_nonredist"。
    `Microsoft.VC[version].OpenMP.LLVM` はこのフォルダの Utilities に載っている
    （https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution）
  - 同じ一覧で vcomp140（`Microsoft_VC143_OpenMP`）は再頒布可能コードに入っている
- 次案（cl の `/openmp:llvm` ＋ 上流 libomp）は 2.5 で試した

### 2.5 ④ cl ＋ `/openmp:llvm` ＋ 上流 LLVM libomp（2026-11-11 試作・3台で計測・採用）

③ のコンパイル（cl ＋ `/openmp:llvm`）はそのままにして、リンクする OpenMP を ① で使った上流の libomp.dll
（LLVM 公式リリースのソースからビルド・Apache-2.0 WITH LLVM-exception）に替えた。`build.ps1 -OpenMP llvm`。
確認する4点を決め、1つでも満たせなければ打ち切る条件で進めた。

1. **パッチなしでビルドが通る**: 通った。CTranslate2 は git の作業ツリーに変更なし（コミット 0d8bcd36）、
   oneDNN 3.1.1 は tarball（SHA256 固定）と全 3595 ファイル一致、LLVM openmp 19.1.7 は tarball（SHA256 固定）と
   全 741 ファイル一致。oneDNN には `-DOpenMP_*_FLAGS=/openmp:llvm` と `-DOpenMP_libomp_LIBRARY=<上流の libomp.lib>`、
   CTranslate2 には ③ と同じ `CMAKE_*_FLAGS_INIT=/openmp:llvm` ＋ `CMAKE_SHARED_LINKER_FLAGS="/LIBPATH:<上流> libomp.lib"`。
   cl は `/openmp:llvm` のとき既定で `libomp.lib` をリンクするが、/LIBPATH で上流のものが先に見つかる。
   libomp 自体は clang-cl でビルドする（① と同じ。C の ABI なので cl のコードから呼べる）
2. **依存する OpenMP が上流の libomp だけ**: `ctranslate2.dll` の依存は `libomp.dll` だけで、libomp140・vcomp140・
   libiomp5md への依存は無い。インポートは `__kmpc_fork_call` / `__kmpc_barrier` / `__kmpc_push_num_threads` /
   `__kmpc_global_thread_num` と `omp_*` 5つの計9個で、いずれも上流 libomp が持つ。libomp.dll 自体の依存は
   KERNEL32・PSAPI・VCRUNTIME140・UCRT だけ。wheel には `LICENSE.LLVM-openmp` を入れる。build.ps1 はこれらを確認し、
   違えば失敗にする
3. **訳文**: 4ケース（int8 の英・中・韓、英 fp32）とも公式と 200/200 一致（開発機）。① と違ってずれないのは、
   翻訳のコード（layer_norm の fast-math 区間を含む）を公式と同じ cl がコンパイルしているため
4. **速さ・CPU/文**（PASSIVE なし・KMP_BLOCKTIME=5）: 開発機（Ryzen 7 9800X3D・200文）で公式の MKL なし（dnnl）と
   比べて英訳 int8 1.01 倍（CPU/文 95→103ms）、英訳 fp32 1.00 倍（282→279ms）、中国語訳 1.01 倍（528→582ms）、
   韓国語訳 1.04 倍（774→787ms）。結果は [results/2026-11-11_9800X3D_upomp](../../bench/ct2_mkl_compare/results/2026-11-11_9800X3D_upomp.txt)

4点とも満たしたので、比較ツールに `llvm` variant を足し、Intel 2台で測り直した（9.4）。結果を見て採用した（9.5）。
translate.py は libomp.dll を "llvm" と判定し、PASSIVE を付けない（KMP_BLOCKTIME=5 だけが効く）。

## 3. ビルド手順

必要なもの: Visual Studio 2022（「C++ によるデスクトップ開発」と「C++ Clang ツール」）、CMake 3.18 以上（4.x 可）、git、
Python 3.11 x64。GitHub Actions の `windows-2022` には全部入っている。

```powershell
# Python は配布物と同じ 3.11.15（uv の場合）
$py = (uv python find 3.11.15).Trim()
pwsh -File tools\signing\ct2_nomkl\build.ps1 -Python $py `
    -WorkDir E:\ct2-build -OutDir E:\ct2-dist `
    -ProbeModel models_conv\fugumt-ja-en-ct2     # 任意: モデルを読んで MKL 不使用をログで確かめる
```

段階: ソース取得（CTranslate2 は git の tag ＋コミット ID を照合、oneDNN と LLVM の openmp / cmake は tar の SHA256 を照合）
→ libomp（clang-cl）→ oneDNN → CTranslate2 → wheel → 確認。`-OpenMP vcomp` で不採用の ② を作れる（比べ直す用）。

**確認で落とすもの**（どれかに当たるとスクリプトが失敗する）:

- ソースが指定のコミットでない、`version.py` 以外に変更がある
- `ctranslate2.dll` が `libiomp5md` / `mkl` / `libomp140` に依存している、OpenMP の依存が `libomp.dll` の1つでない
- `ctranslate2.dll` に MKL のシンボル文字列が残っている
- wheel の中に libiomp5md / cudnn / mkl / libomp140 がある、OpenMP の DLL が `libomp.dll` の1つでない
- wheel にライセンス文（CTranslate2・oneDNN・LLVM OpenMP）が無い
- 素の venv に入れて import・`get_supported_compute_types` が通らない
- `-ProbeModel` 指定時: int8 と float32 で読み込み・1文訳せない、`Use Intel MKL: false` が出ない

### 3.1 所要時間

| 環境 | ソース取得 | oneDNN | CTranslate2 | wheel | 確認 | 合計 |
|---|---|---|---|---|---|---|
| 開発機（Ryzen 7 9800X3D・16 スレッド・新規フォルダ・2026-11-10） | 11秒 | 107秒 | 20秒 | 45秒 | 12秒 | **196秒** |
| GitHub Actions `windows-2022`（4 vCPU・MSVC 14.44・2026-11-10） | 8秒 | 735秒 | 90秒 | 80秒 | 10秒 | **929秒**（ジョブ全体 約16分） |

**-OpenMP llvm（④）の増分**（同じ CI の実行で vcomp / llvm を並べてビルド・2026-11-11・run 42853027717）:

| 環境 | ソース取得 | libomp | oneDNN | CTranslate2 | wheel | 確認 | 合計 |
|---|---|---|---|---|---|---|---|
| CI `windows-2022`・vcomp（MSVC 14.44） | 8秒 | — | 551秒 | 67秒 | 64秒 | 9秒 | **707秒**（ジョブ 12分8秒） |
| CI `windows-2022`・llvm（MSVC 14.44・clang-cl 19.1.5） | 10秒 | 46秒 | 541秒 | 67秒 | 63秒 | 8秒 | **741秒**（ジョブ 12分46秒） |
| 開発機・llvm（MSVC 14.43・clang-cl 19.1.1・新規フォルダ） | 15秒 | 44秒 | 144秒 | 27秒 | 51秒 | 14秒 | **296秒** |

増えるのは libomp のビルド（約45秒）だけで、**CI の合計で +34秒（ジョブ +38秒）**。oneDNN の時間は実行ごとに
550〜735秒とばらつく（共有ランナーのため）。CI の llvm 版の wheel も公式と 4ケース 200/200 一致（開発機で確認）。
LLVM のソース（.tar.xz）は Python の tarfile で展開する。`windows-2022` の tar.exe は openmp-19.1.7.src.tar.xz の
展開で止まった（2回・7分と36分で打ち切り。手元の Windows 11 では数秒）。

コンパイルの大半は oneDNN。wheel 作成は pip が CTranslate2 の Python 拡張をビルドする分。
CI の wheel（MSVC 14.44）も、開発機の wheel（14.43）と同じく公式 wheel と訳文 200/200 一致（英訳 int8・韓国語訳）。
キャッシュが当たればビルドの段階は数秒で済む（release-build-windows.yml の時間は約16分短くなる）。

### 3.2 CI での使い回し（キャッシュ）

ビルドの入力が同じなら、前回の wheel を GitHub Actions のキャッシュから使う。鍵は
`ct2-nomkl-msvc-<MSVC の版>-redist-<再頒布の版>-libomp-<LLVM OpenMP の版>-clang-<clang-cl の版>-py3.11.15-<build.ps1 と probe.py のハッシュ>`。
OpenMP（libomp の版とそれをビルドする clang-cl の版）が変われば鍵も変わる。

- CTranslate2・oneDNN の版、CMake オプション、確認の内容はすべて build.ps1 に書いてあるので、
  どれかを変えればハッシュが変わり、ビルドし直しになる
- MSVC・再頒布パッケージ・clang-cl の版は `build.ps1 -PrintToolchain` で取る（CI イメージの更新で VS が上がれば鍵が変わる）
- build-ctranslate2-windows.yml は毎回キャッシュを使わずにビルドして保存する（時間の実測を兼ねる）。
  build.ps1 を変えて chore/signpath-prep に push すると走る
- release-build-windows.yml は `ctranslate2: nomkl`（既定）のとき鍵で探し、あれば使う。無ければビルドして保存する。
  `ct2_rebuild: true` でキャッシュを無視してビルドし直す。どちらの場合も SHA256SUMS.txt と照合し、
  build-info.json の `openmp` が `llvm` であることを確かめてから入れる
- キャッシュはブランチごと（既定ブランチのものは全ブランチから見える）。7日使わないと消える

**署名に出すビルドでは `ct2_rebuild: true` にする。** SignPath の Origin verification は「その実行で、
そのコミットのソースからビルドされた」ことを見る。キャッシュの wheel も同じスクリプトで CI が作ったものだが、
別の実行の成果物を持ち込む形になるので、署名用は毎回ビルドする（+約16分）。

## 4. 配布物への組み込み

### CI（署名用のリリースビルド）

release-build-windows.yml の入力 `ctranslate2` は既定で `nomkl`（入力の無い実行も nomkl 扱い）。
ロックファイルで公式 wheel を入れた後、同じジョブで build.ps1 の wheel を
`uv pip install --no-deps --reinstall` で差し替える。「Sanity check」で
[check_bundle.ps1](../../tools/signing/ct2_nomkl/check_bundle.ps1) を通す:

- `_internal\ctranslate2\libomp.dll` があり、配布物の中で libomp.dll はこの1つだけ
- libiomp5md（Intel OpenMP）・mkl・vcomp140・libomp140・libgomp の名前のファイルが無い
- `ctranslate2.dll` に MKL の文字列（"Math Kernel Library" など）が無い
- `_internal\ctranslate2-4.8.1+nomkl.dist-info\licenses` に `LICENSE`（CTranslate2）・`LICENSE.oneDNN`・
  `LICENSE.oneDNN-THIRD-PARTY-PROGRAMS`・`LICENSE.LLVM-openmp` がある（中身が Apache-2.0 / LLVM の例外条項）
- 同梱の CREDITS.md に LLVM OpenMP・oneDNN があり、Intel oneMKL・libiomp5md が無い

`build-info.json` も artifact に入れる。

**旧構成（② vcomp140）での配布物への入り方の確認（2026-11-10・手元で CI と同じ手順・記録）**: ロックファイルの venv に CI の wheel
（MSVC 14.44）を入れて PyInstaller（Mojicast.spec）→ build_bundle.ps1 -NoModels → smoke_test.ps1。

- vcomp140.dll は `_internal\ctranslate2\vcomp140.dll` の1つだけ（193,152 バイト・wheel のものと同じハッシュ）。
  `_internal` 直下などへの重複は無い。libiomp5md / mkl / libomp の名前のファイルも無い
- vcomp140 をインポートしているのは `ctranslate2.dll` だけ（dumpbin /dependents で全 DLL・pyd・exe を確認）
- smoke_test.ps1 PASS（英訳のロード時の試訳・多言語訳を含む）。実行中の Mojicast.exe のプロセスには
  `_internal\ctranslate2\VCOMP140.DLL` が読み込まれていた（Windows 側の vcomp140 ではなく同梱のもの）
- build_bundle.ps1 は「MKL なし版なのに CREDITS が公式版」の警告を出した（切り替え時は CREDITS も変える）

### 手元ビルド

開発用の reazonspeech-env には MKL なし版を入れない（torch が同じ環境にあり、ctranslate2 の import で torch の
libiomp5md と libomp が二重に初期化されて落ちる。2.3）。手元で配布物を作るときは CI と同じロックファイルの
環境を別に作り、そこへ wheel を入れる（2026-11-11 の確認はこの手順）:

```powershell
uv venv .venv --python 3.11.15
uv pip sync --python .venv .github\release\requirements-win.lock --require-hashes
uv pip install --python .venv --no-deps --reinstall <ct2-dist>\ctranslate2-4.8.1+nomkl-cp311-cp311-win_amd64.whl
.\.venv\Scripts\pyinstaller --noconfirm Mojicast.spec
.\build_bundle.ps1 -NoModels
pwsh -File tools\signing\ct2_nomkl\check_bundle.ps1 -App dist\Mojicast
.\smoke_test.ps1
```

build_bundle.ps1 は同梱された ctranslate2 の種類（libomp.dll があれば MKL なし版、libiomp5md.dll があれば公式版、
vcomp140.dll なら不採用の旧構成）と CREDITS.md の記載（`LLVM OpenMP` / `libiomp5md`）を比べ、合わなければ警告する。
reazonspeech-env のまま固めると公式版が入り、この警告が出る（署名に出すのは CI の成果物なので配布物には影響しないが、
手元ビルドを配るときは上の手順で作る）。

smoke_test.ps1 は、英訳を読み込んだ Mojicast.exe のプロセスが同梱の OpenMP の DLL（`_internal\ctranslate2\libomp.dll`）を
読み込んでいて、ほかの場所・ほかの種類の OpenMP を読み込んでいないことも確かめる。

### 切り替えたときに変えたもの（2026-11-11）

1. build.ps1 の既定を `-OpenMP llvm` に、build-ctranslate2-windows.yml を llvm だけに
2. release-build-windows.yml の `ctranslate2` の既定を `nomkl` に、Sanity check を check_bundle.ps1 に
3. CREDITS.md の「Intel oneMKL」「Intel OpenMP (libiomp5md)」の2行を消し、「LLVM OpenMP 19.1.7（libomp.dll）」を足した
   （oneDNN の行は残す）。自前ビルドであることとライセンス文の場所を書いた
4. LICENSE_INVENTORY.md・SIGNPATH_APPLICATION.draft.md
5. build_bundle.ps1 の照合、smoke_test.ps1 の OpenMP の確認、`tests/test_ct2_nomkl.py`

## 5. 公式ビルドへ戻す方法

- **CI**: release-build-windows.yml を `ctranslate2: official` で実行する（Sanity check の check_bundle.ps1 は飛ばされる）
- **手元**: `python -m pip install --no-deps --force-reinstall ctranslate2==4.8.1`（PyPI の公式 wheel に戻る）
- **CREDITS.md / LICENSE_INVENTORY.md**: 4 の「切り替えたときに変えたもの」を逆に（git の履歴にある）。
  既定を official に戻すなら release-build-windows.yml と tests/test_ct2_nomkl.py も
- アプリのコードは戻さなくてよい（translate.py の `KMP_BLOCKTIME=5` は Intel OpenMP にも libomp にも効く）

## 6. 版を上げるとき

1. build.ps1 の `$CT2_TAG` / `$CT2_COMMIT`（`git ls-remote https://github.com/OpenNMT/CTranslate2 refs/tags/vX.Y.Z`）
2. 新しい版の `python/tools/prepare_build_environment_windows.sh` を読み、oneDNN の版とオプションが
   変わっていれば `$ONEDNN_VERSION` / `$ONEDNN_SHA256` / `$ONEDNN_CMAKE_OPTIONS` を合わせる
3. requirements.txt とロックファイルの `ctranslate2==` を合わせる
4. build.ps1 を `-ProbeModel` 付きで通し、bench/ct2_mkl_compare で公式 wheel と比べる（訳文の一致を見る）

## 7. 人の確認（採用を決めるための実測）

（② は 2026-11-10、④ は 2026-11-11 に3台で実施済み。結果と判断は 9章。以下は ② のときの手順の記録）

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

- ~~vcomp140 のライセンス~~・~~PASSIVE の遅れ~~: ④ に切り替えたので解消（vcomp140 を同梱しない・PASSIVE を使わない）
- **libomp と clang-cl の保守**: LLVM の版（19.1.7）は build.ps1 に固定。上げるときは SHA256 を取り直し、
  bench/ct2_mkl_compare で訳文と速さを比べ直す。clang-cl は VS 付属のもの（版は build-info.json とキャッシュの鍵に入る）
- **cl の `/openmp:llvm`**: Microsoft の文書は「必要な libomp の DLL が再頒布できないので本番には使えない」としている
  （2.4。DLL は上流のものに替えたのでこの理由は当たらないが、Microsoft が本番用として案内している組み合わせではない）。
  3台・4ケースで訳文一致と速さを確かめた。VS の更新で挙動が変わらないかは、鍵が変わった後の最初のビルドで比較ツールを回して見る
- **ビルドの保守**: CTranslate2 や oneDNN を上げるたびに、公式 wheel のビルド設定（oneDNN の版とオプション）
  を追う必要がある
- **他の OpenMP との同居**: 今の配布物で OpenMP を使うのは ctranslate2.dll だけ（2.2）。今後 OpenMP を使う
  部品（Intel OpenMP や vcomp を持ち込むもの）を足すと、同じプロセスに OpenMP が2つ載る（torch と同居したときの
  `OMP: Error #15` のように落ちうる）。check_bundle.ps1 と smoke_test.ps1 がこれを見張る
- **Intel 機での速さ**: MKL なしは Intel 機で1文あたり最大 +83ms（N100 の韓国語訳・9.4）。AMD 機は変わらない
- **CI イメージの更新**: VS の版が上がるとビルド結果のハッシュが変わる（キャッシュの鍵も変わる）。
  `build-info.json` に MSVC と再頒布パッケージの版を残している

## 9. 判断（② 2026-11-10 → ④ 2026-11-11）

### 9.1 結果

3台（Ryzen 7 9800X3D / i7-13700HX / N100）で bench/ct2_mkl_compare を実行した
（[results/](../../bench/ct2_mkl_compare/results/)）。

- **訳文**: 全台・全ケース（英訳 int8 / fp32・中国語訳・韓国語訳 × 200文）で公式と 200/200 一致。ビルドの正しさは確認できた
- **遅れ**: vcomp＋PASSIVE（custom_passive）は、MKL あり（Intel 機の現行）と比べて1文の時間（平均）が次のとおり増えた

| | 9800X3D | i7-13700HX | N100 |
|---|---|---|---|
| 英訳 int8 | +30ms | +30ms | +54ms |
| 中国語訳 | +22ms | +45ms | +92ms |
| 韓国語訳 | +42ms | +112ms | +209ms（p95 797→1150ms） |

  （9800X3D は AMD で MKL を使わないので、比べる相手は dnnl）
- 時間の比（平均）で 1.2〜1.8 倍。ただし英訳 int8 は元が速いので比が大きく出る（9800X3D 3.10 倍・i7-13700HX 2.29 倍・N100 1.82 倍）。全ケースを含めると 1.19〜3.10 倍
- i7-13700HX の韓国語訳 +112ms の内訳: MKL→oneDNN で +31ms、Intel OpenMP→vcomp で +34ms、PASSIVE で +47ms
- **スレッド数**: 1 / 2 スレッドにしても改善しなかった
- ③（MSVC＋libomp140）は速さは公式並みだったが、Microsoft が DLL の再頒布を認めていない（2.4）

### 9.2 判断

- リリースの既定は **official のまま確定**（release-build-windows.yml・CREDITS.md とも公式版）
- MKL なしビルドは **SignPath に求められたら切り替える控え**として維持する。build.ps1・CI（build-ctranslate2-windows.yml・
  release-build-windows.yml の `ctranslate2: nomkl`）・比較ツール・translate.py の PASSIVE 判定はそのまま残す
- SignPath の申請では、公式バイナリが Intel oneMKL / Intel OpenMP を含むことを開示し、求められれば次のリリースから
  切り替えると書く（[SIGNPATH_APPLICATION.draft.md](SIGNPATH_APPLICATION.draft.md)）
- 切り替えるときは 4章「切り替えるときに変えるもの」の手順

### 9.3 CPU/文 の計測方法の見直し

3台の結果の CPU/文 には異常値があった（N100 の fp32・2スレッドで 5ms、4スレッドが1スレッドより少ない）。原因は2つ。

1. **時計の刻み**: CPU 時間を `os.times()` で測っていた。Windows ではタイマー割り込みの 15.6ms 刻みで数えるため、
   数 ms で終わる計算は 0 か 15.6ms のどちらかになる（3ms の計算 20回で 0 と 15.6 が混ざることを確かめた）
2. **条件の不揃い**: スレッド数の比較で、4 スレッドの行だけ本計測の値（20文・2回のうち少ない方）を使い、
   1 / 2 スレッドの行（10文・1回）と条件が違っていた

比較ツールを直した（2026-11-10）: Windows では CPU サイクル数（`QueryProcessCycleTime`）を、1スレッドの空回りで
校正した周波数で割って CPU 時間にする（3ms の計算を毎回約 3.0ms と測れ、長い区間では os.times と 0.5% 以内で一致）。
スレッド数の比較は 1 / 2 / 4 をすべて同じ条件で測り直す。結果には測り方（`CPU 時間の測り方:`）が出る。

**このため、2026-11-10 の3台の結果（results/ の 2026-11-10_*）の CPU/文 は参考値として扱う。** 特に数十 ms 以下の値と、
スレッド数の比較の CPU/文 は信用しない。1文の時間（実時間）と訳文の一致は perf_counter と文字列比較なので影響を受けない。

（9.1〜9.3 は ② のときの記録。9.2 の「official のまま」は 9.5 で置き換えた）

### 9.4 ④（上流 libomp）の3台計測（2026-11-11）

CI の wheel（run 42853027717）を比較ツールに入れ、i7-13700HX と N100 で mkl / dnnl / llvm / custom_passive を測った
（9800X3D は 2.5。[results/2026-11-11_*_upomp](../../bench/ct2_mkl_compare/results/)・CPU 時間は QueryProcessCycleTime）。

- **訳文**: 両機とも全4ケース 200/200 一致（mkl 基準）。スレッド数 1 / 2 / 4 でも 50/50
- **公式の MKL なし（dnnl）との比**（平均）: 13700HX 0.99〜1.00 倍、N100 1.00 倍。CPU/文 も ±3% 以内。
  つまり Intel OpenMP → 上流 libomp の差は無く、残るのは MKL → oneDNN の差だけ
- **Intel 機の現行（mkl）からの増え**（1文の平均）。括弧は同じ回の custom_passive（vcomp＋PASSIVE）

| | i7-13700HX | N100 |
|---|---|---|
| 英訳 int8 | +5ms（+27ms） | +7ms（+55ms） |
| 英訳 fp32 | −5ms（+19ms） | +21ms（+64ms） |
| 中国語訳 | +19ms（+44ms） | +42ms（+86ms） |
| 韓国語訳 | +29ms（+97ms） | +83ms（+201ms）・p95 776→927ms |

- **CPU/文**（mkl → llvm）: 13700HX 英 int8 88→114ms・fp32 256→225ms・中 383→457ms・韓 564→670ms、
  N100 276→306・600→654・1053→1172・1492→1723ms。fp32 以外で 1.1〜1.3 倍（dnnl と同じ。MKL を外した分）
- **ピークメモリ**: M2M（中韓）で 932→593MB（13700HX）・913→590MB（N100）と約 320MB 減る（dnnl と同じ）
- **スレッド数**: llvm は mkl と同じく 4 スレッドが最も速い。custom_passive のように 2・4 スレッドで頭打ちにならない。
  スレッド数を翻訳の種類ごとに変える必要は無い
- AMD（9800X3D）は現行がもともと dnnl なので、④ にしても速さは変わらない（2.5）

### 9.5 判断（2026-11-11）

- **Windows 版の配布物を ④（cl ＋ 上流 LLVM OpenMP）に切り替える**（えもさん。「よほど数値が悪くない限りリリース前提」に対し、
  悪化は MKL を外した分だけだった）。次のリリースから。release-build-windows.yml の既定 `nomkl`、CREDITS.md も切り替えた（4章）
- 3台の数字（公式の MKL あり＝Intel 機の現行と比べた1文の平均の増え。AMD は現行が dnnl）:

| | 9800X3D（AMD） | i7-13700HX | N100 |
|---|---|---|---|
| 英訳 int8 | ±0（dnnl 比 1.01 倍） | +5ms | +7ms |
| 英訳 fp32 | ±0（1.00 倍） | −5ms | +21ms |
| 中国語訳 | ±0（1.01 倍） | +19ms | +42ms |
| 韓国語訳 | ±0（1.04 倍） | +29ms | +83ms |
| 訳文（4ケース × 200文） | 200/200 一致 | 200/200 一致 | 200/200 一致 |

- **不採用の記録**: ② MSVC＋vcomp140＋PASSIVE は同じ回の計測で 13700HX +19〜+97ms・N100 +55〜+201ms と遅く、
  vcomp140 は非 OSI（2章・9.1）。① clang-cl＋libomp は int8 の訳文が公式とずれる（2.3）。③ libomp140 は再頒布不可（2.4）。
  build.ps1 の `-OpenMP vcomp` と translate.py の PASSIVE 判定は、比べ直すときのために残している
- SignPath の申請書は「Intel のものを含まない・公開ソースから CI でビルド・3種類の CPU で訳文一致」に書き直した
