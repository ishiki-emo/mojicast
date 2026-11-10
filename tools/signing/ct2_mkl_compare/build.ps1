# Intel 機に持っていく比較ツール（ct2_mkl_compare）を作る。
# 実行: powershell -ExecutionPolicy Bypass -File tools\signing\ct2_mkl_compare\build.ps1
#   -Python   : ビルドに使う python（既定: メイン作業フォルダの reazonspeech-env）
#   -LogsRoot : 文を抜き出す配信ログのあるフォルダ（logs\ の親）
# 出力: tools\signing\ct2_mkl_compare\dist\ct2_mkl_compare-win-x64.zip
#   ※ Zip には配信の書き起こし（sentences.txt）が入る。自分の機械以外に配らない
param(
    [string]$Python = "E:\AIProject\ReazonSpeech\reazonspeech-env\Scripts\python.exe",
    [string]$LogsRoot = "E:\AIProject\ReazonSpeech",
    [int]$Sentences = 200
)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$repo = (Resolve-Path (Join-Path $here "..\..\..")).Path
Push-Location $here
try {
    Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
    & $Python -m PyInstaller --noconfirm --clean --console --name ct2_mkl_compare `
        --paths $repo `
        --hidden-import translate --hidden-import apppaths `
        --collect-all ctranslate2 --collect-all sentencepiece `
        --collect-all huggingface_hub --collect-all opencc `
        --exclude-module torch --exclude-module transformers `
        compare.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller が失敗しました" }
    $app = Join-Path $here "dist\ct2_mkl_compare"
    & $Python make_sentences.py $LogsRoot (Join-Path $app "sentences.txt") $Sentences
    if ($LASTEXITCODE -ne 0) { throw "文の抜き出しに失敗しました" }
    Copy-Item (Join-Path $here "README.md") (Join-Path $app "README.md")
    $zip = Join-Path $here "dist\ct2_mkl_compare-win-x64.zip"
    Compress-Archive -Path $app -DestinationPath $zip -Force
    "{0}  {1:N0} MB" -f $zip, ((Get-Item $zip).Length / 1MB)
} finally {
    Pop-Location
}
