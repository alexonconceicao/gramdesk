; Instalador do Gramdesk (Inno Setup 6). Instala só para o usuário, sem pedir administrador.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F2C8E3B-6A1F-4D1B-9C1E-5B0E7A2D9F41}
AppName=Gramdesk
AppVersion={#AppVersion}
AppPublisher=Gramdesk
AppPublisherURL=https://github.com/alexonconceicao/gramdesk
AppSupportURL=https://github.com/alexonconceicao/gramdesk/issues
DefaultDirName={localappdata}\Programs\Gramdesk
DefaultGroupName=Gramdesk
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=Gramdesk-Setup-{#AppVersion}
SetupIconFile=gramdesk.ico
UninstallDisplayIcon={app}\Gramdesk.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\..\LICENSE

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\..\dist\Gramdesk\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Gramdesk"; Filename: "{app}\Gramdesk.exe"; AppUserModelID: "io.github.alexonconceicao.Gramdesk"
Name: "{userdesktop}\Gramdesk"; Filename: "{app}\Gramdesk.exe"; Tasks: desktopicon; AppUserModelID: "io.github.alexonconceicao.Gramdesk"

[Run]
Filename: "{app}\Gramdesk.exe"; Description: "{cm:LaunchProgram,Gramdesk}"; Flags: nowait postinstall skipifsilent
