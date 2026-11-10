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

# --- 翻訳エンジン（ctranslate2）がどちらの版か。CREDITS.md は MKL なし版（LLVM OpenMP）で書いてある。
#     公式版（Intel oneMKL / Intel OpenMP 入り）のままビルドすると記載と中身がずれるので知らせる
#     （切り替え・戻し方: docs/signing/CT2_NOMKL_BUILD.md） ---
$ct2 = Join-Path $app "_internal\ctranslate2"
if (Test-Path (Join-Path $ct2 "libiomp5md.dll")) {
    Write-Warning ("ctranslate2 が公式版（Intel MKL / Intel OpenMP 入り）です。CREDITS.md の記載（MKL なし版）と" +
                   "合いません。MKL なし版の wheel を reazonspeech-env に入れてから PyInstaller をやり直してください" +
                   "（docs/signing/CT2_NOMKL_BUILD.md）")
} elseif (Test-Path (Join-Path $ct2 "libomp.dll")) {
    Write-Host "  ctranslate2 : MKL なし版（LLVM OpenMP）"
}

# --- コード資産のみ app 直下へ。データファイル(hotwords等)はルートに置かず、
#     defaults\ を同梱して初回起動時に seed_defaults が生成する。
#     → 既存インストールへの上書きアップデートでユーザーデータが潰れない ---
foreach ($f in @("overlay.html", "silero_vad.onnx")) {
    Copy-Item (Join-Path $root $f) (Join-Path $app $f) -Force
    Write-Host "  asset : $f"
}
Remove-Item (Join-Path $app "defaults") -Recurse -Force -EA SilentlyContinue
Copy-Item (Join-Path $root "defaults") (Join-Path $app "defaults") -Recurse -Force
Write-Host "  asset : defaults\  (初回起動時にデータファイルを生成)"
# 既存フォルダへの Copy-Item -Recurse は「中へ入れ子コピー」になり、再実行時に
# 古いファイルが残る（defaults と同じく先に消してから丸ごとコピーする）
Remove-Item (Join-Path $app "ui") -Recurse -Force -EA SilentlyContinue
Copy-Item (Join-Path $root "ui") (Join-Path $app "ui") -Recurse -Force
Write-Host "  asset : ui\"

# --- ドキュメント / ヘルパー ---
# CREDITS.md は第三者ライブラリ/モデルのライセンス表記（Apache/MIT/BSD の attribution 義務・
# FuguMT の CC BY-SA など）を配布物側でも満たすために同梱する。
foreach ($f in @("README_TESTER.txt", "マニュアル.html", "ブロック解除.bat", "CREDITS.md", "LICENSE")) {
    Copy-Item (Join-Path $root $f) $app -Force
    Write-Host "  doc   : $f"
}
Remove-Item (Join-Path $app "ガイド") -Recurse -Force -EA SilentlyContinue
Copy-Item (Join-Path $root "ガイド") (Join-Path $app "ガイド") -Recurse -Force
Write-Host "  doc   : ガイド\  (スタイルガイド / エフェクトガイド)"
# MCP サーバ（AI から字幕を調整する・試作）。本体には組み込まず uv で別に起動する。
# .lock は依存の版とハッシュを固定するもの（uv が隣にあれば自動で使う）
Remove-Item (Join-Path $app "mcp") -Recurse -Force -EA SilentlyContinue
New-Item -ItemType Directory (Join-Path $app "mcp") | Out-Null
foreach ($f in @("mojicast_mcp.py", "mojicast_mcp.py.lock", "README.md")) {
    Copy-Item (Join-Path $root "tools\mcp\$f") (Join-Path $app "mcp\$f") -Force
}
Write-Host "  tool  : mcp\  (MCP サーバ・試作)"

# --- モデルを配置（-NoModels 指定時はスキップ＝初回起動時にDL）---
# ASR: HFキャッシュ構造 / 句読点・翻訳: 変換済みモデル (models_conv\, tools\convert_models.py で生成)
if ($NoModels) {
    Write-Host "  model : 同梱なし（軽量版。初回起動時に exe隣 models\ へ自動DL）"
} else {
    $hubSrc = Join-Path $env:USERPROFILE ".cache\huggingface\hub"
    $hubDst = Join-Path $app "models\hub"
    New-Item -ItemType Directory -Force $hubDst | Out-Null
    $m = "models--reazon-research--reazonspeech-k2-v2"       # ASR (k2)
    $src = Join-Path $hubSrc $m
    if (-not (Test-Path $src)) { throw "モデルが見つかりません: $src（先に一度アプリを起動してDLしてください）" }
    Write-Host "  model : $m をコピー中..."
    Copy-Item $src (Join-Path $hubDst $m) -Recurse -Force

    $conv = Join-Path $root "models_conv"
    if (-not (Test-Path (Join-Path $conv "punct\punct_bert.int8.onnx"))) {
        throw "変換済みモデルがありません。先に tools\convert_models.py を実行してください"
    }
    Write-Host "  model : models_conv\ (句読点ONNX + 翻訳CT2) をコピー中..."
    Copy-Item $conv (Join-Path $app "models_conv") -Recurse -Force
    # 句読点の fp32(364MB) は同梱しない。既定は int8 で、高精度モードを
    # 選んだ人だけが初回に追加DLする（同梱版のサイズを増やさないため）
    Remove-Item (Join-Path $app "models_conv\punct\punct_bert.onnx") -Force -EA SilentlyContinue
}

$size = [math]::Round((Get-ChildItem $app -Recurse -File | Measure-Object Length -Sum).Sum/1GB, 2)
Write-Host ""
Write-Host "完成: $app  (合計 ${size} GB)"
