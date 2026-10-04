; Mojicast インストーラ（Inno Setup 6）
;
; 目的: Zip 展開で全ファイルに付くダウンロード印（MOTW）をなくす。インストーラが
; 書き出したファイルには印が付かないため、.NET/clr のブロックが構造的に起きない。
; （setup.exe 自体は未署名なので SmartScreen は初回に1度出る。消すにはコード署名が要る）
;
; ビルド: .\make_installer.ps1   （版は app_server.py から取る・dist\Mojicast\ が必要）
;
; 設計:
; - ユーザー単位インストール（%LOCALAPPDATA%\Programs\Mojicast・UAC なし）。
;   Windows 版は設定・単語・モデルを exe の隣（data\ models\ logs\）に書くため、
;   Program Files には置けない（apppaths.py: Windows 凍結は DATA_BASE = exe のフォルダ）
; - 引き継ぎ: インストール先に今使っている Mojicast フォルダを選べば、プログラムだけ
;   入れ替わり data\ models\ はそのまま残る（Zip の上書き展開と同じ考え方）
; - data\ models\ logs\ はインストーラが入れたファイルではないので、アップデートで
;   上書きされず、アンインストールでも既定では残す（消すかは最後に聞く）

#ifndef AppVersion
  #error AppVersion を /DAppVersion=x.y.z で渡してください（make_installer.ps1 経由で実行）
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Mojicast"
#endif
#ifndef OutputDir
  #define OutputDir ".."
#endif

[Setup]
; AppId は固定（変えると別アプリ扱いになり、アップデートで二重インストールになる）
AppId={{A822A5B5-56E2-47B9-8A42-876DA1610B25}
AppName=Mojicast
AppVersion={#AppVersion}
AppVerName=Mojicast {#AppVersion}
AppPublisher=癒色えも
AppPublisherURL=https://github.com/ishiki-emo/mojicast
AppSupportURL=https://github.com/ishiki-emo/mojicast/issues
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=
DefaultDirName={autopf}\Mojicast
DefaultGroupName=Mojicast
DisableProgramGroupPage=yes
; 引き継ぎのためにインストール先は必ず見せる（2回目以降は前回の場所が既定になる）
DisableDirPage=no
UsePreviousAppDir=yes
; 既存フォルダを選んだときの確認は [Code] の FoundExisting（引き継ぎの説明つき）で出す。
; 既定の「フォルダは既に存在します」も出ると二重に聞くことになるので止める
DirExistsWarning=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=Mojicast-v{#AppVersion}-win-x64-setup
SetupIconFile=..\assets\branding\mojicast-mo-icon-light.ico
UninstallDisplayIcon={app}\Mojicast.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 言語は Windows の表示言語から自動で選ぶ（日本語/英語のどちらにも合わない時だけ聞く）
ShowLanguageDialog=auto
; 起動中の Mojicast はファイルを掴んでいるので、閉じてから入れ替える
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
japanese.DirHint=以前の Mojicast（Zip 版）を使っている場合は、そのフォルダ（Mojicast.exe があるフォルダ）を選ぶと、字幕デザイン・単語・設定・ダウンロード済みのAIモデルをそのまま引き継げます。
english.DirHint=If you already use Mojicast (zip version), choose that folder (the one containing Mojicast.exe) to keep your caption styles, words, settings and downloaded AI models.
japanese.FoundExisting=このフォルダには既存の Mojicast があります。%n%n字幕デザイン・単語・設定（data）とAIモデル（models）はそのまま引き継ぎ、プログラムだけを入れ替えます。
english.FoundExisting=This folder already contains Mojicast.%n%nYour styles, words, settings (data) and AI models (models) will be kept; only the program files are replaced.
japanese.AskDeleteData=字幕デザイン・単語・設定（data）、ダウンロードしたAIモデル（models）、ログ（logs）も削除しますか？%n%n「いいえ」を選ぶと残します（再インストールしたときにそのまま使えます）。
english.AskDeleteData=Also delete your caption styles, words and settings (data), downloaded AI models (models) and logs?%n%nChoose "No" to keep them for a later reinstall.
japanese.DesktopIcon=デスクトップにショートカットを作る
english.DesktopIcon=Create a desktop shortcut

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; Flags: unchecked

[InstallDelete]
; 旧版の実行ファイル一式の残骸を消してから入れ直す（Zip 版の「_internal を削除して
; 上書き展開」と同じ。依存DLLの組み合わせが版で変わるため、混ざると起動しない）
Type: filesandordirs; Name: "{app}\_internal"

[UninstallDelete]
; Zip 版の上に入れた場合、これらのフォルダは「インストーラが作ったもの」ではないため
; 中身を消しても空フォルダが残る。プログラムの一部（利用者のデータは無い）なので丸ごと消す
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\ui"
Type: filesandordirs; Name: "{app}\defaults"
Type: filesandordirs; Name: "{app}\ガイド"
Type: dirifempty; Name: "{app}"

[Files]
; 実行時に生成されるものは入れない（スモークテスト後の dist に残っていても混ぜない）
Source: "{#SourceDir}\*"; DestDir: "{app}"; \
  Excludes: "\data,\logs,\models,\models_conv,*.log"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Mojicast"; Filename: "{app}\Mojicast.exe"
Name: "{autoprograms}\Mojicast マニュアル"; Filename: "{app}\マニュアル.html"
; インストール先は AppData（エクスプローラーで普段は見えない）なので、設定・ログ・
; モデルの場所へ一発で行ける入口を置く（問い合わせ時の案内用）
Name: "{autoprograms}\Mojicast のフォルダを開く"; Filename: "{app}"
Name: "{autodesktop}\Mojicast"; Filename: "{app}\Mojicast.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Mojicast.exe"; Description: "{cm:LaunchProgram,Mojicast}"; \
  Flags: nowait postinstall skipifsilent

[Code]
var
  DirHintLabel: TNewStaticText;

procedure InitializeWizard;
begin
  { インストール先の画面に「引き継ぎたいなら今のフォルダを選ぶ」案内を出す }
  DirHintLabel := TNewStaticText.Create(WizardForm);
  DirHintLabel.Parent := WizardForm.SelectDirPage;
  DirHintLabel.Left := WizardForm.DirEdit.Left;
  DirHintLabel.Top := WizardForm.DirEdit.Top + WizardForm.DirEdit.Height + ScaleY(16);
  DirHintLabel.Width := WizardForm.SelectDirPage.Width - DirHintLabel.Left;
  DirHintLabel.WordWrap := True;
  DirHintLabel.AutoSize := True;
  DirHintLabel.Caption := CustomMessage('DirHint');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  { 既存の Mojicast を選んだら、データを引き継ぐことを明示する（消えないと分かるように） }
  if (CurPageID = wpSelectDir) and
     FileExists(AddBackslash(WizardDirValue) + 'Mojicast.exe') and
     DirExists(AddBackslash(WizardDirValue) + 'data') then
    SuppressibleMsgBox(CustomMessage('FoundExisting'), mbInformation, MB_OK, IDOK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  { 既定は残す（サイレントのアンインストールでも消さない） }
  if CurUninstallStep = usPostUninstall then
    if SuppressibleMsgBox(CustomMessage('AskDeleteData'), mbConfirmation,
                          MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
    begin
      DelTree(ExpandConstant('{app}\data'), True, True, True);
      DelTree(ExpandConstant('{app}\models'), True, True, True);
      DelTree(ExpandConstant('{app}\logs'), True, True, True);
      DeleteFile(ExpandConstant('{app}\translate_error.log'));
      RemoveDir(ExpandConstant('{app}'));
    end;
end;
