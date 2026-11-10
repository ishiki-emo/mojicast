<!--
下書き: README.md の「## Code signing policy」節に貼る想定（未反映）。
SignPath Foundation の条件で、ホームページ（README）と配布ページ（Releases / BOOTH /
プロダクトページ）に "Code signing policy" という語の見出しかリンクが必要。
文言の1行目（Free code signing provided by ...）は SignPath 指定の定型文なので変えない。
申請が承認されるまでは README に載せない（まだ署名していないのに書くと虚偽になる）。
-->

## Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io/),
certificate by [SignPath Foundation](https://signpath.org/).

Windows 版の `Mojicast.exe` とインストーラ（`Mojicast-v*-win-x64-setup.exe`）は、
GitHub Actions 上でこのリポジトリのソースから自動ビルドされたものだけを署名しています。
署名には毎回、下記の承認者による手動承認が必要です。

The Windows binaries (`Mojicast.exe` and the installer) are signed only when they
are built from this repository by GitHub Actions. Every signing request is
approved manually by an approver listed below.

### Team roles

| Role | Members |
|---|---|
| Committers and reviewers | [ishiki-emo](https://github.com/ishiki-emo) |
| Approvers | [ishiki-emo](https://github.com/ishiki-emo) |

- Mojicast は個人開発です。外部からのプルリクエストは、マージ前に必ず上記レビュアーが確認します。
  Pull requests from outside contributors are always reviewed by a reviewer before merging.
- チームメンバーは GitHub と SignPath の両方で多要素認証（MFA）を有効にしています。
  All team members use multi-factor authentication for GitHub and SignPath.
- 署名されるのは本プロジェクトのバイナリだけです。同梱しているサードパーティの
  ライブラリ（DLL 等）は署名しません。
  Only this project's own binaries are signed. Bundled third-party libraries are not.

### Privacy

[Privacy policy](docs/PRIVACY.md)

このプログラムは、音声・字幕テキスト・設定などの利用者のデータを外部へ送信しません。
外部への通信は、AI モデルの初回ダウンロード（Hugging Face）と更新確認（GitHub）だけです。

This program does not send your audio, captions, or settings anywhere. It only
connects to the internet to download AI models on first use (Hugging Face) and
to check for updates (GitHub). See the privacy policy for details.
