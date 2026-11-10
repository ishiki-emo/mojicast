# 署名対象（Windows 軽量版）の同梱コンポーネントとライセンス

調査日: 2026-11-10 / 対象: `build_bundle.ps1 -NoModels` で作る `dist\Mojicast\`
（v1.1.0 時点のビルド物を実際に開いて確認）

SignPath Foundation の条件は「**すべてのコンポーネント**が OSI 承認ライセンス」
（例外は GPLv3 第1節の意味での System Libraries のみ）。
<https://signpath.org/terms>

## 判定のまとめ

| 区分 | 判定 |
|---|---|
| ✅ OSI 承認 | 下の表の大部分 |
| ⚠️ **非 OSI・要対応** | Intel oneMKL（ctranslate2.dll に静的リンク）、Intel OpenMP（libiomp5md.dll）→ MKL なしの自前ビルドを用意済み・採用は実測のあと（下記）、~~Steinberg ASIO（libportaudio64bit-asio.dll）~~ → spec で除外済み、Microsoft WebView2 SDK の DLL 3種 |
| 🟡 非 OSI だが System Libraries 扱いを想定 | MSVC ランタイム / UCRT / api-ms-win-*.dll / dbghelp.dll / dbgcore.dll（MKL なし版に切り替えた場合は vcomp140.dll も） |
| ℹ️ 同梱しない（実行時 DL） | 学習済みモデル（SenseVoice は非 OSI の FunASR Model License） |

## 1. 非 OSI のもの（明示）

| ファイル | 中身 | ライセンス | 入ってくる経路 | 対応案 |
|---|---|---|---|---|
| `_internal\ctranslate2\ctranslate2.dll`（59MB） | CTranslate2 本体＋**Intel oneMKL を静的リンク**（バイナリ内に "Intel(R) oneAPI Math Kernel Library" を確認） | CTranslate2 は MIT、**oneMKL は Intel Simplified Software License（非 OSI）** | PyPI の ctranslate2 wheel | **準備済み・採用は実測待ち（2026-11-10）**: CTranslate2 を MKL なしでソースからビルドした wheel（`4.8.1+nomkl`・MSVC）に差し替える案。中身は CTranslate2（MIT）＋ oneDNN 3.1.1（Apache-2.0）だけ。リリースの既定はまだ公式 wheel。[CT2_NOMKL_BUILD.md](CT2_NOMKL_BUILD.md) |
| `_internal\ctranslate2\libiomp5md.dll` | Intel OpenMP ランタイム | Intel Simplified Software License（非 OSI） | 同上 | **準備済み・採用は実測待ち**: 上と同時に消え、MSVC の OpenMP ランタイム `vcomp140.dll`（VC++ 再頒布パッケージ）に置き換わる。非 OSI だが MSVC ランタイムと同じ System Libraries 扱いを想定（2章）。LLVM の libomp（OSI）は int8 の訳文が公式とずれるため不採用 |
| `_internal\_sounddevice_data\portaudio-binaries\libportaudio64bit-asio.dll` | PortAudio＋**Steinberg ASIO SDK** | PortAudio は MIT、ASIO SDK は Steinberg のライセンス | sounddevice wheel | **Mojicast は ASIO を使っていない**（コード中に参照なし）。spec で除外するだけで済む |
| `_internal\webview\lib\Microsoft.Web.WebView2.Core.dll` / `Microsoft.Web.WebView2.WinForms.dll` / `runtimes\*\native\WebView2Loader.dll` | WebView2 SDK | Microsoft WebView2 SDK ライセンス（再配布可・非 OSI） | pywebview | GUI の土台なので外せない。WebView2 Runtime 自体は Windows 11 の OS 部品なので System Libraries 扱いを主張できる余地はあるが、SDK の DLL は厳密には別物。**申請時に明記して判断を仰ぐ** |

## 2. System Libraries 扱いを想定するもの（非 OSI だが OS/コンパイラ付属）

| ファイル | 由来 |
|---|---|
| `vcruntime140.dll` `vcruntime140_1.dll` `msvcp140.dll` `MSVCP140_1.dll` `numpy.libs\msvcp140-*.dll` | MSVC 再頒布可能ランタイム |
| `ucrtbase.dll` `api-ms-win-*.dll` | Windows Universal CRT |
| `dbghelp.dll` `dbgcore.dll` | Windows のデバッグヘルパー（依存解析で拾われたもの。実行に要るか未確認） |

GPLv3 第1節の System Libraries の定義（主要コンポーネント＝OS やコンパイラに付属し、
それと一緒に使うためのもの）に当てはまると考えられる。dbghelp/dbgcore は
spec で除外しても動くなら除外した方が説明が楽。

## 3. OSI 承認のもの

※ Stream Deck プラグイン（`tools/streamdeck`・2026-11-10 develop）は本体の Zip / setup.exe に入らない別配布物なので、
SignPath の署名対象外。中身は自作 TS（MIT）＋ `@elgato/streamdeck`（MIT）＋ `sdpi-components.js`（MIT・同梱の Lit は BSD-3-Clause）で、
外部通信は `127.0.0.1` の本体だけ。

| コンポーネント | ライセンス | 備考 |
|---|---|---|
| Mojicast 本体（Python コード・UI・overlay.html・defaults・mcp\） | MIT | LICENSE 末尾に日本語の注記があり、GitHub は「Other」と判定している（後述） |
| `silero_vad.onnx`（Silero VAD） | MIT | 唯一同梱するモデル |
| CPython 3.11（python311.dll, *.pyd, base_library） | PSF-2.0 | |
| PyInstaller ブートローダ（Mojicast.exe の実行部） | GPL-2.0 + bootloader 例外 | |
| OpenSSL（libssl-3 / libcrypto-3） | Apache-2.0 | |
| libffi | MIT | |
| SQLite（sqlite3.dll） | Public Domain | OSI 承認ではないが、一般に問題視されない |
| ONNX Runtime | MIT | |
| sherpa-onnx / sherpa-onnx-core | Apache-2.0 | |
| NumPy | BSD-3-Clause | |
| OpenBLAS（numpy.libs\libscipy_openblas64_*.dll） | BSD-3-Clause | |
| CTranslate2（MKL なし自前ビルド `4.8.1+nomkl` に切り替えた場合）＋ oneDNN 3.1.1（静的リンク） | MIT ＋ Apache-2.0 | ライセンス文は wheel の dist-info\licenses に同梱 |
| sentencepiece | Apache-2.0 | |
| OpenCC（辞書含む） | Apache-2.0 | |
| huggingface_hub | Apache-2.0 | |
| pywebview（`WebBrowserInterop.*.dll` `pywebview-android.jar` 含む） | BSD-3-Clause | android.jar は Windows で不要・除外候補 |
| pythonnet / clr_loader / Python.Runtime.dll / .NET ファサード（System.*.dll, netstandard.dll） | MIT | |
| sounddevice / PortAudio（`libportaudio64bit.dll`） | MIT | |
| certifi | MPL-2.0 | |
| protobuf（`google\`） | BSD-3-Clause | |
| PyYAML / charset_normalizer / setuptools / wheel | MIT | |
| click / MarkupSafe | BSD-3-Clause | |
| tqdm | MPL-2.0 / MIT | |

## 4. フォント

**同梱なし。** UI は `"Yu Gothic UI","Meiryo"` 等の Windows 標準フォントを名前で
指定しているだけで、フォントファイル（.ttf/.otf/.woff）は配布物に 1 つも入っていない
（dist 全体を検索して確認）。外部の Web フォント（Google Fonts 等）の読み込みも無い。
ユーザーが自分で入れたフォント（源暎・ラノベPOP 等）を選べるが、同梱はしない。

## 5. 同梱しない（初回・機能オン時にダウンロード）

署名対象のパッケージには入らないが、利用者に影響するので申請時とプライバシーポリシーで触れる。

| モデル | ライセンス | OSI |
|---|---|---|
| ReazonSpeech k2 v2 | Apache-2.0 | ✅ |
| 句読点 BERT（ONNX 変換・ishiki-emo/mojicast-punct-onnx） | Apache-2.0 | ✅ |
| FuguMT ja-en（CT2 変換） | CC BY-SA 4.0 | ソフトウェアライセンスではないが自由ライセンス |
| M2M-100 418M（CT2 変換） | MIT | ✅ |
| SenseVoice small（sherpa-onnx int8） | FunASR Model License | ❌（帰属表示が条件の独自ライセンス）。オプション機能 |
| Zipformer audio tagging | Apache-2.0（ラベル CSV は AudioSet 由来 CC BY 4.0） | ✅ |

## 6. ついでに見つかったこと

- **LICENSE の判定が「Other」**: MIT 本文の後ろに日本語の注記（「この LICENSE は
  コードに対するもの…」）が付いているため、GitHub が MIT と認識していない。
  審査で「OSI ライセンスか」を機械的に見られると不利。注記は CREDITS.md / README へ
  移し、LICENSE は MIT 本文だけにするのが無難。
- **CREDITS.md の記述が実物とずれている**: 「Pillow / hf_xet ほか」が同梱扱いに
  なっているが、現在の spec では両方除外済み。逆に oneMKL（ctranslate2.dll 内）・
  ASIO・WebView2 SDK・OpenSSL・protobuf の記載が無い。
