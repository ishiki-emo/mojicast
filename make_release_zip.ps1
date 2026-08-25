# Mojicast 配布Zipの作成
#
# dist\Mojicast\ を配布用のZipに固める。実行中に生成された物を必ず取り除いてから
# 固めるのが目的。手で Compress-Archive すると、直前にアプリを起動しただけで
# models\（数百MB）・data\（ユーザー設定）・logs\ が紛れ込む。
#
#   .\make_release_zip.ps1            # バージョンは app_server.py から取る
#   .\make_release_zip.ps1 -Version 0.9.6
#
# models\ は消さずに退避して戻す（消すと次の動作確認で数GB再DLになるため）。
# data\ と logs\ は実行時に再生成されるので削除でよい。
param([string]$Version)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$app  = Join-Path $root "dist\Mojicast"
if (-not (Test-Path (Join-Path $app "Mojicast.exe"))) {
    throw "dist\Mojicast\Mojicast.exe がありません。先に pyinstaller → build_bundle.ps1 を実行してください"
}

if (-not $Version) {
    $m = Select-String -Path (Join-Path $root "app_server.py") -Pattern 'APP_VERSION = "([^"]+)"'
    if (-not $m) { throw "app_server.py から APP_VERSION を読めません" }
    $Version = $m.Matches[0].Groups[1].Value
}
$zip = Join-Path $root "Mojicast-v$Version-win-x64.zip"

# 起動中だと data\ や logs\ を書き続けるので先に止める
$proc = Get-Process Mojicast -EA SilentlyContinue
if ($proc) { Write-Host "  Mojicast を停止 (PID $($proc.Id))"; $proc | Stop-Process -Force; Start-Sleep -Seconds 2 }

# models\ … 実行/スモークで取得した本番モデル
# models_conv\ … smoke_test.ps1 がルートからコピーする変換済みモデル。調査で試した
#   候補モデルも入っていて数十GBに育つことがある（19GBで Compress-Archive が
#   "Stream was too long" で落ちた）。どちらも消さずに退避して戻す（再取得を避ける）
$holds = @()
try {
    foreach ($name in @("models", "models_conv")) {
        $src = Join-Path $app $name
        if (Test-Path $src) {
            $dst = Join-Path $root "dist\_hold_$name"
            if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
            Move-Item $src $dst -Force
            $holds += ,@($dst, $src)
            Write-Host "  $name\ を一時退避（Zipには含めない）"
        }
    }
    foreach ($x in @("data", "logs", "__pycache__")) {
        $p = Join-Path $app $x
        if (Test-Path $p) { Remove-Item $p -Recurse -Force; Write-Host "  除去: $x\" }
    }
    Get-ChildItem $app -Filter *.log -File -EA SilentlyContinue |
        ForEach-Object { Remove-Item $_.FullName -Force; Write-Host "  除去: $($_.Name)" }

    if (Test-Path $zip) { Remove-Item $zip -Force }
    Compress-Archive -Path $app -DestinationPath $zip -CompressionLevel Optimal
}
finally {
    # 例外で落ちても必ず戻す（再取得・再変換をさせない）
    foreach ($h in $holds) {
        if (Test-Path $h[0]) {
            Move-Item $h[0] $h[1] -Force
            Write-Host "  $(Split-Path $h[1] -Leaf)\ を戻しました"
        }
    }
}

# --- 検証: 実行時生成物が紛れていないか ---
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($zip)
try {
    $names = $archive.Entries | ForEach-Object { $_.FullName }
    $bad = $names | Where-Object {
        $_ -like "Mojicast/models/*" -or $_ -like "Mojicast/models_conv/*" -or
        $_ -like "Mojicast/data/*" -or
        $_ -like "Mojicast/logs/*" -or $_ -like "*__pycache__*" -or $_ -like "*.log"
    }
} finally { $archive.Dispose() }

$mb = (Get-Item $zip).Length / 1MB
Write-Host ""
if ($bad) {
    Write-Host "★ 実行時生成物が混入しています:" -ForegroundColor Red
    $bad | Select-Object -First 8 | ForEach-Object { Write-Host "    $_" }
    throw "Zipに混入あり（$zip）"
}
"完成: {0}  ({1:N0} MB / {2} エントリ)" -f (Split-Path $zip -Leaf), $mb, $names.Count
