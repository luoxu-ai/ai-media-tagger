#define MyAppName "AI媒体标签工具"
#define MyAppVersion "1.2.7"
#define MyAppPublisher "深圳市艾润特贸易有限公司"
#define MyAppExeName "AI媒体标签工具.exe"

[Setup]
AppId={{E26D4E36-536F-4CB5-A45F-C9AE837B86F7}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://github.com/luoxu-ai/ai-media-tagger
AppSupportURL=https://github.com/luoxu-ai/ai-media-tagger
AppUpdatesURL=https://github.com/luoxu-ai/ai-media-tagger/releases
DefaultDirName={localappdata}\Programs\{#MyAppName}
UsePreviousAppDir=yes
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupIconFile=assets\app-icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallFilesDir={app}\卸载程序
OutputDir=dist_installer
OutputBaseFilename=AI媒体标签工具安装程序
CloseApplications=force
RestartApplications=yes
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=AI媒体标签工具安装程序
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "chinesesimp"; MessagesFile: "installer_languages\ChineseSimplified.isl"

[Files]
Source: "dist\{#MyAppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
Name: "{app}\logs"
Name: "{app}\卸载程序"; Attribs: hidden

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
; Always recreate the same desktop shortcut during install and online update.
; This replaces shortcuts that still target an older installation directory.
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{app}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autoprograms}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall
