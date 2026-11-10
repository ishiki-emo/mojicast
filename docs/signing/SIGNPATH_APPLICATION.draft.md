<!--
SignPath Foundation 申請フォーム（https://signpath.org/apply）に書く内容の英語下書き。
フォームの実際の設問はページが JS で描画されていて取得できなかったので、
一般的な設問（プロジェクト概要・リポジトリ・ライセンス・ビルド方法・配布物・
チーム・評判）に合わせて書いてある。送信前にフォームの設問に合わせて並べ替えること。
[要確認] の箇所は申請時点の事実で埋める／直す。
-->

# SignPath Foundation application — Mojicast (draft)

## Project

- **Name:** Mojicast
- **Repository:** https://github.com/ishiki-emo/mojicast
- **Homepage / download page:** https://ishiki-emo.com/mojicast (product page), https://github.com/ishiki-emo/mojicast/releases
- **License:** MIT (all project code). See "Third-party components" below.
- **First public release:** [要確認: v0.x の公開日] — current release v1.1.0 (2026-11-05). Actively maintained; releases roughly every 1–3 weeks.

## Description

Mojicast is a desktop app for live streamers that turns speech into on-screen
captions in real time. It shows the captions in OBS (browser source) and can
translate them into several languages. Speech recognition, punctuation and
translation all run locally on the user's PC (ONNX Runtime / sherpa-onnx /
CTranslate2); audio and text never leave the machine. The app is used mainly by
Japanese VTubers and streamers.

## Why we need code signing

The Windows build is distributed as a zip and an Inno Setup installer. Both are
unsigned today, so every new user sees a SmartScreen warning, and many
streamers are not comfortable bypassing it. We already ship an installer partly
to reduce Mark-of-the-Web problems, but only a signature removes the warning.

## What will be signed

Windows x64 only (macOS is out of scope for this application):

1. `Mojicast.exe` — the PyInstaller-built launcher of the app (inside the zip
   and the installer)
2. `Mojicast-vX.Y.Z-win-x64-setup.exe` — the Inno Setup installer

Third-party DLLs and Python extension modules bundled in `_internal\` are
**not** signed. AI model weights are not bundled; they are downloaded from
Hugging Face on first use.

All signed files carry `ProductName = Mojicast` and
`ProductVersion = <release version>` (the same value for every file in a
build).

## Build system

- Builds run on GitHub Actions (`.github/workflows/release-build-windows.yml`,
  `windows-2022` runner, manual `workflow_dispatch`), from the tagged source in
  this repository.
- Steps: `pip install -r requirements.txt` (pinned versions) → PyInstaller
  (`Mojicast.spec`) → `build_bundle.ps1 -NoModels` → `make_release_zip.ps1` →
  `make_installer.ps1` (Inno Setup 6) → upload artifact → SignPath signing
  request.
- Each signing request is approved manually by the project owner.

## Team

Mojicast is developed by one person.

- Author / committer / reviewer / approver: ishiki-emo (GitHub: @ishiki-emo)
- MFA is enabled for GitHub [要確認] and will be enabled for SignPath.
- Pull requests from outside contributors are reviewed by the author before
  merging.

## Reputation

- Used in a live stream by the Japanese singer Ado ("はじめてのSILENT HILL f",
  2026-08-21, credited in the video description, about 1.1 million views)
  [要確認: URL と最新の再生数].
- [要確認: GitHub stars / ダウンロード数 / プロダクトページの利用者一覧の件数]

## Privacy

Mojicast sends no user data. It only connects to Hugging Face to download models
and to raw.githubusercontent.com to check for updates. Privacy policy:
[要確認: 公開後の docs/PRIVACY.md の URL]

## Third-party components (for review)

All bundled libraries are listed in `CREDITS.md`. We want to be upfront about
the following non-OSI components in the current build, and ask how you would
like us to handle them:

- **Intel oneMKL** (statically linked into `ctranslate2.dll` from the official
  CTranslate2 PyPI wheel) and **Intel OpenMP** (`libiomp5md.dll`), under the
  Intel Simplified Software License. [要判断: MKL 無しのビルドに切り替えて
  から申請するなら、この項目は「切り替え済み」と書き換える]
- **Microsoft WebView2 SDK** DLLs (`Microsoft.Web.WebView2.Core.dll`,
  `Microsoft.Web.WebView2.WinForms.dll`, `WebView2Loader.dll`), shipped by
  pywebview. The WebView2 Runtime itself is part of Windows 11.
- Microsoft Visual C++ / Universal CRT runtime DLLs, which we treat as System
  Libraries.

The Steinberg ASIO-enabled PortAudio DLL that sounddevice ships is excluded
from the build because Mojicast does not use ASIO. [要確認: 除外を実装済みか]
