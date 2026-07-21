# Mojicast リリース前スモークテスト
#
# ビルド済みの dist\Mojicast\ を実際に起動し、新規ユーザー相当の動作を自動検証する。
# dev では起きずリリース版でだけ起きる不具合（凍結環境の挙動差・新規DL経路の故障）を
# 配布前に検出するためのもの。
#
#   .\smoke_test.ps1           # 高速: モデルをローカルHFキャッシュから複製して検証（約2分）
#   .\smoke_test.ps1 -Fresh    # 完全: モデル無しから実DL（約2GB）して検証 ＝新規ユーザー再現
#
# 判定基準:
#   1) exe起動でHTTPサーバが応答する（凍結importの成功）
#   2) 英訳ONで engine start → running に到達する
#   3) 状態詳細に「失敗」が含まれない（句読点/英訳の自己診断が全て通った）
#   4) 主要APIが応答する
# ※句読点・翻訳は各モジュールのロード時自己診断（語彙サイズ/試訳）で検証される。
param([switch]$Fresh)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$app  = Join-Path $root "dist\Mojicast"
$fail = @()

function Note($m) { Write-Host $m }

if (-not (Test-Path "$app\Mojicast.exe")) { throw "dist\Mojicast\Mojicast.exe がありません。先に pyinstaller → build_bundle.ps1 -NoModels を実行してください" }
if (-not (Test-Path "$app\ui\cockpit.html")) { throw "アセット未配置です。build_bundle.ps1 -NoModels を実行してください" }

# --- モデル準備 ---
Get-Process Mojicast,msedgewebview2 -EA SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
Remove-Item "$app\data","$app\translate_error.log" -Recurse -Force -EA SilentlyContinue
if ($Fresh) {
    # 注意: Fresh は句読点/翻訳の変換済みモデルを HF リポジトリ（punct.py/translate.py の
    # _REPO_ID）から実DLする。リポジトリへのアップロードが済んでいないと失敗する。
    Note "[Fresh] models を削除 → 実ダウンロードで検証します（約1.2GB）"
    Remove-Item "$app\models" -Recurse -Force -EA SilentlyContinue
    Remove-Item "$app\models_conv" -Recurse -Force -EA SilentlyContinue
} else {
    Note "[Fast] ローカルのモデルを複製します（ASR=HFキャッシュ / 句読点・翻訳=models_conv）"
    $hubSrc = Join-Path $env:USERPROFILE ".cache\huggingface\hub"
    $hubDst = "$app\models\hub"
    New-Item -ItemType Directory -Force $hubDst | Out-Null
    $m = "models--reazon-research--reazonspeech-k2-v2"
    if (-not (Test-Path "$hubDst\$m")) { Copy-Item "$hubSrc\$m" "$hubDst\$m" -Recurse -Force }
    if (-not (Test-Path "$app\models_conv\punct\punct_bert.onnx")) {
        if (-not (Test-Path "$root\models_conv\punct\punct_bert.onnx")) {
            throw "models_conv がありません。先に tools\convert_models.py を実行してください"
        }
        Copy-Item "$root\models_conv" "$app\models_conv" -Recurse -Force
    }
}

# --- 起動 → サーバ応答 ---
Note "起動中..."
Start-Process "$app\Mojicast.exe" -WorkingDirectory $app
$server = $false
foreach ($i in 1..30) {
    Start-Sleep -Seconds 1
    try { Invoke-RestMethod "http://127.0.0.1:8765/api/config" -TimeoutSec 3 | Out-Null; $server = $true; break } catch {}
}
if ($server) { Note "  [OK] HTTPサーバ応答（凍結import成功）" } else { $fail += "サーバが起動しない" }

if ($server) {
    # --- 英訳ON・句読点ON（既定）で開始 → running 到達と自己診断 ---
    Invoke-RestMethod "http://127.0.0.1:8765/api/config" -Method Post -Body '{"translate":true,"punctuate":true}' -ContentType "application/json" | Out-Null
    Invoke-RestMethod "http://127.0.0.1:8765/api/engine" -Method Post -Body '{"action":"start"}' -ContentType "application/json" | Out-Null
    $limit = if ($Fresh) { 300 } else { 90 }   # Fresh はDL時間を見込む
    $state = ""; $detail = ""; $died = $false
    foreach ($i in 1..$limit) {
        Start-Sleep -Seconds 2
        if (-not (Get-Process Mojicast -EA SilentlyContinue)) { $died = $true; break }
        try { $s = Invoke-RestMethod "http://127.0.0.1:8765/api/status" -TimeoutSec 5 } catch { continue }
        $state = $s.state; $detail = $s.detail
        if ($state -in @("running","error")) { break }
    }
    if ($died) {
        $fail += "テスト中にプロセスが終了した（※テスト中はMojicastの窓を閉じないでください）"
    } elseif ($state -eq "running") { Note "  [OK] engine running 到達" }
    else { $fail += "running に到達しない (state=$state detail=$detail)" }
    if ($detail -match "失敗") { $fail += "自己診断の警告あり: $detail" } else { Note "  [OK] 自己診断の警告なし（句読点/英訳ロード健全）" }

    # --- 主要API ---
    foreach ($ep in @("/api/hotwords","/api/banned","/api/presets","/api/boxes","/api/profiles","/api/env-suggest")) {
        try { Invoke-RestMethod "http://127.0.0.1:8765$ep" -TimeoutSec 5 | Out-Null; Note "  [OK] $ep" }
        catch { $fail += "$ep が応答しない" }
    }

    # --- 多言語経路（v0.5.0: SenseVoice認識＋M2M-100翻訳のDL・ロード検証） ---
    Note "多言語経路を検証中（未DLならここで 約240MB+470MB のDLが入ります）..."
    try { Invoke-RestMethod "http://127.0.0.1:8765/api/engine" -Method Post -Body '{"action":"stop"}' -ContentType "application/json" -TimeoutSec 10 | Out-Null } catch {}
    Start-Sleep -Seconds 3
    Invoke-RestMethod "http://127.0.0.1:8765/api/config" -Method Post -Body '{"asr_model":"sensevoice","asr_lang":"zh","translate_lang":"en"}' -ContentType "application/json" | Out-Null
    Invoke-RestMethod "http://127.0.0.1:8765/api/engine" -Method Post -Body '{"action":"start"}' -ContentType "application/json" | Out-Null
    $state = ""; $detail = ""
    foreach ($i in 1..$limit) {
        Start-Sleep -Seconds 2
        if (-not (Get-Process Mojicast -EA SilentlyContinue)) { $fail += "多言語検証中にプロセスが終了した"; break }
        try { $s = Invoke-RestMethod "http://127.0.0.1:8765/api/status" -TimeoutSec 5 } catch { continue }
        $state = $s.state; $detail = $s.detail
        if ($state -in @("running","error")) { break }
    }
    if ($state -eq "running") { Note "  [OK] 多言語（SenseVoice＋中→英翻訳）running 到達" }
    else { $fail += "多言語経路で running に到達しない (state=$state detail=$detail)" }
    if ($detail -match "失敗") { $fail += "多言語経路の自己診断警告: $detail" }
    # 設定を既定へ戻す（dataは後段で削除されるが、検証順の独立性のため明示的に）
    try { Invoke-RestMethod "http://127.0.0.1:8765/api/config" -Method Post -Body '{"asr_model":"k2-ja","asr_lang":"auto","translate_lang":"en"}' -ContentType "application/json" -TimeoutSec 5 | Out-Null } catch {}

    try { Invoke-RestMethod "http://127.0.0.1:8765/api/engine" -Method Post -Body '{"action":"stop"}' -ContentType "application/json" -TimeoutSec 5 | Out-Null } catch {}
}

# --- 片付け（実行中に seed されたデータファイルも除去＝配布物をコード資産のみに保つ）---
Start-Sleep -Seconds 2
Get-Process Mojicast,msedgewebview2 -EA SilentlyContinue | Stop-Process -Force
foreach ($f in @("data","logs","translate_error.log")) {
    Remove-Item "$app\$f" -Recurse -Force -EA SilentlyContinue
}

Write-Host ""
if ($fail.Count -eq 0) {
    Write-Host "=== SMOKE TEST: PASS ===" -ForegroundColor Green
    Write-Host "（配布するなら models/ を削除してから Zip すること: build手順参照）"
} else {
    Write-Host "=== SMOKE TEST: FAIL ===" -ForegroundColor Red
    $fail | ForEach-Object { Write-Host "  ✗ $_" -ForegroundColor Red }
    exit 1
}
