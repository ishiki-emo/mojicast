# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller ビルド定義 — Mojicast（onedir・Windows / macOS 両対応）

ビルド:
    Windows:  .\\reazonspeech-env\\Scripts\\pyinstaller.exe --noconfirm Mojicast.spec
    macOS:    .venv/bin/pyinstaller --noconfirm Mojicast.spec

生成物:
    Windows:  dist\\Mojicast\\  (Mojicast.exe + _internal\\)
              → build_bundle.ps1 が models/ とアセットを app 直下へ配置
    macOS:    dist/Mojicast.app
              → build_bundle.sh がアセットを Contents/Resources へ配置し dmg 化
"""
import os
import re
import sys

from PyInstaller.utils.hooks import collect_all

IS_MAC = sys.platform == "darwin"

# バージョンは app_server.APP_VERSION を正とする（import せず正規表現で読む）
with open(os.path.join(SPECPATH, "app_server.py"), encoding="utf-8") as _f:
    APP_VERSION = re.search(r'APP_VERSION = "([^"]+)"', _f.read()).group(1)

# アプリアイコン: assets/branding のPNGからマルチサイズICO/ICNSを生成して埋め込む。
# PNGが無い/Pillow不在のときは icon=None（PyInstaller既定アイコン）へフォールバックする。
APP_ICON = None
try:
    from PIL import Image
    _icon_png = os.path.join(SPECPATH, "assets", "branding", "mojicast-mo-icon-light.png")
    if os.path.exists(_icon_png):
        if IS_MAC:
            APP_ICON = os.path.join(SPECPATH, "assets", "branding", "mojicast-mo-icon-light.icns")
            Image.open(_icon_png).save(APP_ICON, format="ICNS")
        else:
            APP_ICON = os.path.join(SPECPATH, "assets", "branding", "mojicast-mo-icon-light.ico")
            Image.open(_icon_png).save(
                APP_ICON, format="ICO",
                sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                       (64, 64), (128, 128), (256, 256)])
except Exception:
    APP_ICON = None

datas, binaries, hiddenimports = [], [], []

# ネイティブ拡張やデータファイルを丸ごと取り込む重量級パッケージ群
# ※ torch / transformers は排除済み（句読点=onnxruntime / 翻訳=ctranslate2）
# ※ reazonspeech はラッパー2関数を engine にインライン化して排除
#   （librosa→sklearn/scipy の玉突き同梱で約600ファイル膨らんでいたため）
# ※ onnxruntime は標準フックで足りる（collect_all だと training/tools まで入る）
# ※ pythonnet / clr_loader は WebView2 用（Windows専用。macはpyobjc/WKWebView）
_HEAVY_PKGS = [
    "ctranslate2", "sentencepiece", "huggingface_hub", "opencc",
    "sherpa_onnx", "sounddevice",
    "webview",
]
if not IS_MAC:
    _HEAVY_PKGS += ["pythonnet", "clr_loader"]
for pkg in _HEAVY_PKGS:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# ビルド用の残骸（インポートライブラリ等・実行時不要）を落とす
datas = [x for x in datas if not x[0].lower().endswith((".lib", ".pdb", ".exp"))]
binaries = [x for x in binaries if not x[0].lower().endswith((".lib", ".pdb", ".exp"))]
# ctranslate2 の wheel が同梱してくる cuDNN は CPU 専用ビルドでは未使用。
# プロプライエタリで再配布条件も伴うため確実に落とす（ライセンス総点検 2026-08-14）
binaries = [x for x in binaries if "cudnn" not in x[0].lower()]

# 動的 import で拾い漏れやすいもの
hiddenimports += [
    "onnxruntime",               # 句読点（punct.py）が使用・標準フックでDLL収集
    "sherpa_onnx",
]
if IS_MAC:
    # pywebview cocoa バックエンド（pyobjc）と platform_compat が使う枠組み
    hiddenimports += ["objc", "AppKit", "WebKit", "Foundation", "Security"]
else:
    hiddenimports += ["clr"]     # pythonnet（pywebview の WinForms/WebView2 バックエンド）

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
              "PIL",   # このspec冒頭のアイコン生成でのみ使用（実行時は不要）
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
    icon=APP_ICON,          # assets/branding のロゴ（上で生成・無ければ既定）
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name="Mojicast",
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name="Mojicast.app",
        icon=APP_ICON,
        bundle_identifier="com.ishiki-emo.mojicast",
        version=APP_VERSION,
        info_plist={
            "CFBundleDisplayName": "Mojicast",
            "CFBundleName": "Mojicast",
            "CFBundleShortVersionString": APP_VERSION,
            "LSMinimumSystemVersion": "12.0",   # Apple Silicon 世代のみサポート
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.utilities",
            "NSHumanReadableCopyright": "© ishiki-emo",
            # これが無いとマイクアクセス時にクラッシュする（macOSの必須項目）
            "NSMicrophoneUsageDescription":
                "話し声をリアルタイムに字幕へ変換するためにマイクを使用します。"
                "音声はこのMacの外へ送信されません。",
        },
    )
