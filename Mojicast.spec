# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller ビルド定義 — Mojicast（onedir・完全オフライン配布用）

ビルド:
    .\reazonspeech-env\Scripts\pyinstaller.exe --noconfirm Mojicast.spec

生成物: dist\Mojicast\  (Mojicast.exe + _internal\)
ビルド後に build_bundle.ps1 が models/ とアセット/設定ファイルを app 直下へコピーする。
"""
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []

# ネイティブ拡張やデータファイルを丸ごと取り込む重量級パッケージ群
# ※ fugashi / unidic_lite(MeCab辞書 248MB) は実行時に未使用のため意図的に除外
#   （句読点は文字トークナイザ、ASRもMeCab不使用。実推論で未ロードを確認済み）
for pkg in (
    "torch", "transformers", "tokenizers", "safetensors", "huggingface_hub",
    "sherpa_onnx", "sounddevice",
    "webview", "pythonnet", "clr_loader", "reazonspeech",
):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# 動的 import で拾い漏れやすいもの
hiddenimports += [
    "clr",                       # pythonnet（pywebview の WinForms/WebView2 バックエンド）
    "bottle", "proxy_tools",     # pywebview の内部依存
    "reazonspeech.k2.asr",       # ネームスペースパッケージの明示
    "sherpa_onnx",
]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # ビルドを軽く・速くするため未使用の大物を除外
    # fugashi/unidic_lite/MeCab は実行時不要（transformersは無くても正常動作）
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6",
              "pytest", "IPython", "notebook",
              "fugashi", "unidic_lite", "MeCab"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Mojicast",
    debug=False,
    strip=False,
    upx=False,
    console=False,          # ウィンドウアプリ（コンソール窓を出さない）
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name="Mojicast",
)
