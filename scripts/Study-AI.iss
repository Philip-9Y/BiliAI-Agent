; Inno Setup 安装包模板（可选）
; 先运行 scripts/build_windows.ps1 生成 dist\Study-AI
; 再用 Inno Setup Compiler 编译本文件

#define MyAppName "Study-AI"
#define MyAppVersion "1.0.0"
#define MyAppExeName "Study-AI.exe"

[Setup]
AppId={{6E8F3F0B-9D4A-4E8E-9A7D-6C2B1E5A8F11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=..\dist
OutputBaseFilename=Study-AI-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "..\dist\Study-AI\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
