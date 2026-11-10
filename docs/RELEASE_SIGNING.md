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

---

## 0. 申請前に片付けること（ライセンス）

SignPath の条件は「全コンポーネントが OSI 承認ライセンス」。現状のビルドは満たしていない
（詳細は LICENSE_INVENTORY.md）。

- [ ] 👤 **Intel oneMKL / OpenMP（ctranslate2）の扱いを決める**
  - 案A: CTranslate2 を MKL 無しで CI ビルドする（`WITH_MKL=OFF`, `WITH_DNNL=ON`,
    `OPENMP_RUNTIME=COMP`）。翻訳速度・メモリが変わるので実測が要る
  - 案B: 申請時に正直に書いて SignPath に判断を仰ぐ（下書きはこの前提）
- [ ] 🤖 ASIO 版 PortAudio DLL（`libportaudio64bit-asio.dll`）を spec で除外する
      （Mojicast は ASIO 未使用。`a.binaries` / `a.datas` から "asio" を落とす）
- [ ] 🤖 ついでに不要物を除外: `pywebview-android.jar`、（動作確認のうえ）`dbghelp.dll` `dbgcore.dll`
- [ ] 👤 LICENSE 末尾の日本語注記を CREDITS.md へ移すか決める
      （今は GitHub が MIT と判定できず「Other」表示。審査で不利になりうる）
- [ ] 🤖 CREDITS.md を実物に合わせる（Pillow/hf_xet は除外済み・oneMKL/WebView2 SDK/OpenSSL/protobuf を追記）

## 1. exe へのバージョン情報の付与（次のリリースで）

SignPath は署名する全ファイルに `ProductName` と `ProductVersion` を要求する。
現状: `Mojicast.exe` は**全項目が空**、`setup.exe` は ProductName=Mojicast /
ProductVersion=版 が入っている（Inno Setup の既定）が FileVersion は空。

- [ ] 🤖 `Mojicast.spec` に VERSIONINFO を足す（Windows のみ・APP_VERSION から生成）。
      下のコードは dist の exe の**コピー**に書き込んで動作確認済み
      （ProductVersion=`1.2.0-beta1` / FileVersionRaw=`1.2.0.0` になる）

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

- [ ] 🤖 `installer/Mojicast.iss` に追加: `VersionInfoVersion`（数値4つ。beta は `1.2.0.0` のように
      落とす → `make_installer.ps1` で `/DAppVersionNum=` を計算して渡す）、
      `VersionInfoProductName=Mojicast`、`VersionInfoCopyright`
- [ ] 🤖 確認: `(Get-Item dist\Mojicast\Mojicast.exe).VersionInfo` と setup.exe の両方で
      ProductName=`Mojicast`、ProductVersion=`APP_VERSION` になっていること
- [ ] 👤 会社名の表記を決める（`癒色えも` か `ishiki-emo` か。setup.exe は今 `癒色えも`）

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

既知の注意点:
- Pillow が無いとアイコン生成が黙ってスキップされ、installer の `SetupIconFile` で失敗する。
  ワークフローでは明示的に入れている
- Inno Setup はランナーに無ければ choco で入れる

## 3. README への Code signing policy 節の反映（承認後）

承認前に載せると「署名していないのに署名ポリシーを掲げる」ことになるので、**承認後**にやる。
ただし申請時に「どこに載せるか」を聞かれるので、場所だけ先に決めておく。

- [ ] 👤 プライバシーポリシーの公開場所を決める（`docs/PRIVACY.md` か プロダクトページか）
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
