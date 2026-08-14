; Inno Setup — instalador único Aibox-Setup.exe
; Destino: C:\Aibox  |  APKs são baixados pelo app após instalar

#define MyAppName "Aibox"
#ifndef MyAppVersion
#define MyAppVersion "1.0.0"
#endif
#define MyAppPublisher "Intelite"
#define MyAppExeName "Aibox.exe"

[Setup]
AppId={{8F3C2A91-A1B0-4E2D-9C7F-AIBOX0000001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppCopyright=Copyright (C) {#MyAppPublisher}
DefaultDirName=C:\Aibox
DisableDirPage=no
DisableProgramGroupPage=yes
DefaultGroupName={#MyAppName}
OutputDir=..\dist\release
OutputBaseFilename=Aibox-Setup
#ifexist "..\aibox\Aibox.ico"
SetupIconFile=..\aibox\Aibox.ico
#endif
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=yes
RestartApplications=no
AllowNoIcons=yes
SetupLogging=no

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Área de Trabalho"; GroupDescription: "Atalhos:"; Flags: checkedonce

[Files]
Source: "..\dist\Aibox\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Unblock-File -LiteralPath '{app}' -Recurse -ErrorAction SilentlyContinue; try {{ Add-MpPreference -ExclusionPath '{app}' }} catch {{ }}"""; \
    Flags: runhidden waituntilterminated; \
    StatusMsg: "Permitindo o Aibox no Windows…"
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir o Aibox agora"; Flags: nowait postinstall skipifsilent; WorkingDir: "{app}"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\platform-tools"
