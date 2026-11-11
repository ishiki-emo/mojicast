# Windows 版コード署名（SignPath Foundation）への移行手順

SignPath Foundation の無料 OSS コード署名を使うために、次のリリース以降でやることの
チェックリスト。条件の原文は <https://signpath.org/terms>（2026-11-10 時点で確認）。

- 👤 = 作者（えもさん）がやる作業（アカウント・承認・判断・外部サイト）
- 🤖 = Claude Code がやる作業（リポジトリ内の変更・検証）

関連する下書き（このブランチ `chore/signpath-prep` にある）:

| ファイル | 中身 |
|---|---|
| [signing/LICENSE_INVENTORY.md](signing/LICENSE_INVENTORY.md) | 署名対象に同梱される全コンポーネントのライセンス一覧（調査結果） |
| [signing/CODE_SIGNING_POLICY.draft.md](signing/CODE_SIGNING_POLICY.draft.md) | README に入れる "Code signing policy" 節 |
| [signing/PRIVACY.draft.md](signing/PRIVACY.draft.md) | プライバシーポリシー（日英） |
| [signing/SIGNPATH_APPLICATION.draft.md](signing/SIGNPATH_APPLICATION.draft.md) | 申請フォームの英語下書き |
| [../.github/workflows/release-build-windows.yml](../.github/workflows/release-build-windows.yml) | Actions でのリリースビルド（手動実行・署名なし） |
| [signing/CT2_NOMKL_BUILD.md](signing/CT2_NOMKL_BUILD.md) | CTranslate2 の MKL なしビルド（手順・オプション・戻し方・人の確認） |

---

## 0. 申請前に片付けること（ライセンス）

SignPath の条件は「全コンポーネントが OSI 承認ライセンス」。現状のビルドは満たしていない
（詳細は LICENSE_INVENTORY.md）。

- [x] 👤 **Intel oneMKL / OpenMP（ctranslate2）の扱いを決める** → 案A（MKL を外す）に決定（2026-11-10）
  - 案A: CTranslate2 を MKL 無しで CI ビルドする。翻訳速度・メモリが変わるので実測が要る
  - 案B: 申請時に正直に書いて SignPath に判断を仰ぐ
  - 実測（2026-11-10・Ryzen 7 9800X3D）: CTranslate2 は AMD では MKL を使わず oneDNN で動いている
    （`Use Intel MKL: false` / GEMM_S8 backend: DNNL）。MKL を抜いて影響を受けるのは Intel 利用者だけ
  - [x] 🤖 Intel 機で比較するツールを作った（2026-11-10・[bench/ct2_mkl_compare](../bench/ct2_mkl_compare/README.md)）。
    英訳 int8 / fp32・中・韓の1文の時間・訳文の一致・メモリ・CPU 時間を比べる。
    `build.ps1` で Zip（えもさんの配信ログ200文入り・ベータテスターに配ってよい）を作る
  - [x] 👤 Intel 2台（N100・i7-13700HX）で計測 → [results](../bench/ct2_mkl_compare/results/)。訳文は 4ケースとも 200/200 一致、
    MKL なしは平均 0.90〜1.30 倍の時間、ピークメモリは M2M で約 300MB・fp32 で約 200MB 減る
  - [x] 🤖 MKL なしの自前ビルドを用意した（[signing/CT2_NOMKL_BUILD.md](signing/CT2_NOMKL_BUILD.md)・
    `tools/signing/ct2_nomkl/build.ps1`・release-build-windows.yml に `ctranslate2: nomkl` で組み込み済み・
    **既定はまだ official**）。構成は MSVC＋vcomp140＋`OMP_WAIT_POLICY=PASSIVE`（訳文は公式と一致）。
    clang-cl＋LLVM libomp は int8 の訳文がずれるため不採用（2026-11-10 えもさん判断）
  - [x] 👤 自前ビルドでの確認（3台: 9800X3D・i7-13700HX・N100）→ 訳文は全台・全ケース 200/200 一致。ただし vcomp＋PASSIVE は
    MKL あり比で 1文あたり +22〜+209ms 遅い（1.2〜3.1 倍・N100 の韓国語訳 +209ms）、スレッド数でも改善せず
  - [x] 👤 ~~既定は official のまま確定（2026-11-10）~~ → 次の構成で解決したので置き換え
  - [x] 🤖 ④ cl `/openmp:llvm` ＋ 上流 LLVM OpenMP（libomp.dll・ソースからビルド）を試作（2026-11-11）。パッチなし・
    OpenMP の依存は libomp だけ・訳文 200/200 一致・速さは公式の MKL なし（dnnl）と同じ（CT2_NOMKL_BUILD.md 2.5）
  - [x] 👤 ④ を3台で計測 → 訳文は全台・全ケース 200/200 一致、MKL あり比の増えは最大 +83ms（N100 の韓国語訳）で
    MKL を外した分だけ（CT2_NOMKL_BUILD.md 9.4）
  - [x] 👤 **Windows 版の配布物を ④ に切り替えると決定（2026-11-11）**。vcomp＋PASSIVE と clang-cl の構成は不採用
  - [x] 🤖 切り替え（2026-11-11）: build.ps1 の既定 `-OpenMP llvm`、release-build-windows.yml の既定 `ctranslate2: nomkl`・
    Sanity check を `tools/signing/ct2_nomkl/check_bundle.ps1` に（libomp.dll あり／Intel・vcomp140・libomp140 なし／
    ライセンス文あり）、CREDITS.md・LICENSE_INVENTORY.md・申請下書き、build_bundle.ps1 の照合、smoke_test.ps1 で
    同梱の libomp.dll が読み込まれることの確認（CT2_NOMKL_BUILD.md 4章・9.5）
  - 参考（開発機 AMD で MKL を強制した場合・8文）: MKL 有効だと中韓 約3.4〜4.0秒/文 vs 無効 130〜190ms、
    ピークメモリ 2.1GB vs 0.6GB。AMD で MKL を使わない今の既定は正しい
- [x] 🤖 ASIO 版 PortAudio DLL（`libportaudio64bit-asio.dll`）を spec で除外した（2026-11-10 develop・次のリリースから）
      凍結時は `SD_ENABLE_ASIO` を apppaths で外す（設定されていると sounddevice がこの DLL を探して失敗するため）
- [ ] 🤖 ついでに不要物を除外: `pywebview-android.jar`、（動作確認のうえ）`dbghelp.dll` `dbgcore.dll`
- [x] 🤖 LICENSE 末尾の日本語注記を CREDITS.md へ移した（2026-11-10 main に反映・GitHub の判定が MIT になったことを確認済み）
- [x] 🤖 CREDITS.md を実物に合わせた（2026-11-10 develop・次のリリースから）。2026-11-11 に MKL なし版の中身
      （oneDNN・LLVM OpenMP）へ書き換えた（build_bundle.ps1 が同梱物とのずれを警告し、テストが既定とのずれを見る）
- [ ] 👤 手元ビルドで配るとき（CI へ移る前のリリース）は、reazonspeech-env ではなくロックファイルの環境に
      MKL なし版を入れて固める（CT2_NOMKL_BUILD.md 4章「手元ビルド」）。reazonspeech-env のままだと公式版が入る

## 1. exe へのバージョン情報の付与（次のリリースで）

SignPath は署名する全ファイルに `ProductName` と `ProductVersion` を要求する。
現状: `Mojicast.exe` は**全項目が空**、`setup.exe` は ProductName=Mojicast /
ProductVersion=版 が入っている（Inno Setup の既定）が FileVersion は空。

- [x] 🤖 `Mojicast.spec` に VERSIONINFO を足した（2026-11-10・Windows のみ・APP_VERSION から生成）。
      実ビルドで CompanyName=`ishiki-emo` / ProductName=`Mojicast` / ProductVersion=`1.1.0` /
      FileVersionRaw=`1.1.0.0` を確認

  ```python
  # Mojicast.spec（APP_VERSION を読んだ直後あたり）
  VERSION_INFO = None
  if not IS_MAC:
      from PyInstaller.utils.win32 import versioninfo as vi
      _n = [int(x) for x in re.findall(r"\d+", APP_VERSION.split("-")[0])][:4]
      _n = tuple(_n + [0] * (4 - len(_n)))
      VERSION_INFO = vi.VSVersionInfo(
          ffi=vi.FixedFileInfo(filevers=_n, prodvers=_n, mask=0x3F, flags=0x0,
                               OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
          kids=[
              vi.StringFileInfo([vi.StringTable("040904B0", [
                  vi.StringStruct("CompanyName", "ishiki-emo"),
                  vi.StringStruct("FileDescription", "Mojicast"),
                  vi.StringStruct("FileVersion", APP_VERSION),
                  vi.StringStruct("InternalName", "Mojicast"),
                  vi.StringStruct("LegalCopyright", "Copyright (c) 2026 ishiki-emo. MIT License."),
                  vi.StringStruct("OriginalFilename", "Mojicast.exe"),
                  vi.StringStruct("ProductName", "Mojicast"),
                  vi.StringStruct("ProductVersion", APP_VERSION),
              ])]),
              vi.VarFileInfo([vi.VarStruct("Translation", [0x0409, 1200])]),
          ],
      )
  # EXE(...) に version=VERSION_INFO を追加
  ```

- [x] 🤖 `installer/Mojicast.iss` に VersionInfo 一式を追加し、`make_installer.ps1` が数値版
      （`/DAppVersionNum=`・接尾辞を落とす）を渡すようにした（2026-11-10）
- [x] 🤖 確認: `-Version 1.2.0-beta1` で作った setup.exe が ProductName=`Mojicast` /
      ProductVersion=`1.2.0-beta1` / FileVersionRaw=`1.2.0.0` / CompanyName=`ishiki-emo`
- [x] 👤 会社名の表記 → `ishiki-emo`（2026-11-10 決定）。なお「アプリと機能」に出る発行元
      （`AppPublisher`）は `癒色えも` のまま

## 2. Actions でのリリースビルドへの切り替え

SignPath は「CI 上でこのリポジトリのソースから自動ビルドされた物」しか署名しない
（Origin verification）。手元ビルドの exe は署名に出せない。

- [ ] 🤖 `release-build-windows.yml` を develop → main に入れる
      （workflow_dispatch は既定ブランチにファイルが無いと実行ボタンが出ない）
- [ ] 👤 Actions 画面で「Release build (Windows, manual)」を実行
      （`expected_version` に今回の版を入れる）
- [ ] 👤 artifact をダウンロードし、手元ビルドと並べて差分確認
      - ファイル一覧の比較（🤖 が比較スクリプトを書ける）
      - サイズ、VersionInfo、アイコン
- [ ] 👤 CI の成果物を `dist\Mojicast\` に展開して `smoke_test.ps1 -Fresh` を通す
      （CI では GUI とモデル DL を伴うスモークは回していない）
- [ ] 👤 問題なければ、**今回から gh release に載せる Windows 版を CI の成果物にする**
      （手元ビルドは比較用に残す。Mac 版の手順は変えない）
- [ ] 🤖 依存のバージョンを完全固定するか検討（今は requirements.txt の直接依存だけ固定。
      artifact に `pip-freeze.txt` を残しているので、差分が出たらそこで分かる）

試運転の結果（2026-11-10・試運転ブランチ `ci/release-build-dryrun` で push 起動）:
- ビルドは約3分で最後まで通る（Zip 80MB / setup.exe 52MB）
- `actions/setup-python` の 3.11 は python.org 版 3.11.9 止まり → uv の 3.11.15（手元と同じ）に変更済み
- 間接依存は `.github/release/requirements-win.lock`（ハッシュ付き）で固定。requirements.txt を変えたら
  `.github/release/requirements-win.in` 冒頭のコマンドで作り直す
- 手元ビルドとの差は、手元の開発環境に入っている余計なパッケージ（dbghelp/dbgcore・markupsafe・wheel 等）が
  CI 側に入らないことと、click/tqdm 等の版、Windows ランタイム DLL のビルド違い。CI 側の方が素直
- [x] 🤖 CI の成果物で `smoke_test.ps1 -Fresh` → PASS（2026-11-10）。1回目は M2M の初回DLで `LICENSE` だけ
  取り損ねて「英訳の読み込みに失敗」になった（HF 側は正常・再実行で揃う＝一時的な取得失敗）。
  アプリは次回の開始時に続きを取りに行くので自然に回復するが、初回だけ訳が出ない体験にはなる

既知の注意点:
- Pillow が無いとアイコン生成が黙ってスキップされ、installer の `SetupIconFile` で失敗する。
  ワークフローでは明示的に入れている
- Inno Setup はランナーに無ければ choco で入れる

## 3. README への Code signing policy 節の反映（承認後）

承認前に載せると「署名していないのに署名ポリシーを掲げる」ことになるので、**承認後**にやる。
ただし申請時に「どこに載せるか」を聞かれるので、場所だけ先に決めておく。

- [x] 👤 プライバシーポリシーの公開場所 → **`docs/PRIVACY.md` を正本**にし、プロダクトページ
      （ishiki-emo.com/mojicast）からはそこへリンクする（2026-11-10 決定）
- [ ] 🤖 `docs/signing/PRIVACY.draft.md` を公開用に仕上げ（日付・版を埋める）
- [ ] 🤖 README に「## Code signing policy」節を追加（下書きをそのまま使う）
- [ ] 👤 配布ページにも "Code signing policy" の語でリンクを置く:
      GitHub Releases の本文、BOOTH、プロダクトページ（ishiki-emo.com/mojicast）
- [ ] 🤖 リリースノートのテンプレートに Code signing policy へのリンクを入れる

## 4. 申請 → 承認後の署名ステップ追加と Secrets 設定

- [ ] 👤 GitHub アカウントの 2 要素認証が有効か確認（条件: 全メンバー MFA 必須）
- [ ] 👤 申請フォーム（<https://signpath.org/apply>）を送る。内容は
      `SIGNPATH_APPLICATION.draft.md` の [要確認] を埋めてから
- [ ] 👤 承認後、SignPath にログインして MFA を有効にする
- [ ] 👤 SignPath 側で設定（ガイドに従う）:
      - GitHub を Trusted Build System として接続（SignPath の GitHub App をリポジトリに入れる）
      - Artifact configuration: zip の中の `Mojicast.exe`、および `*-setup.exe` を署名対象に。
        **ProductName=Mojicast / ProductVersion の制約（metadata restrictions）を必ず有効に**
      - Signing policy: `release-signing`（手動承認あり）。承認者 = 自分
- [ ] 👤 GitHub の Secrets/Variables を登録（Settings → Secrets and variables → Actions）
      - Secret `SIGNPATH_API_TOKEN`（SignPath の CI 用ユーザーのトークン）
      - Variable `SIGNPATH_ORGANIZATION_ID`、`SIGNPATH_PROJECT_SLUG`、`SIGNPATH_SIGNING_POLICY_SLUG`
- [ ] 🤖 ワークフローに署名ステップを追加（`signpath/github-action-submit-signing-request`）
      - 順番が要る: **① dist（Mojicast.exe）を署名 → ② 署名済み exe でインストーラを作る →
        ③ setup.exe を署名**。Inno Setup のインストーラは SignPath が中を開けないので
        1 回では済まない（＝リリースごとに承認が 2 回）
      - 署名ステップは environment（例 `release`）で保護し、main/タグからのみ実行できるようにする
- [ ] 🤖 署名後の検証ステップ: `Get-AuthenticodeSignature` が `Valid`、発行者が SignPath Foundation

## 5. リリースごとの署名承認の流れ（運用開始後）

1. 🤖 いつも通り feat/* → develop → main、`APP_VERSION` と `latest.json` を更新
2. 👤 Actions で「Release build」を実行（main・`expected_version` 指定）
3. 👤 SignPath から承認依頼が届く → **ビルド元のコミットが main の想定コミットか**を確認して承認（1 回目: Mojicast.exe）
4. 👤 2 回目の承認（setup.exe）
5. 🤖 署名済み artifact で `smoke_test.ps1` を回す（Zip 版・インストーラ版）
6. 👤 `gh release create` で署名済みの Zip / setup.exe / setup.zip を公開（Mac 版は従来通り）
7. 👤 BOOTH・プロダクトページの更新（従来通り）

承認で見るべき点（SignPath の条件より: ビルドスクリプトと CI 設定の変更は特に注意）:
- 前回リリースから `.github/workflows/`、`Mojicast.spec`、`build_bundle.ps1`、
  `make_installer.ps1`、`installer/Mojicast.iss`、`requirements.txt` に変更があれば中身を確認

## 6. 署名後に変わること（知っておくこと）

- 署名の発行者名（Publisher）は **「SignPath Foundation」** になる（「癒色えも」ではない）。
  UAC やファイルのプロパティにそう表示される
- SmartScreen の警告は「署名した瞬間に消える」とは限らない（評判の蓄積で消える）
- 条件違反（非 OSI 部品の混入・承認なしの署名・プライバシーポリシーの不備など）が
  あると、予告なく証明書を失効されうる。新しい依存や外部通信を足すときは
  LICENSE_INVENTORY.md と PRIVACY を更新する
- アンインストーラ（unins000.exe）は Inno Setup がビルド中に作るので、そのままでは未署名のまま
  （署名するには ISCC に SignTool 連携が要り、SignPath だとさらに承認が 1 回増える）
