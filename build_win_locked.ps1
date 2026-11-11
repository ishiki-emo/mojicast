# Windows 版を CI（release-build-windows.yml）と同じ依存で手元ビルドする
#
#   .\build_win_locked.ps1                    # 翻訳エンジン（MKL なし版）もソースからビルドして使う
#   .\build_win_locked.ps1 -Ct2Wheel <whl>    # CI の artifact など、ビルド済みの wheel を使う
#
# やること:
#   1. .venv（Python 3.11.15）をロックファイル（.github\release\requirements-win.lock）どおりに揃える
#   2. CTranslate2 の MKL なし版（cl ＋ 上流 LLVM OpenMP）の wheel を用意して差し替える
#      （tools\signing\ct2_nomkl\build.ps1。2回目以降は前回のビルドを使い回すので速い）
#   3. PyInstaller → build_bundle.ps1 -NoModels → 翻訳エンジンの検査（check_bundle.ps1）
# このあとは今までどおり smoke_test.ps1 → make_release_zip.ps1 → make_installer.ps1。
#
# 開発用の reazonspeech-env では固めない（公式版の CTranslate2＝Intel MKL / Intel OpenMP 入りが入る。
# MKL なし版を入れると同じ環境の torch とぶつかって落ちる）。docs/signing/CT2_NOMKL_BUILD.md 4章
# 必要なもの: uv、pwsh（PowerShell 7）、Visual Studio 2022（C++ と C++ Clang ツール。-Ct2Wheel を渡すなら不要）
param([string]$Ct2Wheel = "")
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$PY_VERSION = "3.11.15"
function Exec([string]$exe, [string[]]$argv) {
    & $exe @argv
    if ($LASTEXITCODE -ne 0) { throw "$exe が失敗しました（終了コード $LASTEXITCODE）" }
}
foreach ($c in @("uv", "pwsh")) {
    if (-not (Get-Command $c -ErrorAction SilentlyContinue)) { throw "$c が見つかりません" }
}
Get-Process Mojicast -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$root\dist\*" } |
    ForEach-Object { throw "dist\Mojicast の Mojicast.exe が動いています。閉じてから実行してください" }
$sw = [Diagnostics.Stopwatch]::StartNew()

# ---- 1. ロックファイルの環境 ----
Write-Host "`n==== 依存（ロックファイル） ====" -ForegroundColor Cyan
$venv = Join-Path $root ".venv"
$vpy = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $vpy)) { Exec uv @("venv", $venv, "--python", $PY_VERSION) }
$have = (& $vpy -c "import sys; print(sys.version.split()[0])").Trim()
if ($have -ne $PY_VERSION) { throw ".venv の Python が $have です（$PY_VERSION のはず）。.venv を消してやり直してください" }
# sync は ctranslate2 を公式版に戻すので、必ず wheel の差し替えより先に
Exec uv @("pip", "sync", "--python", $venv, (Join-Path $root ".github\release\requirements-win.lock"), "--require-hashes")

# ---- 2. 翻訳エンジン（MKL なし版） ----
Write-Host "`n==== 翻訳エンジン（MKL なし版） ====" -ForegroundColor Cyan
if (-not $Ct2Wheel) {
    $out = Join-Path $root "tools\signing\ct2_nomkl\dist"
    $basePy = (& uv python find $PY_VERSION).Trim()
    Exec pwsh @("-NoProfile", "-File", (Join-Path $root "tools\signing\ct2_nomkl\build.ps1"), "-Python", $basePy, "-OutDir", $out)
    $Ct2Wheel = (Get-ChildItem $out -Filter "ctranslate2-*.whl" | Select-Object -First 1).FullName
}
$Ct2Wheel = (Resolve-Path $Ct2Wheel).Path
$info = Join-Path (Split-Path $Ct2Wheel) "build-info.json"
if (Test-Path $info) {
    $bi = Get-Content $info -Raw | ConvertFrom-Json
    if ($bi.openmp -ne "llvm") { throw "wheel の OpenMP が上流 LLVM OpenMP ではありません: $($bi.openmp)" }
    if ($bi.wheel_sha256 -and (Get-FileHash $Ct2Wheel -Algorithm SHA256).Hash.ToLower() -ne $bi.wheel_sha256) {
        throw "wheel の SHA256 が build-info.json と違います: $Ct2Wheel"
    }
    Write-Host "  $($bi.wheel)  OpenMP: $($bi.openmp_runtime)"
} else {
    Write-Warning "build-info.json が wheel の隣にありません（中身は check_bundle.ps1 で確かめます）"
}
Exec uv @("pip", "install", "--python", $venv, "--no-deps", "--reinstall", $Ct2Wheel)
$v = (& $vpy -c "import ctranslate2; print(ctranslate2.__version__)").Trim()
if (-not $v.EndsWith("+nomkl")) { throw "ctranslate2 が MKL なし版になっていません: $v" }
Write-Host "  ctranslate2 $v"

# ---- 3. 固める・検査 ----
Write-Host "`n==== PyInstaller ====" -ForegroundColor Cyan
# PyInstaller は dist\Mojicast を作り直すので、smoke_test 用に置いてあるモデル（models_conv は十数 GB）を
# 同じドライブの dist\ 直下へ退避して、終わったら戻す（移動なのですぐ終わる）
$app = Join-Path $root "dist\Mojicast"
$kept = @()
foreach ($d in @("models", "models_conv")) {
    $src = Join-Path $app $d
    if (Test-Path $src) {
        $tmp = Join-Path $root "dist\_keep_$d"
        if (Test-Path $tmp) { throw "前回の退避先が残っています: $tmp（中を確かめて dist\Mojicast\$d へ戻すか消してください）" }
        Move-Item $src $tmp
        $kept += $d
        Write-Host "  退避: $d"
    }
}
Push-Location $root
try {
    Exec (Join-Path $venv "Scripts\pyinstaller.exe") @("--noconfirm", "Mojicast.spec")
    Write-Host "`n==== build_bundle.ps1 -NoModels ====" -ForegroundColor Cyan
    & (Join-Path $root "build_bundle.ps1") -NoModels
    Write-Host "`n==== 翻訳エンジンの検査 ====" -ForegroundColor Cyan
    Exec pwsh @("-NoProfile", "-File", (Join-Path $root "tools\signing\ct2_nomkl\check_bundle.ps1"), "-App", $app)
} finally {
    Pop-Location
    foreach ($d in $kept) {
        New-Item -ItemType Directory -Force $app | Out-Null
        Move-Item (Join-Path $root "dist\_keep_$d") (Join-Path $app $d)
        Write-Host "  戻した: $d"
    }
}

Write-Host ("`n完了（{0:N0} 秒）: dist\Mojicast。次は smoke_test.ps1 → make_release_zip.ps1 → make_installer.ps1" -f $sw.Elapsed.TotalSeconds) -ForegroundColor Green
