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
https://github.com/ishiki-emo/mojicast/blob/main/docs/PRIVACY.md (also linked from https://ishiki-emo.com/mojicast)
[要確認: 申請前に docs/PRIVACY.md を main に公開しておく]

Points the privacy policy states explicitly (so reviewers do not have to find
them in the code):

- **Unauthenticated local endpoints.** The caption overlay for OBS
  (`/events?overlay=1`) and `/api/style` can be read without authentication,
  because OBS browser sources cannot send credentials. The server listens on
  `127.0.0.1` only and rejects requests from other web origins, but other
  programs on the same computer can read the live captions. All other API
  calls require a key issued by Mojicast.
- **Caption logs.** Saving caption logs (`save_log`) is on by default. Logs are
  unencrypted text files in the app's data folder and never leave the
  computer. Users can turn it off in Settings → Caption log and delete the
  folder.
- **Mark of the Web removal.** At startup the Windows build removes the
  `Zone.Identifier` stream from files inside its own install folder, and only
  when a bundled component still carries it (i.e. the first launch after
  extracting a downloaded zip). Without this, .NET refuses to load the
  pythonnet / WebView2 assemblies and the app cannot start. It never touches
  files outside its folder, and SmartScreen still checks `Mojicast.exe` before
  any of this runs. (`apppaths.py`, `_unblock_downloaded_files`)
- **Pinned downloads.** AI models are downloaded from Hugging Face at fixed
  commit IDs (`model_pins.py`), not from the moving `main` branch. GitHub
  Actions are pinned to commit SHAs and run with `contents: read`.

## Third-party components (for review)

All bundled libraries are listed in `CREDITS.md`.

We want to be upfront about the following non-OSI components in the current
build, and ask how you would like us to handle them:

- **Intel oneMKL and Intel OpenMP, inside the official CTranslate2 binary.**
  Mojicast ships the upstream CTranslate2 4.8.1 wheel from PyPI unchanged. On
  Windows, that official binary statically links Intel oneMKL into
  `ctranslate2.dll` and ships Intel OpenMP (`libiomp5md.dll`), both under the
  Intel Simplified Software License, which is not OSI-approved.
  - We have prepared a replacement: CTranslate2 4.8.1 built from its tagged
    source with `WITH_MKL=OFF` (matrix math via oneDNN, Apache-2.0) and MSVC,
    inside the release GitHub Actions job (`tools/signing/ct2_nomkl/build.ps1`,
    documented in `docs/signing/CT2_NOMKL_BUILD.md`). The workflow fails if any
    Intel MKL or Intel OpenMP file remains in the package. In that build,
    OpenMP comes from the MSVC runtime (`vcomp140.dll`, Visual C++
    Redistributable).
  - We verified that this build produces exactly the same translations as the
    official binary: 200 of 200 sentences identical in every translation mode
    we use, on three different CPUs (AMD Ryzen 7 9800X3D, Intel Core
    i7-13700HX, Intel N100). The benchmark tool and results are in
    `bench/ct2_mkl_compare/`.
  - However, it is measurably slower: each translated sentence takes 22 to
    209 ms longer than with the official binary (1.2 to 3.1 times as long;
    the largest added time is +209 ms per sentence for Korean on the Intel
    N100). Because Mojicast shows live captions during
    streams, we currently keep the official binary.
  - If you require it, we will switch to the MKL-free build starting with the
    next release. The switch is a single workflow input
    (`ctranslate2: nomkl`) plus the matching `CREDITS.md` update.
- **Microsoft WebView2 SDK** DLLs (`Microsoft.Web.WebView2.Core.dll`,
  `Microsoft.Web.WebView2.WinForms.dll`, `WebView2Loader.dll`), shipped by
  pywebview. The WebView2 Runtime itself is part of Windows 11.
- Microsoft Visual C++ / Universal CRT runtime DLLs, which we treat as System
  Libraries.

The Steinberg ASIO-enabled PortAudio DLL that sounddevice ships is excluded
from the build because Mojicast does not use ASIO.
