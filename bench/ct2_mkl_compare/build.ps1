# 実機に持っていく比較ツール（ct2_mkl_compare）を作る。
# 実行: powershell -ExecutionPolicy Bypass -File bench\ct2_mkl_compare\build.ps1 [-CustomWheel <whl>]
#   -Python      : ビルドに使う python（既定: メイン作業フォルダの reazonspeech-env。3.11）
#   -LogsRoot    : 文を抜き出す配信ログのあるフォルダ（logs\ の親）
#   -CustomWheel : 自前ビルドの wheel（tools\signing\ct2_nomkl\build.ps1 の出力）。指定すると
#                  Zip の ct2_custom\ に展開して入れ、公式ビルドと並べて測る（同じフォルダの
#                  build-info.json も一緒に入れる）。省略時は公式ビルドの MKL あり/なしだけ
# 出力: bench\ct2_mkl_compare\dist\ct2_mkl_compare-win-x64.zip
#   ※ Zip には配信の書き起こし（sentences.txt・えもさん本人の配信）が入る。ベータテスターに配ってよい
param(
    [string]$Python = "E:\AIProject\ReazonSpeech\reazonspeech-env\Scripts\python.exe",
    [string]$LogsRoot = "E:\AIProject\ReazonSpeech",
    [int]$Sentences = 200,
    [string]$CustomWheel = ""
)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$repo = (Resolve-Path (Join-Path $here "..\..")).Path
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
    if ($CustomWheel) {
        # wheel は zip。中の ctranslate2\ と *.dist-info\ をそのまま置く（compare.py が優先して読む）
        $dst = Join-Path $app "ct2_custom"
        New-Item -ItemType Directory -Force $dst | Out-Null
        $tmp = Join-Path $here "build\custom.zip"
        Copy-Item $CustomWheel $tmp
        Expand-Archive $tmp -DestinationPath $dst -Force
        $bi = Join-Path (Split-Path $CustomWheel) "build-info.json"
        if (Test-Path $bi) { Copy-Item $bi $dst }
        Write-Host "自前ビルドを同梱: $CustomWheel"
    }
    $zip = Join-Path $here "dist\ct2_mkl_compare-win-x64.zip"
    Compress-Archive -Path $app -DestinationPath $zip -Force
    "{0}  {1:N0} MB" -f $zip, ((Get-Item $zip).Length / 1MB)
} finally {
    Pop-Location
}
