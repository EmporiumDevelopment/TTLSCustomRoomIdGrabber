; Inno Setup script: makes RoomIDOverlay-Setup.exe
; Installs per-user (no admin prompt), like Discord.

[Setup]
AppName=Room ID Overlay
AppVersion=1.0.0
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

[Run]
Filename: "{app}\RoomIDOverlay.exe"; Description: "Open Room ID Overlay now"; Flags: nowait postinstall skipifsilent
