#!/bin/bash
# Mojicast macOS 配布パッケージ組み立てスクリプト（build_bundle.ps1 のMac版）
# PyInstaller ビルド後に実行し、アセットを .app へ配置して dmg を作成する。
#
#   .venv/bin/pyinstaller --noconfirm Mojicast.spec
#   ./build_bundle.sh
#
# 生成物: dist/Mojicast-<version>-mac-arm64.dmg
#
# 常に軽量版（モデル無し・初回起動時に ~/Library/Application Support/Mojicast/models
# へ自動DL）。モデル同梱版は Booth の1ファイル1.2GB上限を超えるため作らない。
# 配布は未公証（フェーズ1）: ad-hoc 署名のみ。初回起動手順は同梱の
# 「はじめにお読みください（Mac版）.html」を参照。
set -euo pipefail
cd "$(dirname "$0")"

APP="dist/Mojicast.app"
RES="$APP/Contents/Resources"
if [ ! -x "$APP/Contents/MacOS/Mojicast" ]; then
    echo "先に PyInstaller ビルドを実行してください（$APP が見つかりません）" >&2
    exit 1
fi

VERSION=$(sed -n 's/^APP_VERSION = "\([^"]*\)"/\1/p' app_server.py)
echo "Mojicast v$VERSION (mac arm64)"

# --- コード資産のみ Resources へ。データファイル(hotwords等)は同梱せず、
#     defaults/ を種として初回起動時に Application Support 側へ生成する ---
for f in overlay.html silero_vad.onnx; do
    cp "$f" "$RES/$f"
    echo "  asset : $f"
done
for d in defaults ui; do
    rm -rf "${RES:?}/$d"
    cp -R "$d" "$RES/$d"
    echo "  asset : $d/"
done

# --- ライセンス表記（Apache/MIT/BSD の attribution・FuguMT の CC BY-SA 等）は
#     配布物側でも満たす必要があるため .app 内にも持たせる ---
cp CREDITS.md "$RES/CREDITS.md"
echo "  doc   : CREDITS.md (app内)"

# --- アセット追加でバンドルの署名が崩れるため、ad-hoc で締め直す
#     （未署名だと Apple Silicon では起動すらできない） ---
codesign --force --deep -s - "$APP"
echo "  sign  : ad-hoc 署名を更新"

# --- dmg staging: アプリ + Applicationsリンク + ドキュメント ---
STAGE="dist/dmg-stage"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
for f in "はじめにお読みください（Mac版）.html" "マニュアル.html" CREDITS.md; do
    cp "$f" "$STAGE/"
    echo "  doc   : $f"
done
cp -R "ガイド" "$STAGE/ガイド"
echo "  doc   : ガイド/"

DMG="dist/Mojicast-$VERSION-mac-arm64.dmg"
rm -f "$DMG"
hdiutil create -volname "Mojicast" -srcfolder "$STAGE" -ov -format UDZO "$DMG" > /dev/null
rm -rf "$STAGE"

# --- Booth 用 zip（Booth は dmg を直接アップロードできないため zip で包む。
#     ガイドを同梱して、解凍した時点で初回起動手順が目に入るようにする）---
ZIPSTAGE="dist/zip-stage"
rm -rf "$ZIPSTAGE"
mkdir -p "$ZIPSTAGE"
cp "$DMG" "$ZIPSTAGE/"
cp "はじめにお読みください（Mac版）.html" "$ZIPSTAGE/"
ZIP="dist/Mojicast-$VERSION-mac-arm64.zip"
rm -f "$ZIP"
ditto -c -k --norsrc "$ZIPSTAGE" "$ZIP"   # UTF-8名対応zip（._メタデータは除外）
rm -rf "$ZIPSTAGE"

echo ""
echo "完成: $DMG ($(du -h "$DMG" | cut -f1))  ← GitHub Releases用"
echo "      $ZIP ($(du -h "$ZIP" | cut -f1))  ← Booth用"
