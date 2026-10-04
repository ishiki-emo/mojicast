# Mojicast インストーラ（setup.exe）の作成
#
# dist\Mojicast\ を Inno Setup で setup.exe にまとめる。Zip 版（make_release_zip.ps1）と
# 並べて配布する想定（Zip はポータブル版として残す）。
#
#   .\make_installer.ps1            # バージョンは app_server.py から取る
#   .\make_installer.ps1 -Version 0.9.9
#
# 実行時に生成される data\ logs\ models\ は installer\Mojicast.iss の Excludes で
# 除外するので、スモークテスト直後の dist でもそのまま作れる（退避は不要）。
# 前提: Inno Setup 6（winget install JRSoftware.InnoSetup）
param([string]$Version)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$app  = Join-Path $root "dist\Mojicast"
if (-not (Test-Path (Join-Path $app "Mojicast.exe"))) {
    throw "dist\Mojicast\Mojicast.exe がありません。先に pyinstaller → build_bundle.ps1 -NoModels を実行してください"
}
if (-not (Test-Path (Join-Path $app "ui\cockpit.html"))) {
    throw "アセット未配置です。build_bundle.ps1 -NoModels を実行してください"
}

if (-not $Version) {
    $m = Select-String -Path (Join-Path $root "app_server.py") -Pattern 'APP_VERSION = "([^"]+)"'
    if (-not $m) { throw "app_server.py から APP_VERSION を読めません" }
    $Version = $m.Matches[0].Groups[1].Value
}

$iscc = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 が見つかりません（winget install JRSoftware.InnoSetup）" }

$iss = Join-Path $root "installer\Mojicast.iss"
& $iscc /Qp "/DAppVersion=$Version" "/DSourceDir=$app" "/DOutputDir=$root" $iss
if ($LASTEXITCODE -ne 0) { throw "ISCC が失敗しました（終了コード $LASTEXITCODE）" }

$out = Join-Path $root "Mojicast-v$Version-win-x64-setup.exe"
$mb = [math]::Round((Get-Item $out).Length / 1MB, 1)
Write-Host ""
Write-Host "完成: $(Split-Path $out -Leaf)  ($mb MB)"
