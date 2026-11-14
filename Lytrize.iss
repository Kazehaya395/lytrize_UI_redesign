; Lytrize Windows installer script for Inno Setup 6 / 7.
;
; Invoked by build_exe.ps1 -MakeInstaller (which passes /DAppVersion=x.y.z).
; Can also be run directly:  ISCC.exe "/DAppVersion=1.0.0" Lytrize.iss
;
; Expects the default folder build layout:  dist\Lytrize\Lytrize.exe
; (i.e. build_exe.ps1 WITHOUT -OneFile, which produces dist\Lytrize.exe
;  and is therefore not supported by this script).

#define AppName      "Lytrize"
#define AppPublisher "Lytrize"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

; AppId is the identity used for upgrades and uninstall entries. NEVER change
; it between releases or Windows will see the new installer as a new product.
[Setup]
AppId={{9742252C-A0AA-4358-ACF0-A625AF307EC7}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
PrivilegesRequired=lowest
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\Lytrize.exe
OutputDir=dist
OutputBaseFilename=Lytrize-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=yes

; Setup icon: only when the repo actually ships one (mirrors build_exe.ps1,
; which falls back to a default exe icon with a warning instead of failing).
#if FileExists("backend\assets\lytrize.ico")
SetupIconFile=backend\assets\lytrize.ico
#endif

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "dist\Lytrize\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\Lytrize.exe"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\Lytrize.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Lytrize.exe"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
