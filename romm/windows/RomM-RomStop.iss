; Inno Setup script for RomM-RomStop-Setup.exe, the Windows 11 installer.
; Build: iscc /DAppVersion=5.4.1 RomM-RomStop.iss  (CI does this in romm-installer.yml)
; The setup only gathers settings; Install-RomM.ps1 does the work, so a re-run repairs or upgrades.

#ifndef AppVersion
  #define AppVersion "5.4.1"
#endif

[Setup]
AppId={{8C1B7E42-5D0A-4E3B-9C55-6A7F1D2E9B10}
AppName=RomM with RomStop
AppVersion={#AppVersion}
AppPublisher=RomStop
AppPublisherURL=https://github.com/itsdinkd/RomStop
DefaultDirName={sd}\RomM
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19045
OutputBaseFilename=RomM-RomStop-Setup
OutputDir=out
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=RomM with RomStop
SetupLogging=yes

[Files]
Source: "Install-RomM.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "Update-RomM.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "Uninstall-RomM.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\RomM\Open RomM"; Filename: "http://localhost:{code:GetPort}"
Name: "{autoprograms}\RomM\Update RomM"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -NoExit -File ""{app}\scripts\Update-RomM.ps1"" -InstallDir ""{app}"""
Name: "{autoprograms}\RomM\Install log"; Filename: "{app}\install.log"

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\Uninstall-RomM.ps1"" -InstallDir ""{app}"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveRomM"

[Code]
var
  LibraryPage: TInputDirWizardPage;
  SettingsPage: TInputQueryWizardPage;
  KeysPage: TInputQueryWizardPage;
  RestartNeeded: Boolean;
  SetupFailed: Boolean;

procedure InitializeWizard;
begin
  LibraryPage := CreateInputDirPage(wpSelectDir, 'ROM library',
    'Where are your ROMs?',
    'RomM reads games from <folder>\roms\<platform>, for example D:\ROMs\roms\nes. ' +
    'Pick an existing library or an empty folder; RomM never deletes your files.',
    False, '');
  LibraryPage.Add('Library folder');
  LibraryPage.Values[0] := GetPreviousData('LibraryDir', ExpandConstant('{sd}\RomM\library'));

  SettingsPage := CreateInputQueryPage(LibraryPage.ID, 'Web address',
    'Which port should RomM use?',
    'You will open RomM at http://localhost:<port>, or from other devices at http://<this PC>:<port>.');
  SettingsPage.Add('Port:', False);
  SettingsPage.Values[0] := GetPreviousData('Port', '8080');

  KeysPage := CreateInputQueryPage(SettingsPage.ID, 'Game metadata (optional)',
    'Accounts RomM uses to fetch box art and game info',
    'Leave these blank to keep what you entered before, or add them later in the .env file in the install folder. Hasheous is on without an account. ' +
    'RomStop needs no account.');
  KeysPage.Add('ScreenScraper user:', False);
  KeysPage.Add('ScreenScraper password:', True);
  KeysPage.Add('IGDB client ID:', False);
  KeysPage.Add('IGDB client secret:', True);
  KeysPage.Add('RetroAchievements API key:', True);
  KeysPage.Add('SteamGridDB API key:', True);
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'LibraryDir', LibraryPage.Values[0]);
  SetPreviousData(PreviousDataKey, 'Port', SettingsPage.Values[0]);
end;

function GetPort(Param: String): String;
begin
  Result := SettingsPage.Values[0];
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Port: Integer;
begin
  Result := True;
  if CurPageID = SettingsPage.ID then
  begin
    Port := StrToIntDef(SettingsPage.Values[0], 0);
    if (Port < 1) or (Port > 65535) then
    begin
      MsgBox('Enter a port number between 1 and 65535.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function JsonString(S: String): String;
begin
  StringChangeEx(S, '\', '\\', True);
  StringChangeEx(S, '"', '\"', True);
  Result := '"' + S + '"';
end;

procedure RunInstaller;
var
  SettingsFile, Json, Params: String;
  Code: Integer;
begin
  SettingsFile := ExpandConstant('{tmp}\romm-settings.json');
  Json := '{' +
    '"LibraryDir":' + JsonString(LibraryPage.Values[0]) + ',' +
    '"Port":' + SettingsPage.Values[0] + ',' +
    '"SCREENSCRAPER_USER":' + JsonString(KeysPage.Values[0]) + ',' +
    '"SCREENSCRAPER_PASSWORD":' + JsonString(KeysPage.Values[1]) + ',' +
    '"IGDB_CLIENT_ID":' + JsonString(KeysPage.Values[2]) + ',' +
    '"IGDB_CLIENT_SECRET":' + JsonString(KeysPage.Values[3]) + ',' +
    '"RETROACHIEVEMENTS_API_KEY":' + JsonString(KeysPage.Values[4]) + ',' +
    '"STEAMGRIDDB_API_KEY":' + JsonString(KeysPage.Values[5]) + '}';
  SaveStringToFile(SettingsFile, Json, False);
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\scripts\Install-RomM.ps1') +
    '" -InstallDir "' + ExpandConstant('{app}') + '" -SettingsFile "' + SettingsFile + '" -NoBrowser';
  WizardForm.StatusLabel.Caption := 'Setting up WSL2, Docker Desktop and RomM. A console window shows progress; this can take 10 minutes or more.';
  if not Exec('powershell.exe', Params, '', SW_SHOW, ewWaitUntilTerminated, Code) then
    Code := -1;
  DeleteFile(SettingsFile);
  if Code = 3010 then
    RestartNeeded := True
  else if Code <> 0 then
  begin
    SetupFailed := True;
    MsgBox('RomM setup did not finish (code ' + IntToStr(Code) + '). Details are in ' +
      ExpandConstant('{app}\install.log') + '. Fix the problem and run this installer again; it picks up where it stopped.',
      mbError, MB_OK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RunInstaller;
end;

function NeedRestart: Boolean;
begin
  Result := RestartNeeded;
end;

procedure CurPageChanged(CurPageID: Integer);
var
  ErrorCode: Integer;
begin
  if (CurPageID = wpFinished) and not RestartNeeded and not SetupFailed then
    ShellExec('open', 'http://localhost:' + SettingsPage.Values[0], '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;
