; 노래 악보 만들기 - Inno Setup 스크립트
; 빌드: installer\build.ps1 (stage 폴더를 만든 뒤 ISCC 로 컴파일)

#define AppName "노래 악보 만들기"
#define AppVersion "1.0.0"
#define AppId "{{6E2F7C1A-3B8D-4E5F-9A21-5C7D8E0F1B34}"
#define Stage "..\build\stage"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=cybereun
AppPublisherURL=https://github.com/cybereun/song-to-score
AppSupportURL=https://github.com/cybereun/song-to-score/issues
AppUpdatesURL=https://github.com/cybereun/song-to-score/releases
AppCopyright=Copyright (c) 2026 cybereun
VersionInfoCompany=cybereun
VersionInfoCopyright=Copyright (c) 2026 cybereun
VersionInfoDescription=노래 악보 만들기 설치 프로그램
DefaultDirName={autopf}\SongToScore
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=SongToScore_Setup_{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\assets\icon.ico
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
ExtraDiskSpaceRequired=6000000000
InfoBeforeFile=info_before.txt
LicenseFile=THIRD_PARTY_NOTICES.txt
ShowLanguageDialog=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Messages]
korean.LicenseLabel3=아래 오픈소스 구성 요소 안내를 읽어 주세요. 설치를 계속하려면 동의해야 해요.

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 만들기"; GroupDescription: "바로가기:"

[Files]
Source: "{#Stage}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app.py"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\icon.ico"; AppUserModelID: "SongToScore.App"
Name: "{group}\{#AppName} - 구성 요소 다시 설치"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\setup_env.ps1"" -AppDir ""{app}"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\icon.ico"
Name: "{group}\{#AppName} 제거"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app.py"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\icon.ico"; AppUserModelID: "SongToScore.App"; Tasks: desktopicon

[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\setup_env.ps1"" -AppDir ""{app}"""; WorkingDir: "{app}"; StatusMsg: "AI 구성 요소를 내려받아 설치하는 중이에요 (10~30분, 열린 창을 닫지 마세요)..."; Flags: waituntilterminated
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app.py"""; WorkingDir: "{app}"; Description: "노래 악보 만들기 실행"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; pip 로 설치된 라이브러리는 경로가 길어(260자 초과) 일반 삭제가 실패할 수 있어 \\?\ 경로로 먼저 지운다
Filename: "{cmd}"; Parameters: "/c rd /s /q ""\\?\{app}\python"""; Flags: runhidden waituntilterminated; RunOnceId: "RemovePython"

[UninstallDelete]
; 설치 후 pip 로 추가된 파일·캐시 정리
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\songscore\__pycache__"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: files; Name: "{app}\env_ok.flag"
Type: files; Name: "{app}\setup_log.txt"
Type: dirifempty; Name: "{app}"

[Code]
function DataDir(): String;
begin
  Result := ExpandConstant('{localappdata}\SongToScore');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and DirExists(DataDir()) then
  begin
    if UninstallSilent() or
       (MsgBox('분석한 곡 작업 파일과 내려받은 AI 모델(약 2GB)도 함께 지울까요?' + #13#10 + #13#10 +
               DataDir(), mbConfirmation, MB_YESNO) = IDYES) then
      DelTree(DataDir(), True, True, True);
  end;
end;
