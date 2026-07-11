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
Remove-Item "$app\config.json","$app\translate_error.log" -Force -EA SilentlyContinue
if ($Fresh) {
    Note "[Fresh] models を削除 → 実ダウンロードで検証します（約2GB）"
    Remove-Item "$app\models" -Recurse -Force -EA SilentlyContinue
} else {
    Note "[Fast] ローカルHFキャッシュからモデルを複製します"
    $hubSrc = Join-Path $env:USERPROFILE ".cache\huggingface\hub"
    $hubDst = "$app\models\hub"
    New-Item -ItemType Directory -Force $hubDst | Out-Null
    foreach ($m in @("models--reazon-research--reazonspeech-k2-v2",
                     "models--tohoku-nlp--bert-base-japanese-char-v3",
                     "models--bobfromjapan--bert_japanese_punctuation",
                     "models--staka--fugumt-ja-en")) {
        if (-not (Test-Path "$hubDst\$m")) { Copy-Item "$hubSrc\$m" "$hubDst\$m" -Recurse -Force }
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
    foreach ($ep in @("/api/hotwords","/api/banned","/api/presets","/api/boxes")) {
        try { Invoke-RestMethod "http://127.0.0.1:8765$ep" -TimeoutSec 5 | Out-Null; Note "  [OK] $ep" }
        catch { $fail += "$ep が応答しない" }
    }

    try { Invoke-RestMethod "http://127.0.0.1:8765/api/engine" -Method Post -Body '{"action":"stop"}' -ContentType "application/json" -TimeoutSec 5 | Out-Null } catch {}
}

# --- 片付け（実行中に seed されたデータファイルも除去＝配布物をコード資産のみに保つ）---
Start-Sleep -Seconds 2
Get-Process Mojicast,msedgewebview2 -EA SilentlyContinue | Stop-Process -Force
foreach ($f in @("config.json","logs","hotwords.txt","effects.json","presets.json",
                 "boxes.json","banned.txt","glossary.txt","_hotwords_gen.txt","translate_error.log")) {
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
