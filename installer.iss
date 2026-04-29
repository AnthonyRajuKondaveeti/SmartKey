; Smart Keyboard -- Inno Setup Installer Script
; -----------------------------------------------
; Prerequisites:
;   1. Install Inno Setup 6 (free): https://jrsoftware.org/isdl.php
;   2. Run build_release.bat first to populate dist\SmartKeyboard\ (with models inside)
;   3. Open this file in Inno Setup IDE and press Compile  (or: iscc installer.iss)
;
; Output: dist\SmartKeyboard-v1.0.0-Setup.exe

#define MyAppName      "Smart Keyboard"
#define MyAppVersion   "1.0.0"
#define MyAppPublisher "Smart Keyboard"
#define MyAppExeName   "SmartKeyboard.exe"
#define MyAppSourceDir "dist\SmartKeyboard"

[Setup]
AppId={{6F3A9C1E-2D47-4B8A-9E5F-0C1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
; Output installer to dist\ so build_release.bat can find it
OutputDir=dist
OutputBaseFilename=SmartKeyboard-v{#MyAppVersion}-Setup
; LZMA solid compression -- slower to compile but smallest output
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; Only x64 Windows supported (onnxruntime, PyQt5 are 64-bit)
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Ask for admin so we can write to Program Files
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayName={#MyAppName}
; Show "Installed successfully" finish page with launch option
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";  Description: "{cm:CreateDesktopIcon}";           GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startuprun";   Description: "Start Smart Keyboard with Windows"; GroupDescription: "Startup options:";   Flags: unchecked

[Files]
; NOTE: run build_release.bat first so models\ is present inside dist\SmartKeyboard\
Source: "{#MyAppSourceDir}\{#MyAppExeName}";                           DestDir: "{app}";                              Flags: ignoreversion
Source: "{#MyAppSourceDir}\_internal\*";                               DestDir: "{app}\_internal";                    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#MyAppSourceDir}\models\indictrans2\*";                      DestDir: "{app}\models\indictrans2";           Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#MyAppSourceDir}\models\grammar\coedit-small_int8\*";        DestDir: "{app}\models\grammar\coedit-small_int8"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#MyAppSourceDir}\UserManual.txt";                            DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Start Menu
Name: "{group}\{#MyAppName}";              Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}";    Filename: "{uninstallexe}"
; Desktop (user-opted-in)
Name: "{commondesktop}\{#MyAppName}";      Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Auto-start with Windows (user-opted-in)
; HKA resolves to HKCU for per-user installs and HKLM for admin installs
Root: HKA; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "{#MyAppName}"; \
  ValueData: """{app}\{#MyAppExeName}"""; \
  Flags: uninsdeletevalue; Tasks: startuprun

[Run]
; Offer to launch after install finishes
Filename: "{app}\{#MyAppExeName}"; \
  Description: "Launch {#MyAppName} now"; \
  Flags: nowait postinstall skipifsilent

[UninstallRun]
; Kill the running process before uninstalling
Filename: "taskkill.exe"; Parameters: "/f /im {#MyAppExeName}"; Flags: skipifdoesntexist runhidden; RunOnceId: "KillProcess"
