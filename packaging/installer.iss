; Inno Setup kurulum betiği — TÜBİTAK / Teknofest İnovasyon Zekası
;
; Önce PyInstaller çıktısı üretilmiş olmalı (dist\InovasyonZekasi\).
; Derleme (proje kök dizininden):
;     iscc /DAppVersion=1.1.0 packaging\installer.iss
; Çıktı: Output\InovasyonZekasi-Kurulum-<sürüm>.exe

#ifndef AppVersion
  #define AppVersion "1.1.0"
#endif
#define AppName "İnovasyon Zekası"
#define AppExeName "InovasyonZekasi.exe"

[Setup]
AppId={{7C2B6E4A-3F1D-4E8B-9A57-2D6C0F4B8E21}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=İnovasyon Zekası
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\InovasyonZekasi
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Yönetici izni gerektirmez (okul bilgisayarları için); istenirse tüm kullanıcılara kurulabilir
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\Output
OutputBaseFilename=InovasyonZekasi-Kurulum-{#AppVersion}
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
LZMANumBlockThreads=4
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
turkish.OllamaDownload=Ollama'yı indir (uygulamanın çalışması için gerekli, ücretsiz)
english.OllamaDownload=Download Ollama (required, free)
turkish.LaunchApp=İnovasyon Zekası'nı başlat
english.LaunchApp=Launch Innovation AI

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\InovasyonZekasi\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "https://ollama.com/download/windows"; Description: "{cm:OllamaDownload}"; Flags: shellexec postinstall skipifsilent nowait; Check: not IsOllamaInstalled
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchApp}"; Flags: nowait postinstall skipifsilent

[Code]
function IsOllamaInstalled(): Boolean;
begin
  Result :=
    FileExists(ExpandConstant('{localappdata}\Programs\Ollama\ollama.exe')) or
    FileExists(ExpandConstant('{commonpf64}\Ollama\ollama.exe')) or
    FileExists(ExpandConstant('{commonpf}\Ollama\ollama.exe'));
end;
