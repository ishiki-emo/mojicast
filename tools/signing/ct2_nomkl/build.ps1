# CTranslate2 を Intel MKL・Intel OpenMP なしでビルドし、Python の wheel を作る（Windows x64）。
#
# 公式の PyPI wheel（ctranslate2 4.8.1）との違い。翻訳のコードもコンパイラ（MSVC）も同じ:
#   - WITH_MKL=OFF        … Intel oneMKL（非 OSI）を静的リンクしない。行列演算は公式でも使っている
#                           oneDNN 3.1.1（Apache-2.0・静的リンク）だけになる
#   - OPENMP_RUNTIME=COMP … Intel OpenMP（libiomp5md.dll・非 OSI）の代わりに、MSVC の OpenMP
#                           ランタイム（vcomp140.dll・VC++ 再頒布パッケージ）を使う
#   - WITH_CUDA=OFF       … Mojicast は CPU だけで動くので GPU 部分を入れない（cudnn64_9.dll も入らない）
#   - wheel の版に +nomkl を付ける（`ctranslate2==4.8.1` の指定はそのまま満たす）
# CPU の自動判別（ENABLE_CPU_DISPATCH）は公式と同じく有効。
# vcomp140 は計算後にスレッドが空回りする（Intel OpenMP の KMP_BLOCKTIME に当たる設定が無い）ので、
# Mojicast は OMP_WAIT_POLICY=PASSIVE を設定して使う（translate.py）。
# clang-cl＋LLVM libomp の構成は int8 の訳文が公式とずれるため不採用。詳しくは docs/signing/CT2_NOMKL_BUILD.md。
#
# -OpenMP llvm（試作・2026-11-11）: コンパイラは cl のまま /openmp:llvm で LLVM 形式の OpenMP 呼び出しを出させ、
#   リンクする OpenMP ランタイムを、LLVM 公式リリースのソースからビルドした上流の libomp.dll
#   （Apache-2.0 WITH LLVM-exception）にする。Microsoft の libomp140 は再頒布できないので使わない（2.4）。
#   libomp 自体は clang-cl でビルドする（上流の想定どおり。C の ABI なので cl のコードから呼べる）。
#   Intel OpenMP と同じ KMP_BLOCKTIME が効くので PASSIVE は要らない。上流ソースへのパッチなし。
#
# 実行（ローカル・GitHub Actions 共通）:
#   powershell -ExecutionPolicy Bypass -File tools\signing\ct2_nomkl\build.ps1 -Python <python3.11.exe> [-OpenMP llvm]
# 必要なもの: Visual Studio 2022（「C++ によるデスクトップ開発」。-OpenMP llvm は「C++ Clang ツール」も）、
#             CMake 3.18 以上、git、tar（Windows 標準）、Python 3.11 x64。
# 出力: <OutDir>\ctranslate2-4.8.1+nomkl-cp311-cp311-win_amd64.whl と build-info.json、SHA256SUMS.txt
param(
    [Parameter(Mandatory = $true)][string]$Python,
    [string]$WorkDir = (Join-Path $PSScriptRoot "_build"),
    [string]$OutDir = (Join-Path $PSScriptRoot "dist"),
    [int]$Jobs = [Environment]::ProcessorCount,
    # 変換済みの CTranslate2 モデルのフォルダ（例: models_conv\fugumt-ja-en-ct2）。指定すると
    # 読み込んで1文訳し、実際に使われる行列演算の実装（MKL を使っていないこと）をログで確かめる。
    # CTranslate2 はこのログをモデルを読んだ時にしか出さないため、省略時は DLL の依存とシンボルだけで確かめる
    [string]$ProbeModel = "",
    # CI のキャッシュの鍵に使う値（MSVC の版）を表示して終わる
    [switch]$PrintToolchain,
    # vcomp: MSVC の vcomp140.dll（＋PASSIVE） / llvm: cl /openmp:llvm ＋ 上流 libomp.dll（試作）
    [ValidateSet("vcomp", "llvm")][string]$OpenMP = "vcomp"
)
$ErrorActionPreference = "Stop"

# ---- 固定する版（変えるときは docs/signing/CT2_NOMKL_BUILD.md の手順で） ----
$CT2_TAG = "v4.8.1"
$CT2_COMMIT = "0d8bcd362ac75ef860ef161d6f0efad0ae439ff0"
$ONEDNN_VERSION = "3.1.1"          # 公式 wheel の prepare_build_environment_windows.sh と同じ
$ONEDNN_SHA256 = "d1cd58f57b2308d362a9bc474acbc21524cb879cf5d8779b460c718f0fac82d6"
$PYBIND11_VERSION = "2.11.1"       # CTranslate2 の python/pyproject.toml と同じ
$LOCAL_VERSION = "nomkl"
# -OpenMP llvm で使う上流 libomp（LLVM 公式リリースの openmp / cmake ソース）
$LLVM_VERSION = "19.1.7"
$LLVM_OPENMP_SHA256 = "bd7e6901ab086fd268750363017935fd4a717c153dad3c2aab86cb0140d9e3fe"
$LLVM_CMAKE_SHA256 = "11c5a28f90053b0c43d0dec3d0ad579347fc277199c005206b963c19aae514e3"
$LIBOMP_CMAKE_OPTIONS = @(
    "-DCMAKE_BUILD_TYPE=Release",
    "-DLIBOMP_ENABLE_SHARED=ON",
    "-DLIBOMP_USE_ITT_NOTIFY=OFF",          # Intel VTune 向けの計測フック（不要）
    "-DLIBOMP_OMPT_SUPPORT=OFF",            # ツール向けインターフェース（不要）
    "-DLIBOMP_OMPD_SUPPORT=OFF",
    "-DOPENMP_ENABLE_LIBOMPTARGET=OFF",     # GPU オフロード（不要）
    "-DOPENMP_ENABLE_OMPT_TOOLS=OFF",
    "-DLIBOMP_INSTALL_ALIASES=OFF"          # libiomp5md.dll などの別名を作らない
)

$CT2_CMAKE_OPTIONS = @(
    "-DCMAKE_BUILD_TYPE=Release",
    "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
    "-DWITH_MKL=OFF",
    "-DWITH_DNNL=ON",
    "-DOPENMP_RUNTIME=COMP",           # コンパイラの OpenMP（MSVC なら vcomp140.dll）
    "-DENABLE_CPU_DISPATCH=ON",
    "-DWITH_CUDA=OFF",
    "-DWITH_CUDNN=OFF",
    "-DWITH_RUY=OFF",
    "-DBUILD_CLI=OFF",
    "-DBUILD_TESTS=OFF"
)
$ONEDNN_CMAKE_OPTIONS = @(
    "-DCMAKE_BUILD_TYPE=Release",
    "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",   # oneDNN 3.1.1 は古い cmake_minimum_required（CMake 4 で必要）
    "-DONEDNN_LIBRARY_TYPE=STATIC",
    "-DONEDNN_CPU_RUNTIME=OMP",
    "-DONEDNN_GPU_RUNTIME=NONE",
    "-DONEDNN_BUILD_EXAMPLES=OFF",
    "-DONEDNN_BUILD_TESTS=OFF",
    "-DONEDNN_ENABLE_WORKLOAD=INFERENCE",
    "-DONEDNN_ENABLE_PRIMITIVE=CONVOLUTION;REORDER",
    "-DONEDNN_BUILD_GRAPH=OFF"
)

$sw = [Diagnostics.Stopwatch]::StartNew()
$times = [ordered]@{}
function Step($name, [scriptblock]$body) {
    $t = [Diagnostics.Stopwatch]::StartNew()
    Write-Host "`n==== $name ====" -ForegroundColor Cyan
    & $body
    $times[$name] = [math]::Round($t.Elapsed.TotalSeconds)
    Write-Host ("---- {0}: {1:N0} 秒" -f $name, $t.Elapsed.TotalSeconds)
}
function Exec([string]$exe, [string[]]$argv) {
    & $exe @argv
    if ($LASTEXITCODE -ne 0) { throw "$exe が失敗しました（終了コード $LASTEXITCODE）" }
}
function Fetch([string]$url, [string]$path, [string]$sha256) {
    if (-not (Test-Path $path)) {
        Write-Host "取得: $url"
        # 進捗表示を出さない（pwsh の進捗表示はダウンロードを大きく遅くする）。止まったときは時間切れにする
        $pp = $ProgressPreference; $ProgressPreference = "SilentlyContinue"
        try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 600 -OutFile $path $url } finally { $ProgressPreference = $pp }
    }
    $h = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
    if ($h -ne $sha256) { Remove-Item $path; throw "SHA256 が違います: $url ($h)" }
    Write-Host ("  {0:N0} バイト・SHA256 一致" -f (Get-Item $path).Length)
}
# 展開は Windows 標準の tar（bsdtar）に固定する（PATH 次第で Git 付属の GNU tar が選ばれると、
# "D:\..." を「ホスト D のファイル」と解釈したりする）。
# .tar.xz（LLVM のソース）は Python の tarfile で展開する: GitHub Actions の windows-2022 では
# tar.exe が .xz の展開で止まった（手元の Windows 11 では数秒。2026-11-11 に2回）
$TAR = Join-Path $env:SystemRoot "System32\tar.exe"
function Extract([string]$archive, [string]$dest) {
    Write-Host "展開: $archive"
    if ($archive -like "*.xz") {
        Exec $Python @("-I", "-c",
            "import sys, tarfile; t = tarfile.open(sys.argv[1], 'r:xz'); t.extractall(sys.argv[2], filter='data'); t.close()",
            $archive, $dest)
    } else {
        Exec $TAR @("-xf", $archive, "-C", $dest)
    }
}

# ---- Visual Studio 2022 の開発環境を取り込む（cl / link / dumpbin / ninja） ----
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$need = @("Microsoft.VisualStudio.Component.VC.Tools.x86.x64")
if ($OpenMP -eq "llvm") { $need += "Microsoft.VisualStudio.Component.VC.Llvm.Clang" }
$vs = & $vswhere -version "[17.0,18.0)" -products * -latest -requires $need -property installationPath
if (-not $vs) { throw "Visual Studio 2022（C++ ツール$(if ($OpenMP -eq 'llvm') {'・C++ Clang ツール'})）が見つかりません" }
$msvcVer = (Get-Content (Join-Path $vs "VC\Auxiliary\Build\Microsoft.VCToolsVersion.default.txt")).Trim()
$redistVer = (Get-Content (Join-Path $vs "VC\Auxiliary\Build\Microsoft.VCRedistVersion.default.txt")).Trim()
$clangCl = Join-Path $vs "VC\Tools\Llvm\x64\bin\clang-cl.exe"
$clangVer = ""
if ($OpenMP -eq "llvm") {
    if (-not (Test-Path $clangCl)) { throw "clang-cl が見つかりません: $clangCl" }
    $clangVer = if ((& $clangCl --version | Select-Object -First 1) -match 'version (\S+)') { $Matches[1] } else { "?" }
}
if ($PrintToolchain) {
    # vcomp 版は従来どおりの値（既存のキャッシュをそのまま使う）。llvm 版は libomp と clang-cl の版も入れる
    if ($OpenMP -eq "llvm") { "msvc-$msvcVer-redist-$redistVer-libomp-$LLVM_VERSION-clang-$clangVer" }
    else { "msvc-$msvcVer-redist-$redistVer" }
    return
}
& cmd /c "`"$(Join-Path $vs 'VC\Auxiliary\Build\vcvars64.bat')`" >nul && set" | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') { Set-Item "Env:$($Matches[1])" $Matches[2] }
}
$dumpbin = (Get-Command dumpbin.exe).Source
$cl = (Get-Command cl.exe).Source
$ninja = Join-Path $vs "Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe"
if (-not (Test-Path $ninja)) { $ninja = (Get-Command ninja.exe).Source }
$GEN = @("-G", "Ninja", "-DCMAKE_MAKE_PROGRAM=$ninja", "-DCMAKE_C_COMPILER=$cl", "-DCMAKE_CXX_COMPILER=$cl")
$vcomp = Join-Path $vs "VC\Redist\MSVC\$redistVer\x64\Microsoft.VC143.OpenMP\vcomp140.dll"
if ($OpenMP -eq "vcomp" -and -not (Test-Path $vcomp)) { throw "vcomp140.dll が見つかりません: $vcomp" }
Write-Host "Visual Studio: $vs（MSVC $msvcVer / 再頒布 $redistVer）$(if ($clangVer) { " / clang-cl $clangVer" })"
Write-Host "OpenMP: $OpenMP"

New-Item -ItemType Directory -Force $WorkDir, $OutDir | Out-Null
$WorkDir = (Resolve-Path $WorkDir).Path
$OutDir = (Resolve-Path $OutDir).Path
$src = Join-Path $WorkDir "CTranslate2"
$dnnlSrc = Join-Path $WorkDir "oneDNN-$ONEDNN_VERSION"
# llvm 版はビルド先を分ける（同じ WorkDir で両方作っても混ざらないように）
$sfx = if ($OpenMP -eq "llvm") { "-llvm" } else { "" }
$dnnlPrefix = Join-Path $WorkDir "onednn-install$sfx"
$ct2Prefix = Join-Path $WorkDir "ct2-install$sfx"
$ompSrc = Join-Path $WorkDir "llvm-$LLVM_VERSION"
$ompPrefix = Join-Path $WorkDir "libomp-install"
$ompLibDir = Join-Path $ompPrefix "lib"
if ($OpenMP -eq "llvm") {
    $ompDll = Join-Path $ompPrefix "bin\libomp.dll"
    $ompName = "libomp.dll"
    $ompDesc = "LLVM OpenMP $LLVM_VERSION (libomp.dll, built from source with clang-cl $clangVer; code compiled with cl /openmp:llvm)"
    # oneDNN は find_package(OpenMP) で探すので、ここでビルドした libomp を教える
    $dnnlOmp = @("-DOpenMP_C_FLAGS=/openmp:llvm", "-DOpenMP_CXX_FLAGS=/openmp:llvm",
                 "-DOpenMP_C_LIB_NAMES=libomp", "-DOpenMP_CXX_LIB_NAMES=libomp",
                 "-DOpenMP_libomp_LIBRARY=$(Join-Path $ompLibDir 'libomp.lib')")
    # CTranslate2 は MSVC だと /openmp を直書きする。/openmp:llvm を足すと cl は llvm 形式を選ぶ。
    # リンクは上流の libomp.lib を先に指定する（cl が既定で付ける libomp.lib＝libomp140 より前に見つかる）
    $ct2Omp = @("-DCMAKE_CXX_FLAGS_INIT=/openmp:llvm", "-DCMAKE_C_FLAGS_INIT=/openmp:llvm",
                "-DCMAKE_SHARED_LINKER_FLAGS=/LIBPATH:$ompLibDir libomp.lib")
} else {
    $ompDll = $vcomp
    $ompName = "vcomp140.dll"
    $ompDesc = "MSVC OpenMP (vcomp140.dll, VC++ $redistVer)"
    $dnnlOmp = @()
    $ct2Omp = @()
}

Step "ソース取得" {
    if (-not (Test-Path $src)) {
        Exec git @("clone", "--quiet", "--depth", "1", "--branch", $CT2_TAG,
                   "https://github.com/OpenNMT/CTranslate2.git", $src)
        # 使うサブモジュールだけ（CPU の判別と、ログ出力）
        Exec git @("-C", $src, "submodule", "update", "--init", "--depth", "1",
                   "third_party/cpu_features", "third_party/spdlog")
    }
    $head = (& git -C $src rev-parse HEAD).Trim()
    if ($head -ne $CT2_COMMIT) { throw "CTranslate2 のコミットが違います: $head（期待 $CT2_COMMIT）" }
    # ソースに手が入っていないこと（wheel 作成で足す version.py と LICENSE* は除く）
    $dirty = & git -C $src status --porcelain --untracked-files=no -- . ":!python/ctranslate2/version.py"
    if ($dirty) { throw "CTranslate2 のソースが変更されています: $dirty" }
    if (-not (Test-Path $dnnlSrc)) {
        $tgz = Join-Path $WorkDir "onednn-$ONEDNN_VERSION.tar.gz"
        Fetch "https://github.com/oneapi-src/oneDNN/archive/refs/tags/v$ONEDNN_VERSION.tar.gz" $tgz $ONEDNN_SHA256
        Extract $tgz $WorkDir
    }
    if ($OpenMP -eq "llvm" -and -not (Test-Path (Join-Path $ompSrc "openmp"))) {
        # openmp/ の CMake は兄弟フォルダの cmake/（LLVM 共通モジュール）を参照する
        New-Item -ItemType Directory -Force $ompSrc | Out-Null
        $base = "https://github.com/llvm/llvm-project/releases/download/llvmorg-$LLVM_VERSION"
        foreach ($p in @(@("openmp", $LLVM_OPENMP_SHA256), @("cmake", $LLVM_CMAKE_SHA256))) {
            $xz = Join-Path $WorkDir "$($p[0])-$LLVM_VERSION.src.tar.xz"
            Fetch "$base/$($p[0])-$LLVM_VERSION.src.tar.xz" $xz $p[1]
            Extract $xz $ompSrc
            Rename-Item (Join-Path $ompSrc "$($p[0])-$LLVM_VERSION.src") $p[0]
        }
    }
}

if ($OpenMP -eq "llvm") {
    Step "libomp ビルド" {
        $b = Join-Path $WorkDir "libomp-build"
        $genClang = @("-G", "Ninja", "-DCMAKE_MAKE_PROGRAM=$ninja",
                      "-DCMAKE_C_COMPILER=$clangCl", "-DCMAKE_CXX_COMPILER=$clangCl")
        Exec cmake (@("-S", (Join-Path $ompSrc "openmp"), "-B", $b) + $genClang + @(
                      "-DCMAKE_ASM_MASM_COMPILER=ml64", "-DCMAKE_INSTALL_PREFIX=$ompPrefix") + $LIBOMP_CMAKE_OPTIONS)
        Exec cmake @("--build", $b, "--parallel", "$Jobs")
        Exec cmake @("--install", $b)
    }
}

Step "oneDNN ビルド" {
    $b = Join-Path $WorkDir "onednn-build$sfx"
    Exec cmake (@("-S", $dnnlSrc, "-B", $b) + $GEN + @("-DCMAKE_INSTALL_PREFIX=$dnnlPrefix") + $dnnlOmp + $ONEDNN_CMAKE_OPTIONS)
    Exec cmake @("--build", $b, "--parallel", "$Jobs")
    Exec cmake @("--install", $b)
}

Step "CTranslate2 ビルド" {
    $b = Join-Path $WorkDir "ct2-build$sfx"
    Exec cmake (@("-S", $src, "-B", $b) + $GEN + @("-DCMAKE_INSTALL_PREFIX=$ct2Prefix",
                  "-DCMAKE_PREFIX_PATH=$dnnlPrefix") + $ct2Omp + $CT2_CMAKE_OPTIONS)
    Exec cmake @("--build", $b, "--parallel", "$Jobs")
    Exec cmake @("--install", $b)
}

Step "wheel 作成" {
    $py = Join-Path $src "python"
    Copy-Item (Join-Path $src "README.md") $py -Force
    # ライセンス文を wheel の dist-info\licenses に入れる（setuptools は setup.py の隣の LICENSE* を
    # 自動で同梱する。PyInstaller の collect_all で配布物の _internal にも入る）。
    # oneDNN は ctranslate2.dll にバイナリで入るため（Apache-2.0 第4項）
    Get-ChildItem $py -Filter "LICENSE*" | Remove-Item -Force
    Copy-Item (Join-Path $src "LICENSE") (Join-Path $py "LICENSE") -Force
    Copy-Item (Join-Path $dnnlSrc "LICENSE") (Join-Path $py "LICENSE.oneDNN") -Force
    Copy-Item (Join-Path $dnnlSrc "THIRD-PARTY-PROGRAMS") (Join-Path $py "LICENSE.oneDNN-THIRD-PARTY-PROGRAMS") -Force
    if ($OpenMP -eq "llvm") {
        # libomp.dll をバイナリで再配布するため（Apache-2.0 第4項）
        Copy-Item (Join-Path $ompSrc "openmp\LICENSE.TXT") (Join-Path $py "LICENSE.LLVM-openmp") -Force
    }
    Get-ChildItem $py\ctranslate2 -Filter *.dll | Remove-Item -Force
    # setuptools の前回のビルド結果（build\lib.*\ctranslate2\*.dll）が wheel に混ざらないように消す
    Remove-Item -Recurse -Force (Join-Path $py "build"), (Join-Path $py "ctranslate2.egg-info") -ErrorAction SilentlyContinue
    Copy-Item (Join-Path $ct2Prefix "bin\ctranslate2.dll") (Join-Path $py "ctranslate2") -Force
    # OpenMP ランタイム（vcomp140.dll か libomp.dll）。公式 wheel が libiomp5md.dll を入れているのと同じ場所に
    # 置く（ctranslate2\__init__.py がこのフォルダの DLL を先に読み込む。VC++ 再頒布パッケージが
    # 入っていない PC でも読めるように）
    Copy-Item $ompDll (Join-Path $py "ctranslate2") -Force
    $verFile = Join-Path $py "ctranslate2\version.py"
    $ver = (Get-Content $verFile -Raw) -replace '__version__ = "([^"+]+)"', "__version__ = `"`$1+$LOCAL_VERSION`""
    Set-Content $verFile $ver -NoNewline -Encoding utf8
    $venv = Join-Path $WorkDir "wheel-venv"
    if (-not (Test-Path $venv)) { Exec $Python @("-m", "venv", $venv) }
    $vpy = Join-Path $venv "Scripts\python.exe"
    Exec $vpy @("-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                "pybind11==$PYBIND11_VERSION", "setuptools", "wheel")
    $env:CTRANSLATE2_ROOT = $ct2Prefix
    Get-ChildItem $OutDir -Filter "ctranslate2-*.whl" | Remove-Item -Force
    Exec $vpy @("-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--disable-pip-version-check",
                "-w", $OutDir, $py)
}

Step "確認" {
    $whl = Get-ChildItem $OutDir -Filter "ctranslate2-*.whl" | Select-Object -First 1
    if (-not $whl) { throw "wheel ができていません" }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($whl.FullName)
    $names = $zip.Entries | ForEach-Object { $_.FullName }
    $zip.Dispose()
    $lic = $names | Where-Object { $_ -match '\.dist-info/licenses/' } | ForEach-Object { Split-Path $_ -Leaf }
    Write-Host "wheel のライセンス文: $($lic -join ', ')"
    $wantLic = @("LICENSE", "LICENSE.oneDNN") + $(if ($OpenMP -eq "llvm") { @("LICENSE.LLVM-openmp") } else { @() })
    foreach ($l in $wantLic) { if ($lic -notcontains $l) { throw "wheel に $l がありません" } }
    $dlls = $names | Where-Object { $_ -match '\.dll$' }
    Write-Host "wheel の DLL: $($dlls -join ', ')"
    if ($dlls -match 'iomp|mkl|cudnn|libomp140') { throw "wheel に Intel OpenMP / MKL / cuDNN / libomp140 が入っています" }
    # OpenMP ランタイムはちょうど1つ（vcomp 版は vcomp140.dll、llvm 版は libomp.dll）
    $ompInWheel = @($dlls | Where-Object { $_ -match '/(vcomp140|libomp)\.dll$' } | ForEach-Object { Split-Path $_ -Leaf })
    if ($ompInWheel.Count -ne 1 -or $ompInWheel[0] -ne $ompName) {
        throw "wheel の OpenMP ランタイムが想定と違います（$ompName だけのはず）: $($ompInWheel -join ', ')"
    }

    $dll = Join-Path $ct2Prefix "bin\ctranslate2.dll"
    $deps = (& $dumpbin /nologo /dependents $dll) |
        Where-Object { $_ -match '^\s+\S+\.dll\s*$' } | ForEach-Object { $_.Trim() }
    Write-Host "ctranslate2.dll が読む DLL: $($deps -join ', ')"
    if ($deps -match 'iomp|mkl|libomp140') { throw "Intel の部品か libomp140（再頒布不可）に依存しています: $deps" }
    $depOmp = @($deps | Where-Object { $_ -match '^(vcomp\d*|libomp\S*)\.dll$' })
    if ($depOmp.Count -ne 1 -or $depOmp[0] -ne $ompName) {
        throw "ctranslate2.dll の OpenMP の依存が想定と違います（$ompName だけのはず）: $($depOmp -join ', ')"
    }
    $bin = [IO.File]::ReadAllText($dll, [Text.Encoding]::ASCII)
    if ($bin -match 'mkl_serv_|MKL_Get_Version|mkl_sgemm') { throw "MKL のシンボルが残っています" }

    # 素の venv に入れて読み込み、使われる実装を出させる
    $tvenv = Join-Path $WorkDir "test-venv"
    if (Test-Path $tvenv) { Remove-Item -Recurse -Force $tvenv }
    Exec $Python @("-m", "venv", $tvenv)
    $tpy = Join-Path $tvenv "Scripts\python.exe"
    Exec $tpy @("-m", "pip", "install", "--quiet", "--disable-pip-version-check", "numpy", $whl.FullName)
    $env:CT2_VERBOSE = "1"
    $env:CT2_PROBE_MODEL = $ProbeModel
    $out = & $tpy (Join-Path $PSScriptRoot "probe.py") 2>&1 | ForEach-Object { "$_" }
    $probeExit = $LASTEXITCODE
    Remove-Item Env:CT2_VERBOSE, Env:CT2_PROBE_MODEL
    $out | ForEach-Object { Write-Host "  $_" }
    if ($probeExit -ne 0) { throw "読み込み確認に失敗しました" }
    if ($ProbeModel -and -not ($out -match 'Use Intel MKL: false')) { throw "MKL 無効のログが出ません" }

    $info = [ordered]@{
        ctranslate2 = @{ tag = $CT2_TAG; commit = $CT2_COMMIT }
        onednn = @{ version = $ONEDNN_VERSION; sha256 = $ONEDNN_SHA256; cmake = $ONEDNN_CMAKE_OPTIONS }
        cmake = $CT2_CMAKE_OPTIONS
        openmp = $OpenMP
        openmp_runtime = $ompDesc
        compiler = "MSVC $msvcVer$(if ($OpenMP -eq 'llvm') { ' (/openmp:llvm)' })"
        msvc = $msvcVer
        visual_studio = (Split-Path $vs -Leaf)
        python = (& $Python -c "import sys; print(sys.version.split()[0])").Trim()
        pybind11 = $PYBIND11_VERSION
        ctranslate2_dll_dependents = $deps
        openmp_dll_sha256 = (Get-FileHash $ompDll -Algorithm SHA256).Hash.ToLower()
        wheel = $whl.Name
        wheel_sha256 = (Get-FileHash $whl.FullName -Algorithm SHA256).Hash.ToLower()
        built_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        seconds = $times
    }
    $info | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $OutDir "build-info.json") -Encoding utf8
    "$($info.wheel_sha256)  $($whl.Name)" | Set-Content (Join-Path $OutDir "SHA256SUMS.txt") -Encoding ascii
}

Write-Host ("`n完了（合計 {0:N0} 秒）: {1}" -f $sw.Elapsed.TotalSeconds, $OutDir) -ForegroundColor Green
$times.GetEnumerator() | ForEach-Object { "  {0}: {1} 秒" -f $_.Key, $_.Value }
