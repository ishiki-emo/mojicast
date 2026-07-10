@echo off
chcp 65001 >nul
rem ダウンロードしたZipを展開すると、Windowsが中のファイルを「インターネット由来」として
rem ブロックし、Mojicast.exe が起動できないことがあります（画面に .NET/clr のエラー）。
rem このバッチは、このフォルダ内の全ファイルのブロックを解除します。一度だけ実行してください。
echo Mojicast: ダウンロード由来のブロック(MOTW)を解除します...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0' -Recurse -File | Unblock-File"
echo.
echo 完了しました。Mojicast.exe をダブルクリックで起動できます。
pause
