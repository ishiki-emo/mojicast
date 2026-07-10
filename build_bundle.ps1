# Mojicast 配布パッケージ組み立てスクリプト
# PyInstaller ビルド後に実行し、モデル・アセット・設定を dist\Mojicast\ 直下へ配置する。
#
#   .\reazonspeech-env\Scripts\pyinstaller.exe --noconfirm Mojicast.spec
#   .\build_bundle.ps1              # 同梱版（モデル込み・約3.1GB・完全オフライン）
#   .\build_bundle.ps1 -NoModels    # 軽量版（モデル無し・約1GB・初回起動時にDL）
#
param([switch]$NoModels)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$app  = Join-Path $root "dist\Mojicast"
if (-not (Test-Path (Join-Path $app "Mojicast.exe"))) {
    throw "先に PyInstaller ビルドを実行してください（$app が見つかりません）"
}

# --- アセット / 既定データファイルを app 直下へ（config.json は同梱しない＝初回に既定生成）---
# defaults\ にあるファイルは汎用版を優先（個人用の単語帳等を配布物に入れない）
$assets = @("overlay.html", "silero_vad.onnx",
            "presets.json", "boxes.json", "effects.json", "hotwords.txt",
            "banned.txt")
foreach ($f in $assets) {
    $generic = Join-Path $root "defaults\$f"
    $src = if (Test-Path $generic) { $generic } else { Join-Path $root $f }
    Copy-Item $src (Join-Path $app $f) -Force
    Write-Host ("  asset : $f" + $(if ($src -eq $generic) { "  (汎用版)" }))
}
Copy-Item (Join-Path $root "ui") (Join-Path $app "ui") -Recurse -Force
Write-Host "  asset : ui\"

# --- ドキュメント ---
foreach ($f in @("README_TESTER.txt", "マニュアル.html")) {
    Copy-Item (Join-Path $root $f) $app -Force
    Write-Host "  doc   : $f"
}

# --- モデルを HF キャッシュ構造で配置（-NoModels 指定時はスキップ＝初回起動時にDL）---
if ($NoModels) {
    Write-Host "  model : 同梱なし（軽量版。初回起動時に exe隣 models\ へ自動DL）"
} else {
    $hubSrc = Join-Path $env:USERPROFILE ".cache\huggingface\hub"
    $hubDst = Join-Path $app "models\hub"
    New-Item -ItemType Directory -Force $hubDst | Out-Null
    $models = @(
        "models--reazon-research--reazonspeech-k2-v2",       # ASR (k2)
        "models--tohoku-nlp--bert-base-japanese-char-v3",    # 句読点BERTの土台
        "models--bobfromjapan--bert_japanese_punctuation",   # 句読点の重み
        "models--staka--fugumt-ja-en"                        # 英訳 (FuguMT JA→EN)
    )
    foreach ($m in $models) {
        $src = Join-Path $hubSrc $m
        if (-not (Test-Path $src)) { throw "モデルが見つかりません: $src（先に一度アプリを起動してDLしてください）" }
        Write-Host "  model : $m をコピー中..."
        Copy-Item $src (Join-Path $hubDst $m) -Recurse -Force
    }
}

$size = [math]::Round((Get-ChildItem $app -Recurse -File | Measure-Object Length -Sum).Sum/1GB, 2)
Write-Host ""
Write-Host "完成: $app  (合計 ${size} GB)"
