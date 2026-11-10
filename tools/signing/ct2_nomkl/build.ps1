# CTranslate2 を Intel MKL・Intel OpenMP なしでビルドし、Python の wheel を作る（Windows x64）。
#
# 公式の PyPI wheel（ctranslate2 4.8.1）との違い。翻訳のコードは同じ v4.8.1:
#   - WITH_MKL=OFF        … Intel oneMKL（非 OSI）を静的リンクしない。行列演算は公式でも使っている
#                           oneDNN 3.1.1（Apache-2.0・静的リンク）だけになる
#   - OpenMP ランタイム   … Intel OpenMP（libiomp5md.dll・非 OSI）の代わりに、LLVM の OpenMP
#                           ランタイム（libomp.dll・Apache-2.0 WITH LLVM-exception）をソースからビルドして使う。
#                           libomp は Intel OpenMP がオープンソース化されたもので、Mojicast が使っている
#                           KMP_BLOCKTIME（計算後の空回り時間）がそのまま効く
#   - コンパイラ          … clang-cl（Visual Studio 同梱の LLVM）。MSVC の cl は OpenMP を vcomp140.dll
#                           向けにしか出力できないため（-OpenMP msvc で cl＋vcomp140.dll も作れる。比較用）
#   - WITH_CUDA=OFF       … Mojicast は CPU だけで動くので GPU 部分を入れない（cudnn64_9.dll も入らない）
#   - wheel の版に +nomkl を付ける（`ctranslate2==4.8.1` の指定はそのまま満たす）
# CPU の自動判別（ENABLE_CPU_DISPATCH）は公式と同じく有効。詳しくは docs/signing/CT2_NOMKL_BUILD.md。
#
# 実行（ローカル・GitHub Actions 共通）:
#   powershell -ExecutionPolicy Bypass -File tools\signing\ct2_nomkl\build.ps1 -Python <python3.11.exe>
# 必要なもの: Visual Studio 2022（「C++ によるデスクトップ開発」と「C++ Clang ツール」）、CMake 3.18 以上、
#             git、tar（Windows 標準）、Python 3.11 x64。
# 出力: <OutDir>\ctranslate2-4.8.1+nomkl-cp311-cp311-win_amd64.whl と build-info.json、SHA256SUMS.txt
param(
    [Parameter(Mandatory = $true)][string]$Python,
    [string]$WorkDir = (Join-Path $PSScriptRoot "_build"),
    [string]$OutDir = (Join-Path $PSScriptRoot "dist"),
    [int]$Jobs = [Environment]::ProcessorCount,
    # llvm: clang-cl＋libomp.dll（既定・採用） / msvc: cl＋vcomp140.dll（比較用。計算後の空回りを止められない）
    [ValidateSet("llvm", "msvc")][string]$OpenMP = "llvm",
    # 変換済みの CTranslate2 モデルのフォルダ（例: models_conv\fugumt-ja-en-ct2）。指定すると
    # 読み込んで1文訳し、実際に使われる行列演算の実装（MKL を使っていないこと）をログで確かめる。
    # CTranslate2 はこのログをモデルを読んだ時にしか出さないため、省略時は DLL の依存とシンボルだけで確かめる
    [string]$ProbeModel = ""
)
$ErrorActionPreference = "Stop"

# ---- 固定する版（変えるときは docs/signing/CT2_NOMKL_BUILD.md の手順で） ----
$CT2_TAG = "v4.8.1"
$CT2_COMMIT = "0d8bcd362ac75ef860ef161d6f0efad0ae439ff0"
$ONEDNN_VERSION = "3.1.1"          # 公式 wheel の prepare_build_environment_windows.sh と同じ
$ONEDNN_SHA256 = "d1cd58f57b2308d362a9bc474acbc21524cb879cf5d8779b460c718f0fac82d6"
$LLVM_VERSION = "19.1.7"           # libomp のソース（LLVM 公式リリースの openmp / cmake ソース）
$LLVM_OPENMP_SHA256 = "bd7e6901ab086fd268750363017935fd4a717c153dad3c2aab86cb0140d9e3fe"
$LLVM_CMAKE_SHA256 = "11c5a28f90053b0c43d0dec3d0ad579347fc277199c005206b963c19aae514e3"
$PYBIND11_VERSION = "2.11.1"       # CTranslate2 の python/pyproject.toml と同じ
$LOCAL_VERSION = "nomkl"

$CT2_CMAKE_OPTIONS = @(
    "-DCMAKE_BUILD_TYPE=Release",
    "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
    "-DWITH_MKL=OFF",
    "-DWITH_DNNL=ON",
    "-DOPENMP_RUNTIME=COMP",           # コンパイラの OpenMP（clang-cl なら libomp）
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
$LIBOMP_CMAKE_OPTIONS = @(
    "-DCMAKE_BUILD_TYPE=Release",
    "-DLIBOMP_ENABLE_SHARED=ON",
    "-DLIBOMP_USE_ITT_NOTIFY=OFF",          # Intel VTune 向けの計測フック（不要。MSVC の STL では通らない）
    "-DLIBOMP_OMPT_SUPPORT=OFF",            # ツール向けインターフェース（不要）
    "-DLIBOMP_OMPD_SUPPORT=OFF",
    "-DOPENMP_ENABLE_LIBOMPTARGET=OFF",     # GPU オフロード（不要）
    "-DOPENMP_ENABLE_OMPT_TOOLS=OFF",
    "-DLIBOMP_INSTALL_ALIASES=OFF"
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
    if (-not (Test-Path $path)) { Invoke-WebRequest -UseBasicParsing -OutFile $path $url }
    $h = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
    if ($h -ne $sha256) { Remove-Item $path; throw "SHA256 が違います: $url ($h)" }
}

# ---- Visual Studio 2022 の開発環境を取り込む（cl / link / ml64 / clang-cl / ninja） ----
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$need = @("-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64")
if ($OpenMP -eq "llvm") { $need += "Microsoft.VisualStudio.Component.VC.Llvm.Clang" }
$vs = & $vswhere -version "[17.0,18.0)" -products * -latest @need -property installationPath
if (-not $vs) { throw "Visual Studio 2022（C++ ツール$(if ($OpenMP -eq 'llvm') {'・C++ Clang ツール'})）が見つかりません" }
$msvcVer = (Get-Content (Join-Path $vs "VC\Auxiliary\Build\Microsoft.VCToolsVersion.default.txt")).Trim()
$redistVer = (Get-Content (Join-Path $vs "VC\Auxiliary\Build\Microsoft.VCRedistVersion.default.txt")).Trim()
& cmd /c "`"$(Join-Path $vs 'VC\Auxiliary\Build\vcvars64.bat')`" >nul && set" | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') { Set-Item "Env:$($Matches[1])" $Matches[2] }
}
$dumpbin = (Get-Command dumpbin.exe).Source
$ninja = Join-Path $vs "Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe"
if (-not (Test-Path $ninja)) { $ninja = (Get-Command ninja.exe).Source }
if ($OpenMP -eq "llvm") {
    $cc = Join-Path $vs "VC\Tools\Llvm\x64\bin\clang-cl.exe"
    $ccVer = ((& $cc --version) | Select-Object -First 1).Trim()
} else {
    $cc = (Get-Command cl.exe).Source
    $ccVer = "MSVC $msvcVer"
}
$GEN = @("-G", "Ninja", "-DCMAKE_MAKE_PROGRAM=$ninja", "-DCMAKE_C_COMPILER=$cc", "-DCMAKE_CXX_COMPILER=$cc")
# clang は既定で a*b+c を FMA 命令にまとめる（-ffp-contract=on）。MSVC（公式 wheel）はまとめないので、
# 丸めが変わって訳文が数 % 変わる。公式と同じ計算にそろえるため止める（CMake は初回の構成時に
# 環境変数 CFLAGS / CXXFLAGS を既定のフラグに足す）
$EXTRA_FLAGS = if ($OpenMP -eq "llvm") { "/clang:-ffp-contract=off" } else { "" }
$env:CFLAGS = $EXTRA_FLAGS
$env:CXXFLAGS = $EXTRA_FLAGS
Write-Host "Visual Studio: $vs（MSVC $msvcVer / 再頒布 $redistVer）"
Write-Host "コンパイラ: $ccVer"

New-Item -ItemType Directory -Force $WorkDir, $OutDir | Out-Null
$WorkDir = (Resolve-Path $WorkDir).Path
$OutDir = (Resolve-Path $OutDir).Path
$src = Join-Path $WorkDir "CTranslate2"
$dnnlSrc = Join-Path $WorkDir "oneDNN-$ONEDNN_VERSION"
$ompSrc = Join-Path $WorkDir "llvm-$LLVM_VERSION"
$ompPrefix = Join-Path $WorkDir "libomp-install"
$dnnlPrefix = Join-Path $WorkDir "onednn-install-$OpenMP"
$ct2Prefix = Join-Path $WorkDir "ct2-install-$OpenMP"

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
    if (-not (Test-Path $dnnlSrc)) {
        $tgz = Join-Path $WorkDir "onednn-$ONEDNN_VERSION.tar.gz"
        Fetch "https://github.com/oneapi-src/oneDNN/archive/refs/tags/v$ONEDNN_VERSION.tar.gz" $tgz $ONEDNN_SHA256
        Exec tar @("-xzf", $tgz, "-C", $WorkDir)
    }
    if ($OpenMP -eq "llvm" -and -not (Test-Path $ompSrc)) {
        # openmp/ の CMake は兄弟フォルダの cmake/（LLVM 共通モジュール）を参照する
        New-Item -ItemType Directory -Force $ompSrc | Out-Null
        $base = "https://github.com/llvm/llvm-project/releases/download/llvmorg-$LLVM_VERSION"
        foreach ($p in @(@("openmp", $LLVM_OPENMP_SHA256), @("cmake", $LLVM_CMAKE_SHA256))) {
            $xz = Join-Path $WorkDir "$($p[0])-$LLVM_VERSION.src.tar.xz"
            Fetch "$base/$($p[0])-$LLVM_VERSION.src.tar.xz" $xz $p[1]
            Exec tar @("-xf", $xz, "-C", $ompSrc)
            Rename-Item (Join-Path $ompSrc "$($p[0])-$LLVM_VERSION.src") $p[0]
        }
    }
}

$ompHints = @()
if ($OpenMP -eq "llvm") {
    Step "libomp ビルド" {
        $b = Join-Path $WorkDir "libomp-build"
        Exec cmake (@("-S", (Join-Path $ompSrc "openmp"), "-B", $b) + $GEN + @(
                      "-DCMAKE_ASM_MASM_COMPILER=ml64", "-DCMAKE_INSTALL_PREFIX=$ompPrefix") + $LIBOMP_CMAKE_OPTIONS)
        Exec cmake @("--build", $b, "--parallel", "$Jobs")
        Exec cmake @("--install", $b)
    }
    # oneDNN は find_package(OpenMP) で探すので、ここでビルドした libomp を教える
    $lib = Join-Path $ompPrefix "lib\libomp.lib"
    $ompHints = @("-DOpenMP_C_FLAGS=/openmp", "-DOpenMP_CXX_FLAGS=/openmp",
                  "-DOpenMP_C_LIB_NAMES=libomp", "-DOpenMP_CXX_LIB_NAMES=libomp",
                  "-DOpenMP_libomp_LIBRARY=$lib")
    # CTranslate2 は MSVC 系のコンパイラだと /openmp を付けるだけで、ランタイムのリンクを
    # コンパイラ任せにする（cl なら vcomp が自動で付く）。clang-cl は付けないので明示する
    $ct2OmpLink = @("-DCMAKE_SHARED_LINKER_FLAGS=/LIBPATH:$(Join-Path $ompPrefix 'lib') libomp.lib")
} else {
    $ct2OmpLink = @()
}

Step "oneDNN ビルド" {
    $b = Join-Path $WorkDir "onednn-build-$OpenMP"
    Exec cmake (@("-S", $dnnlSrc, "-B", $b) + $GEN + @("-DCMAKE_INSTALL_PREFIX=$dnnlPrefix") +
                $ONEDNN_CMAKE_OPTIONS + $ompHints)
    Exec cmake @("--build", $b, "--parallel", "$Jobs")
    Exec cmake @("--install", $b)
}

Step "CTranslate2 ビルド" {
    $b = Join-Path $WorkDir "ct2-build-$OpenMP"
    Exec cmake (@("-S", $src, "-B", $b) + $GEN + @("-DCMAKE_INSTALL_PREFIX=$ct2Prefix",
                  "-DCMAKE_PREFIX_PATH=$dnnlPrefix") + $CT2_CMAKE_OPTIONS + $ct2OmpLink)
    Exec cmake @("--build", $b, "--parallel", "$Jobs")
    Exec cmake @("--install", $b)
}

if ($OpenMP -eq "llvm") {
    $ompDll = Join-Path $ompPrefix "bin\libomp.dll"
    $ompName = "libomp.dll"
    $ompDesc = "LLVM OpenMP $LLVM_VERSION (libomp.dll, built from source with $ccVer)"
} else {
    $ompDll = Join-Path $vs "VC\Redist\MSVC\$redistVer\x64\Microsoft.VC143.OpenMP\vcomp140.dll"
    $ompName = "vcomp140.dll"
    $ompDesc = "MSVC OpenMP (vcomp140.dll, VC++ $redistVer)"
}
if (-not (Test-Path $ompDll)) { throw "OpenMP ランタイムが見つかりません: $ompDll" }

Step "wheel 作成" {
    $py = Join-Path $src "python"
    Copy-Item (Join-Path $src "README.md") $py -Force
    # ライセンス文を wheel の dist-info\licenses に入れる（setuptools は setup.py の隣の LICENSE* を
    # 自動で同梱する。PyInstaller の collect_all で配布物の _internal にも入る）。
    # oneDNN と libomp は ctranslate2.dll / libomp.dll としてバイナリで再配布するため（Apache-2.0 第4項）
    Get-ChildItem $py -Filter "LICENSE*" | Remove-Item -Force
    Copy-Item (Join-Path $src "LICENSE") (Join-Path $py "LICENSE") -Force
    Copy-Item (Join-Path $dnnlSrc "LICENSE") (Join-Path $py "LICENSE.oneDNN") -Force
    Copy-Item (Join-Path $dnnlSrc "THIRD-PARTY-PROGRAMS") (Join-Path $py "LICENSE.oneDNN-THIRD-PARTY-PROGRAMS") -Force
    if ($OpenMP -eq "llvm") {
        Copy-Item (Join-Path $ompSrc "openmp\LICENSE.TXT") (Join-Path $py "LICENSE.LLVM-openmp") -Force
    }
    Get-ChildItem $py\ctranslate2 -Filter *.dll | Remove-Item -Force
    # setuptools の前回のビルド結果（build\lib.*\ctranslate2\*.dll）が wheel に混ざらないように消す
    Remove-Item -Recurse -Force (Join-Path $py "build"), (Join-Path $py "ctranslate2.egg-info") -ErrorAction SilentlyContinue
    Copy-Item (Join-Path $ct2Prefix "bin\ctranslate2.dll") (Join-Path $py "ctranslate2") -Force
    # OpenMP ランタイム。公式 wheel が libiomp5md.dll を入れているのと同じ場所に置く
    # （ctranslate2\__init__.py がこのフォルダの DLL を先に読み込む）
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
    $lic = $zip.Entries | Where-Object { $_.FullName -match '\.dist-info/licenses/' } | ForEach-Object { $_.Name }
    $zip.Dispose()
    Write-Host "wheel のライセンス文: $($lic -join ', ')"
    $wantLic = @("LICENSE", "LICENSE.oneDNN") + $(if ($OpenMP -eq "llvm") { "LICENSE.LLVM-openmp" } else { @() })
    foreach ($l in $wantLic) { if ($lic -notcontains $l) { throw "wheel に $l がありません" } }
    $dll = Join-Path $ct2Prefix "bin\ctranslate2.dll"
    $deps = (& $dumpbin /nologo /dependents $dll) |
        Where-Object { $_ -match '^\s+\S+\.dll\s*$' } | ForEach-Object { $_.Trim() }
    Write-Host "ctranslate2.dll が読む DLL: $($deps -join ', ')"
    if ($deps -match 'iomp|mkl') { throw "Intel の部品に依存しています: $deps" }
    if (-not ($deps -match "^$([regex]::Escape($ompName))$")) { throw "$ompName に依存していません（OpenMP が無効？）" }
    if ($OpenMP -eq "llvm" -and ($deps -match '^vcomp')) { throw "vcomp にも依存しています（OpenMP が2つ混在）" }
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
    $files = ($out -match '^files ') -join ''
    if ($files -match 'libiomp5md|cudnn|mkl') { throw "wheel に Intel OpenMP / cuDNN / MKL が入っています" }
    $otherOmp = @("libomp.dll", "vcomp140.dll") | Where-Object { $_ -ne $ompName }
    if ($files -notmatch [regex]::Escape($ompName) -or $files -match [regex]::Escape($otherOmp)) {
        throw "wheel の OpenMP ランタイムが想定と違います（$ompName だけのはず）: $files"
    }

    $info = [ordered]@{
        ctranslate2 = @{ tag = $CT2_TAG; commit = $CT2_COMMIT }
        onednn = @{ version = $ONEDNN_VERSION; sha256 = $ONEDNN_SHA256
                    cmake = $ONEDNN_CMAKE_OPTIONS + $ompHints.ForEach({ $_ -replace [regex]::Escape($WorkDir), '<work>' }) }
        cmake = $CT2_CMAKE_OPTIONS + $ct2OmpLink.ForEach({ $_ -replace [regex]::Escape($WorkDir), '<work>' })
        openmp = $OpenMP
        openmp_runtime = $ompDesc
        libomp = $(if ($OpenMP -eq "llvm") { @{ version = $LLVM_VERSION; openmp_src_sha256 = $LLVM_OPENMP_SHA256;
                    cmake_src_sha256 = $LLVM_CMAKE_SHA256; cmake = $LIBOMP_CMAKE_OPTIONS;
                    dll_sha256 = (Get-FileHash $ompDll -Algorithm SHA256).Hash.ToLower() } } else { $null })
        compiler = $ccVer
        extra_cflags = $EXTRA_FLAGS
        msvc = $msvcVer
        visual_studio = (Split-Path $vs -Leaf)
        python = (& $Python -c "import sys; print(sys.version.split()[0])").Trim()
        pybind11 = $PYBIND11_VERSION
        ctranslate2_dll_dependents = $deps
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
