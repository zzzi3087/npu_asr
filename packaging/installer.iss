#ifndef BundleDir
  #error BundleDir must point to the tested PyInstaller directory
#endif
#ifndef ReleaseDir
  #define ReleaseDir "..\dist"
#endif
[Setup]
AppId={{7795B538-B180-4CC8-9DE4-26FCE739732A}
AppName=实时字幕
AppVersion=0.2.2
AppPublisher=npu_asr contributors
AppPublisherURL=https://github.com/zzzi3087/npu_asr
DefaultDirName={localappdata}\Programs\LiveCaptions
DefaultGroupName=实时字幕
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
Compression=lzma2/normal
SolidCompression=yes
OutputDir={#ReleaseDir}
OutputBaseFilename=LiveCaptions-0.2.2-win64-setup
SetupIconFile=..\resources\assets\app.ico
UninstallDisplayIcon={app}\LiveCaptions.exe
WizardStyle=modern
DisableWelcomePage=no
CloseApplications=no
RestartApplications=no
LicenseFile=..\licenses\THIRD_PARTY_NOTICES.md
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："
[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\实时字幕"; Filename: "{app}\LiveCaptions.exe"
Name: "{autodesktop}\实时字幕"; Filename: "{app}\LiveCaptions.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\LiveCaptions.exe"; Description: "启动实时字幕"; Flags: nowait postinstall skipifsilent
