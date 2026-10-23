; Inno Setup script for the playlist creator; build it with packaging\build.ps1, which passes /DAppVersion.
#ifndef AppVersion
  #error AppVersion is not defined; build with packaging\build.ps1
#endif

#define AppName "Playlist creator"
#define AppExe "PlaylistCreator.exe"
#define RepoUrl "https://github.com/herbook/spotify_tools"

[Setup]
; Never change the AppId: it is how a newer installer finds and updates the installed version
AppId={{6D0B6F4E-3C0A-4B8E-9F5B-2A7E1C9D4A31}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Herman Böök
AppPublisherURL={#RepoUrl}
AppCopyright=Copyright (c) 2026 Herman Böök
; Per-user install: no administrator rights, and {autopf} then resolves to %LOCALAPPDATA%\Programs
PrivilegesRequired=lowest
DefaultDirName={autopf}\PlaylistCreator
DisableDirPage=no
UsePreviousAppDir=yes
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/ultra64
SolidCompression=yes
LicenseFile=..\LICENSE
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
WizardStyle=modern
OutputDir=..\dist
OutputBaseFilename=PlaylistCreator-{#AppVersion}-setup

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
; The header subtitle of the license page is replaced by the distribution notice built in InitializeWizard
LicenseLabel=

[Tasks]
Name: "startmenuicon"; Description: "Create a &Start Menu shortcut"; Flags: unchecked
Name: "desktopicon"; Description: "Create a &Desktop shortcut"

[InstallDelete]
; Remove the previous version's bundled files so none are left stale; the logs, seeds and .cache are not in here
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\PlaylistCreator\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: startmenuicon
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The Spotify login token is always removed
Type: files; Name: "{app}\.cache"

[Code]
var
  NoticePrefix, NoticeLink, NoticeSemicolon, NoticeRest: TNewStaticText;

procedure OpenRepository(Sender: TObject);
var
  ErrorCode: Integer;
begin
  ShellExec('open', '{#RepoUrl}', '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

// Creates a hidden label in the page header, where the subtitle sits
function AddNoticeLabel(Caption: String; Left: Integer): TNewStaticText;
begin
  Result := TNewStaticText.Create(WizardForm);
  Result.Parent := WizardForm.PageDescriptionLabel.Parent;
  Result.AutoSize := True;
  Result.WordWrap := False;
  Result.Caption := Caption;
  Result.Left := Left;
  Result.Top := WizardForm.PageDescriptionLabel.Top;
  Result.Visible := False;
end;

// Builds "Get the latest version from <GitHub link>; do not redistribute this installer." in the license page header
procedure InitializeWizard;
var
  Description: TNewStaticText;
begin
  Description := WizardForm.PageDescriptionLabel;
  NoticePrefix := AddNoticeLabel('Get the latest version from', Description.Left);
  NoticeLink := AddNoticeLabel('GitHub', NoticePrefix.Left + NoticePrefix.Width + ScaleX(4));
  NoticeLink.Font.Color := clBlue;
  NoticeLink.Font.Style := [fsUnderline];
  NoticeLink.Cursor := crHand;
  NoticeLink.OnClick := @OpenRepository;
  NoticeSemicolon := AddNoticeLabel(';', NoticeLink.Left + NoticeLink.Width);
  NoticeRest := AddNoticeLabel('do not redistribute this installer.', NoticeSemicolon.Left + NoticeSemicolon.Width + ScaleX(4));
  // A header too narrow for one line gets the last part on a second line instead of cutting it off
  if NoticeRest.Left + NoticeRest.Width > Description.Left + Description.Width then
  begin
    NoticeRest.Left := Description.Left;
    NoticeRest.Top := Description.Top + NoticeRest.Height;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
var
  OnLicensePage: Boolean;
begin
  OnLicensePage := CurPageID = wpLicense;
  NoticePrefix.Visible := OnLicensePage;
  NoticeLink.Visible := OnLicensePage;
  NoticeSemicolon.Visible := OnLicensePage;
  NoticeRest.Visible := OnLicensePage;
end;

// Logs and seeds are the user's data: ask before removing them, and keep them in a silent uninstall
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  LogDir, SeedsDir: String;
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
  begin
    LogDir := ExpandConstant('{app}\log');
    SeedsDir := ExpandConstant('{app}\seeds');
    if DirExists(LogDir) or DirExists(SeedsDir) then
    begin
      if MsgBox('Also delete your logs and seeds?' + #13#10 + #13#10 + ExpandConstant('{app}'),
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        DelTree(LogDir, True, True, True);
        DelTree(SeedsDir, True, True, True);
        // Only succeeds when the folder is empty
        RemoveDir(ExpandConstant('{app}'));
      end;
    end;
  end;
end;
