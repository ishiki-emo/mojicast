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
# ※ torch / transformers は排除済み（句読点=onnxruntime / 翻訳=ctranslate2）
# ※ reazonspeech はラッパー2関数を engine にインライン化して排除
#   （librosa→sklearn/scipy の玉突き同梱で約600ファイル膨らんでいたため）
# ※ onnxruntime は標準フックで足りる（collect_all だと training/tools まで入る）
for pkg in (
    "ctranslate2", "sentencepiece", "huggingface_hub",
    "sherpa_onnx", "sounddevice",
    "webview", "pythonnet", "clr_loader",
):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# ビルド用の残骸（インポートライブラリ等・実行時不要）を落とす
datas = [x for x in datas if not x[0].lower().endswith((".lib", ".pdb", ".exp"))]
binaries = [x for x in binaries if not x[0].lower().endswith((".lib", ".pdb", ".exp"))]

# 動的 import で拾い漏れやすいもの
hiddenimports += [
    "clr",                       # pythonnet（pywebview の WinForms/WebView2 バックエンド）
    "bottle", "proxy_tools",     # pywebview の内部依存
    "onnxruntime",               # 句読点（punct.py）が使用・標準フックでDLL収集
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
    # torch/transformers は実行時不要（ONNX/CT2移行済み。混入したら失敗させる）
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6",
              "pytest", "IPython", "notebook",
              "torch", "transformers", "tokenizers", "safetensors",
              "fugashi", "unidic_lite", "MeCab",
              # reazonspeech(k2)排除に伴い不要になった科学計算系の玉突き依存
              "reazonspeech", "librosa", "sklearn", "scipy", "soundfile",
              "numba", "llvmlite", "audioread", "pooch", "joblib",
              "threadpoolctl", "lazy_loader", "msgpack"],
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
