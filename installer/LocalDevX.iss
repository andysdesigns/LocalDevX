; Inno Setup script for LocalDevX.
; Built by build.bat (python build.py), which passes the version from localdevx/__init__.py:
;   ISCC /DAppVersion=1.0.0 installer\LocalDevX.iss
; Created by Andy Christian S. Loyola <acl20032003@gmail.com>

#ifndef AppVersion
  #error AppVersion is not defined - build with build.bat
#endif
#define AppName "LocalDevX"
; overridable for automated testing only (build.py never sets these)
#ifndef SourceDir
  #define SourceDir "..\dist\LocalDevX"
#endif
#ifndef Privileges
  #define Privileges "admin"
#endif
#ifndef AppGuid
  #define AppGuid "0659FC78-3CA6-42CC-AF11-8E55D959F0BA"
#endif
#define AppAuthor "Andy Christian S. Loyola"
#define AppEmail "acl20032003@gmail.com"
#define AppExe "LocalDevX.exe"

[Setup]
; AppId identifies LocalDevX to Windows for upgrades/uninstall - never change it.
AppId={{{#AppGuid}}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} v{#AppVersion}
AppPublisher={#AppAuthor}
AppPublisherURL=mailto:{#AppEmail}
AppSupportURL=mailto:{#AppEmail}
AppContact={#AppEmail}
AppCopyright=Copyright (c) 2026 {#AppAuthor}
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppAuthor}
VersionInfoDescription={#AppName} v{#AppVersion} Setup
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; LocalDevX edits the hosts file and controls Apache, so it always runs as Administrator.
PrivilegesRequired={#Privileges}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\release
OutputBaseFilename={#AppName}-{#AppVersion}-setup
SetupIconFile=..\build\localdevx.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} v{#AppVersion}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; A running LocalDevX (often hidden in the tray) is closed automatically - see PrepareToInstall
; and [UninstallRun] - so no AppMutex prompt is needed.
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "&Start LocalDevX when I sign in to Windows (minimized to the tray)"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; files from an older version that the new one no longer ships
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "{#AppName} v{#AppVersion} - local development manager for XAMPP"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Parameters: "--enable-startup"; StatusMsg: "Setting up Start with Windows..."; Tasks: startup; Flags: runhidden waituntilterminated
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName} now"; Flags: nowait postinstall skipifsilent runascurrentuser

[UninstallRun]
; close the running app, remove the Start-with-Windows task and the firewall rule
Filename: "{app}\{#AppExe}"; Parameters: "--uninstall-cleanup"; Flags: runhidden waituntilterminated; RunOnceId: "LocalDevXCleanup"

[Code]
// Close a running LocalDevX (it hides in the tray, so a normal close isn't enough) before files are replaced.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  if FileExists(ExpandConstant('{app}\{#AppExe}')) then
    Exec(ExpandConstant('{app}\{#AppExe}'), '--quit', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;

// Settings are kept on uninstall unless the user asks to remove them.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\{#AppName}');
    if DirExists(DataDir) then
      if SuppressibleMsgBox('Also delete your LocalDevX settings, site list, backups and local certificate authority?' + #13#10 + #13#10 +
                            DataDir + #13#10 + #13#10 +
                            'Your website files, hosts file and Apache configuration are not touched either way. ' +
                            'Choose No to keep them for a future reinstall or the portable version.',
                            mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
