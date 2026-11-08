; Inno Setup script: makes RoomIDOverlay-Setup.exe
; Installs per-user (no admin prompt), like Discord.

#define VersionFile FileOpen("version.txt")
#define AppVer Trim(FileRead(VersionFile))
#expr FileClose(VersionFile)

[Setup]
AppName=Room ID Overlay
AppVersion={#AppVer}
AppPublisher=StellarPE
DefaultDirName={localappdata}\Programs\RoomIDOverlay
DefaultGroupName=Room ID Overlay
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=RoomIDOverlay-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\RoomIDOverlay.exe

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "dist\RoomIDOverlay\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\Room ID Overlay"; Filename: "{app}\RoomIDOverlay.exe"
Name: "{userdesktop}\Room ID Overlay"; Filename: "{app}\RoomIDOverlay.exe"; Tasks: desktopicon

[Registry]
; The "open with LIVE Studio" setting is written by the app; remove it on uninstall.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "RoomIDOverlayWatcher"; Flags: dontcreatekey uninsdeletevalue

[Run]
Filename: "{app}\RoomIDOverlay.exe"; Description: "Open Room ID Overlay now"; Flags: nowait postinstall skipifsilent
; Updates started from inside the app run silently: reopen it afterwards.
Filename: "{app}\RoomIDOverlay.exe"; Flags: nowait; Check: WizardSilent

[Code]
// The app and its background watcher run from the files being replaced or removed.
procedure StopRunningApp;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im RoomIDOverlay.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(1000);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningApp;
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopRunningApp;
  Result := True;
end;
