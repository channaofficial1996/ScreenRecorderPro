; Screen Recorder Pro installer. Built by build.py, which passes AppVersion, ExeSource and IconFile.
; Installs per user, without admin rights: the in-app updater has to be able to replace the exe.

#define AppName "Screen Recorder Pro"
#define AppExe "ScreenRecorderPro.exe"

[Setup]
AppId={{232608D9-696B-4925-94D8-CEB8C66C972A}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Cambo Ads
AppCopyright=© 2026 Cambo Ads. Licensed under AGPL-3.0.
AppPublisherURL=https://github.com/channaofficial1996/ScreenRecorderPro
AppSupportURL=https://github.com/channaofficial1996/ScreenRecorderPro/issues
AppUpdatesURL=https://github.com/channaofficial1996/ScreenRecorderPro/releases/latest
VersionInfoCompany=Cambo Ads
VersionInfoCopyright=© 2026 Cambo Ads. Licensed under AGPL-3.0.
VersionInfoVersion={#AppVersion}
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir=dist
OutputBaseFilename=ScreenRecorderPro-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#ExeSource}"; DestDir: "{app}"; DestName: "{#AppExe}"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
; The Start Menu entry is what makes the app show up in Windows search.
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; left behind by the in-app updater
Type: files; Name: "{app}\{#AppExe}.old"
Type: files; Name: "{app}\{#AppExe}.new"
