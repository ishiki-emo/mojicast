@echo off
rem Mojicast 起動用ランチャー
cd /d "%~dp0"
start "" "%~dp0reazonspeech-env\Scripts\pythonw.exe" app.py
