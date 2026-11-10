# 配布物（PyInstaller ＋ build_bundle.ps1 の後の dist\Mojicast）に入った翻訳エンジンが、
# MKL なし版（cl ＋ 上流 LLVM OpenMP）として正しいかを確かめる。どれかに当たれば失敗する。
#
#   - _internal\ctranslate2\libomp.dll がある（配布物の中で libomp.dll はこの1つだけ）
#   - Intel OpenMP（libiomp5md）・Intel MKL・MSVC OpenMP（vcomp140）・Microsoft の libomp140・GNU OpenMP が無い
#   - ctranslate2.dll に Intel MKL の文字列が残っていない
#   - ライセンス文（CTranslate2・oneDNN・LLVM OpenMP）が ctranslate2 の dist-info\licenses にある
#   - 同梱の CREDITS.md が MKL なし版の記載（LLVM OpenMP・oneDNN あり、Intel oneMKL・libiomp5md なし）
#
# release-build-windows.yml の「Sanity check」と、手元ビルドの確認（docs/signing/CT2_NOMKL_BUILD.md 4章）で使う。
#   pwsh -File tools\signing\ct2_nomkl\check_bundle.ps1 -App dist\Mojicast
param([string]$App = "dist\Mojicast")
$ErrorActionPreference = "Stop"
$App = (Resolve-Path $App).Path
$ct2 = Join-Path $App "_internal\ctranslate2"
$errors = @()
$files = Get-ChildItem $App -Recurse -File

# OpenMP ランタイム: 上流 libomp が ctranslate2 の隣にちょうど1つ
$omp = @($files | Where-Object { $_.Name -ieq "libomp.dll" })
if ($omp.Count -ne 1 -or $omp[0].DirectoryName -ne $ct2) {
    $errors += "libomp.dll が _internal\ctranslate2 に1つだけ、になっていません: $($omp.FullName -join ', ')"
} else {
    Write-Host ("  libomp.dll: {0:N0} バイト・SHA256 {1}" -f $omp[0].Length, (Get-FileHash $omp[0].FullName -Algorithm SHA256).Hash.ToLower())
}

# 入っていてはいけないもの（Intel のもの・他の OpenMP）
$bad = @($files | Where-Object { $_.Name -match '(?i)iomp|mkl_|^mkl|vcomp\d*\.dll$|libomp140|libgomp' })
foreach ($f in $bad) { $errors += "入っていてはいけないファイル: $($f.FullName.Substring($App.Length + 1))" }

# ctranslate2.dll に MKL が静的リンクされていない（公式版はこの文字列を含む）
$dll = Join-Path $ct2 "ctranslate2.dll"
if (-not (Test-Path $dll)) {
    $errors += "ctranslate2.dll がありません"
} else {
    $bin = [IO.File]::ReadAllText($dll, [Text.Encoding]::ASCII)
    foreach ($s in @("Math Kernel Library", "mkl_serv_", "MKL_Get_Version")) {
        if ($bin.Contains($s)) { $errors += "ctranslate2.dll に MKL の文字列が残っています: $s" }
    }
}

# ライセンス文（Apache-2.0 第4項: バイナリで再頒布するものはライセンス文を添える）
$distInfo = @(Get-ChildItem (Join-Path $App "_internal") -Directory -Filter "ctranslate2-*.dist-info")
if ($distInfo.Count -ne 1) {
    $errors += "ctranslate2 の dist-info が1つではありません: $($distInfo.Name -join ', ')"
} else {
    if ($distInfo[0].Name -notmatch '\+nomkl\.dist-info$') { $errors += "ctranslate2 が MKL なし版（+nomkl）ではありません: $($distInfo[0].Name)" }
    $lic = Join-Path $distInfo[0].FullName "licenses"
    foreach ($l in @("LICENSE", "LICENSE.oneDNN", "LICENSE.oneDNN-THIRD-PARTY-PROGRAMS", "LICENSE.LLVM-openmp")) {
        $p = Join-Path $lic $l
        if (-not (Test-Path $p) -or (Get-Item $p).Length -eq 0) { $errors += "ライセンス文がありません: $($distInfo[0].Name)\licenses\$l" }
    }
    if (Test-Path (Join-Path $lic "LICENSE.LLVM-openmp")) {
        $t = Get-Content (Join-Path $lic "LICENSE.LLVM-openmp") -Raw
        if ($t -notmatch 'Apache License' -or $t -notmatch 'LLVM Exceptions') { $errors += "LICENSE.LLVM-openmp の中身が LLVM のライセンス文ではありません" }
    }
    if (Test-Path (Join-Path $lic "LICENSE.oneDNN")) {
        $t = Get-Content (Join-Path $lic "LICENSE.oneDNN") -Raw
        if ($t -notmatch 'Apache License') { $errors += "LICENSE.oneDNN の中身が Apache-2.0 ではありません" }
    }
}

# 同梱の CREDITS.md の記載が中身と合っている
$credits = Join-Path $App "CREDITS.md"
if (-not (Test-Path $credits)) {
    $errors += "CREDITS.md がありません"
} else {
    $c = Get-Content $credits -Raw -Encoding UTF8
    foreach ($w in @("LLVM OpenMP", "oneDNN")) { if (-not $c.Contains($w)) { $errors += "CREDITS.md に $w がありません" } }
    foreach ($w in @("Intel oneMKL", "libiomp5md")) { if ($c.Contains($w)) { $errors += "CREDITS.md に公式版の記載（$w）が残っています" } }
}

if ($errors) {
    $errors | ForEach-Object { Write-Host "  ✗ $_" -ForegroundColor Red }
    throw "翻訳エンジンの確認に失敗しました（$($errors.Count) 件）"
}
Write-Host "  [OK] 翻訳エンジンは MKL なし版（上流 LLVM OpenMP）・ライセンス文あり" -ForegroundColor Green
