# Mojicast アプリ全体（本体＋GUI窓のWebView2）のメモリを実測する
#
# bench_memory.py が測るのは推論プロセス側のモデル分だけ。利用者のタスク
# マネージャに出る値は GUI窓の WebView2 子プロセス群を足したものなので、
# こちらは起動中のプロセスツリーを丸ごとサンプリングする。
#
# 使い方（Mojicast を起動した状態で PowerShell から）:
#   .\bench\mem_watch.ps1                        # 60秒・1秒間隔
#   .\bench\mem_watch.ps1 -Seconds 300 -Csv mem.csv
#
# 出力: 本体/GUI窓ごとの 平均・最小・ピーク 作業セットと、アプリ全体の合計。
param(
    [int]$Seconds = 60,
    [double]$Interval = 1.0,
    [string]$Csv = ""
)

# 本体プロセス: 配布版(Mojicast.exe) / 開発起動(pythonw.exe app.py)
# 子孫（WebView2 等）は親子関係をたどって拾う（他アプリの WebView2 を誤計上しない）
function Get-Targets {
    $all = Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, Name, CommandLine
    $roots = $all | Where-Object {
        $_.Name -eq "Mojicast.exe" -or
        ($_.Name -like "python*.exe" -and $_.CommandLine -like "*app.py*")
    }
    if (-not $roots) { return @() }
    $ids = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($r in $roots) { [void]$ids.Add([int]$r.ProcessId) }
    for ($i = 0; $i -lt 6; $i++) {     # 子→孫…と数段たどる（WebView2は2段）
        $added = $false
        foreach ($p in $all) {
            if ($ids.Contains([int]$p.ParentProcessId) -and
                $ids.Add([int]$p.ProcessId)) { $added = $true }
        }
        if (-not $added) { break }
    }
    return Get-Process -Id $ids -ErrorAction SilentlyContinue
}

$targets = Get-Targets
if (-not $targets) {
    Write-Host "Mojicast が見つかりません。アプリを起動してから実行してください。"
    Write-Host "（開発起動なら .\Mojicast.bat、配布版なら dist\Mojicast\Mojicast.exe）"
    exit 1
}
Write-Host ("対象プロセス: " + (($targets | Group-Object Name |
    ForEach-Object { "$($_.Name)x$($_.Count)" }) -join ", "))
Write-Host ("{0} 秒間 / {1} 秒間隔でサンプリングします..." -f $Seconds, $Interval)

$rows = @()
$deadline = (Get-Date).AddSeconds($Seconds)
while ((Get-Date) -lt $deadline) {
    $stamp = (Get-Date).ToString("HH:mm:ss")
    $procs = Get-Targets
    if (-not $procs) { Write-Host "アプリが終了しました。集計します。"; break }
    foreach ($p in $procs) {
        $kind = if ($p.Name -like "msedgewebview2*") { "GUI窓(WebView2)" }
                elseif ($p.Name -like "python*" -or $p.Name -eq "Mojicast") { "本体(認識・モデル)" }
                else { "その他(" + $p.Name + ")" }
        $rows += [pscustomobject]@{
            time    = $stamp
            proc_id = $p.Id
            name    = $p.Name
            kind    = $kind
            ws_mb   = [math]::Round($p.WorkingSet64 / 1MB, 1)
            priv_mb = [math]::Round($p.PrivateMemorySize64 / 1MB, 1)
        }
    }
    $total = ($rows | Where-Object { $_.time -eq $stamp } |
        Measure-Object ws_mb -Sum).Sum
    Write-Host ("  {0}  合計 {1,7:N0} MB  ({2} プロセス)" -f $stamp, $total, $procs.Count)
    Start-Sleep -Seconds $Interval
}

if (-not $rows) { Write-Host "サンプルが取れませんでした。"; exit 1 }

Write-Host ""
Write-Host "===== 種別ごと（作業セット MB） ====="
$rows | Group-Object kind, time | ForEach-Object {
    [pscustomobject]@{
        kind = $_.Group[0].kind
        time = $_.Group[0].time
        ws   = ($_.Group | Measure-Object ws_mb -Sum).Sum
    }
} | Group-Object kind | ForEach-Object {
    $s = $_.Group | Measure-Object ws -Average -Maximum -Minimum
    "{0,-20} 平均 {1,7:N0}  最小 {2,7:N0}  ピーク {3,7:N0}" -f
        $_.Name, $s.Average, $s.Minimum, $s.Maximum
}

Write-Host ""
Write-Host "===== アプリ全体（本体＋GUI窓の合計 MB） ====="
$totals = $rows | Group-Object time | ForEach-Object {
    ($_.Group | Measure-Object ws_mb -Sum).Sum
}
$t = $totals | Measure-Object -Average -Maximum -Minimum
"平均 {0,7:N0}  最小 {1,7:N0}  ピーク {2,7:N0}  （{3} サンプル）" -f
    $t.Average, $t.Minimum, $t.Maximum, $totals.Count

if ($Csv) {
    $rows | Export-Csv -Path $Csv -NoTypeInformation -Encoding UTF8
    Write-Host ("生データ: " + (Resolve-Path $Csv))
}
