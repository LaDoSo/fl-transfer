"""Build what is handed to users.  Neither result needs Python on the user's machine.

  python packaging/build.py

  release/FL-Transfer-Setup-<version>-en.exe   installer: Start menu and desktop shortcuts, uninstaller
  release/FL-Transfer-<version>-en-win64.zip   portable: unzip anywhere, start "FL Transfer.exe"
  release/FL Transfer/                         the program folder both are made from

Needs PyInstaller, Pillow (once, for the icon) and, for the installer, NSIS 3: makensis on PATH,
in the usual install folder, or unpacked under _tools/ (the portable zip from nsis.sourceforge.io).
"""
import glob
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
from fltransfer import __version__

NAME = "FL Transfer"
ICON = os.path.join(HERE, "icon.ico")
WELCOME = os.path.join(HERE, "welcome.bmp")
RELEASE = os.path.join(ROOT, "release")
WORK = os.path.join(RELEASE, "work")
FOLDER = os.path.join(RELEASE, NAME)
TKDND = os.path.join(ROOT, "vendor", "tkinterdnd2", "tkdnd", "win-x64")

VERSION_FILE = """VSVersionInfo(
  ffi=FixedFileInfo(filevers=({v}, 0), prodvers=({v}, 0), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', 'FL Transfer'),
      StringStruct('ProductName', 'FL Transfer'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('ProductVersion', '{version}'),
      StringStruct('InternalName', 'FL Transfer'),
      StringStruct('OriginalFilename', 'FL Transfer.exe'),
      StringStruct('Comments', 'Move samples, instruments and Patterns between FL Studio projects')])]),
    VarFileInfo([VarStruct('Translation', [0x0409, 1200])])])
"""

NSI = r"""Unicode true
ManifestDPIAware true
SetCompressor /SOLID lzma
RequestExecutionLevel user

!define APP "FL Transfer"
!define VERSION "@VERSION@"
!define EXE "FL Transfer.exe"
!define UNKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\FLTransfer"

Name "${APP} ${VERSION}"
OutFile "@OUT@"
InstallDir "$LOCALAPPDATA\Programs\FL Transfer"
InstallDirRegKey HKCU "Software\FLTransfer" "InstallDir"
BrandingText "${APP} ${VERSION}"

VIProductVersion "@VERSION@.0"
VIAddVersionKey /LANG=1033 "ProductName" "${APP}"
VIAddVersionKey /LANG=1033 "FileDescription" "${APP} Setup"
VIAddVersionKey /LANG=1033 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1033 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=1033 "LegalCopyright" "${APP}"

!include "MUI2.nsh"
!include "LogicLib.nsh"
!define MUI_ICON "@ICON@"
!define MUI_UNICON "@ICON@"
!define MUI_ABORTWARNING
!define MUI_WELCOMEFINISHPAGE_BITMAP "@WELCOME@"
!define MUI_WELCOMEPAGE_TEXT "This will install ${APP} ${VERSION} on this computer.$\r$\n$\r$\n${APP} moves samples, instruments and Patterns between FL Studio projects; a Pattern takes the instruments it uses with it.$\r$\n$\r$\nIt is installed for the current user only and needs no administrator rights. If ${APP} is running, close it first."
!define MUI_COMPONENTSPAGE_NODESC
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Run ${APP} now"
!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\User Guide.txt"
!define MUI_FINISHPAGE_SHOWREADME_TEXT "Open the user guide"
!define MUI_FINISHPAGE_SHOWREADME_NOTCHECKED

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

!macro StopIfRunning
  ${If} ${FileExists} "$INSTDIR\${EXE}"
    ClearErrors
    FileOpen $0 "$INSTDIR\${EXE}" a
    ${If} ${Errors}
      MessageBox MB_OK|MB_ICONEXCLAMATION "${APP} is running. Close it first, then run this program again." /SD IDOK
      Abort
    ${EndIf}
    FileClose $0
  ${EndIf}
!macroend

Section "${APP} (required)" SecMain
  SectionIn RO
  !insertmacro StopIfRunning
  SetOutPath "$INSTDIR"
  RMDir /r "$INSTDIR\_internal"
  ; what the Chinese edition leaves behind when this one is installed over it
  Delete "$INSTDIR\使用说明.txt"
  Delete "$INSTDIR\第三方许可.txt"
  Delete "$SMPROGRAMS\${APP}\使用说明.lnk"
  Delete "$SMPROGRAMS\${APP}\卸载 ${APP}.lnk"
  File /r "@FOLDER@\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\FLTransfer" "InstallDir" "$INSTDIR"
  CreateDirectory "$SMPROGRAMS\${APP}"
  CreateShortcut "$SMPROGRAMS\${APP}\${APP}.lnk" "$INSTDIR\${EXE}"
  CreateShortcut "$SMPROGRAMS\${APP}\User Guide.lnk" "$INSTDIR\User Guide.txt"
  CreateShortcut "$SMPROGRAMS\${APP}\Uninstall ${APP}.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${UNKEY}" "DisplayName" "${APP}"
  WriteRegStr HKCU "${UNKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNKEY}" "DisplayIcon" "$INSTDIR\${EXE}"
  WriteRegStr HKCU "${UNKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNKEY}" "UninstallString" "$\"$INSTDIR\Uninstall.exe$\""
  WriteRegStr HKCU "${UNKEY}" "QuietUninstallString" "$\"$INSTDIR\Uninstall.exe$\" /S"
  WriteRegDWORD HKCU "${UNKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNKEY}" "NoRepair" 1
  WriteRegDWORD HKCU "${UNKEY}" "EstimatedSize" @KB@
SectionEnd

Section "Desktop shortcut" SecDesktop
  CreateShortcut "$DESKTOP\${APP}.lnk" "$INSTDIR\${EXE}"
SectionEnd

Section "Uninstall"
  !insertmacro StopIfRunning
  Delete "$DESKTOP\${APP}.lnk"
  Delete "$SMPROGRAMS\${APP}\${APP}.lnk"
  Delete "$SMPROGRAMS\${APP}\User Guide.lnk"
  Delete "$SMPROGRAMS\${APP}\Uninstall ${APP}.lnk"
  RMDir "$SMPROGRAMS\${APP}"
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\${EXE}"
  Delete "$INSTDIR\User Guide.txt"
  Delete "$INSTDIR\Third-Party Licenses.txt"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UNKEY}"
  DeleteRegKey HKCU "Software\FLTransfer"
  IfSilent done
  MessageBox MB_YESNO|MB_ICONQUESTION "Also delete the settings and temporary files of ${APP}?$\r$\n(None of your FL projects are affected.)" IDNO done
  RMDir /r "$APPDATA\FLTransfer"
  RMDir /r "$TEMP\FLTransfer"
  done:
SectionEnd
"""


def find_makensis() -> str | None:
    found = shutil.which("makensis")
    if found:
        return found
    spots = glob.glob(os.path.join(ROOT, "_tools", "nsis*", "makensis.exe"))
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(env):
            spots.append(os.path.join(os.environ[env], "NSIS", "makensis.exe"))
    return next((s for s in spots if os.path.isfile(s)), None)


def build_program() -> None:
    if not (os.path.isfile(ICON) and os.path.isfile(WELCOME)):
        subprocess.check_call([sys.executable, os.path.join(HERE, "make_icon.py")])
    shutil.rmtree(FOLDER, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    version_file = os.path.join(WORK, "version.txt")
    with open(version_file, "w", encoding="utf-8") as f:
        f.write(VERSION_FILE.format(v=", ".join(__version__.split(".")[:3]), version=__version__))
    subprocess.check_call([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
        "--name", NAME, "--icon", ICON, "--version-file", version_file,
        "--paths", os.path.join(ROOT, "src"), "--paths", os.path.join(ROOT, "vendor"),
        "--add-data", TKDND + os.pathsep + os.path.join("tkinterdnd2", "tkdnd", "win-x64"),
        "--add-data", ICON + os.pathsep + ".",
        "--exclude-module", "numpy", "--exclude-module", "PIL", "--exclude-module", "unittest",
        "--exclude-module", "pydoc", "--exclude-module", "setuptools", "--exclude-module", "pip",
        "--distpath", RELEASE, "--workpath", WORK, "--specpath", WORK,
        os.path.join(HERE, "app.py")])
    shutil.copyfile(os.path.join(HERE, "USER_GUIDE.txt"), os.path.join(FOLDER, "User Guide.txt"))
    shutil.copyfile(os.path.join(HERE, "THIRD_PARTY.txt"), os.path.join(FOLDER, "Third-Party Licenses.txt"))


def build_zip() -> int:
    out = os.path.join(RELEASE, f"FL-Transfer-{__version__}-en-win64.zip")
    if os.path.exists(out):
        os.remove(out)
    size = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for base, _dirs, files in os.walk(FOLDER):
            for name in files:
                path = os.path.join(base, name)
                z.write(path, os.path.join(NAME, os.path.relpath(path, FOLDER)))
                size += os.path.getsize(path)
    print(f"{out}: {os.path.getsize(out) / 1e6:.1f} MB ({size / 1e6:.1f} MB unpacked)")
    return size


def build_installer(size: int) -> bool:
    makensis = find_makensis()
    if not makensis:
        print("installer skipped: makensis (NSIS 3) not found")
        return False
    out = os.path.join(RELEASE, f"FL-Transfer-Setup-{__version__}-en.exe")
    script = os.path.join(WORK, "installer.nsi")
    text = (NSI.replace("@VERSION@", __version__).replace("@OUT@", out).replace("@ICON@", ICON).replace("@WELCOME@", WELCOME)
            .replace("@FOLDER@", FOLDER).replace("@KB@", str(size // 1024)))
    with open(script, "w", encoding="utf-8-sig") as f:       # the BOM tells makensis the script is UTF-8
        f.write(text)
    subprocess.check_call([makensis, "/V2", "/INPUTCHARSET", "UTF8", script])
    print(f"{out}: {os.path.getsize(out) / 1e6:.1f} MB")
    return True


def main() -> int:
    build_program()
    size = build_zip()
    build_installer(size)
    shutil.rmtree(WORK, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
