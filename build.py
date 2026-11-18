r"""Build LocalDevX for release.

    build.bat            (or: python build.py)

Produces, in .\release:
    LocalDevX-<version>-setup.exe        Windows installer (Inno Setup)
    LocalDevX-<version>-portable\        unpacked app - run LocalDevX.exe straight from the folder
    LocalDevX-<version>-portable.zip     the same folder, zipped

The version comes from APP_VERSION in localdevx/__init__.py.
Needs: pip install pyinstaller, and Inno Setup 6 (winget install JRSoftware.InnoSetup).

Created by Andy Christian S. Loyola <acl20032003@gmail.com>
"""
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from localdevx import APP_NAME, APP_TITLE, APP_VERSION, buildinfo  # noqa: E402

BUILD = os.path.join(ROOT, "build")
DIST = os.path.join(ROOT, "dist")
RELEASE = os.path.join(ROOT, "release")
PORTABLE = f"{APP_NAME}-{APP_VERSION}-portable"
BS = "\\"

PORTABLE_README = rf"""{APP_TITLE} - portable version
{'=' * (len(APP_TITLE) + 19)}

No installation needed: double-click LocalDevX.exe to run it from this folder.
You can move the folder anywhere, e.g. a USB drive or C:\Tools\LocalDevX.

- Windows asks for Administrator rights: LocalDevX edits the hosts file and controls Apache.
- Settings and your site list are stored in %APPDATA%\LocalDevX and shared with the installed
  version, so you can switch between portable and installed at any time.
- Only one copy runs at a time. If "Start with Windows" is on, it starts whichever copy
  you used most recently.
- To remove the portable version, quit it (tray icon -> Quit) and delete this folder.

Created by Andy Christian S. Loyola <acl20032003@gmail.com>
"""


def step(msg):
    print(f"\n=== {msg}", flush=True)


def find_iscc():
    cands = [shutil.which("iscc"),
             os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
             r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
             r"C:\Program Files\Inno Setup 6\ISCC.exe"]
    return next((c for c in cands if c and os.path.isfile(c)), None)


def main():
    print(f"Building {APP_TITLE}")
    os.makedirs(BUILD, exist_ok=True)
    os.makedirs(RELEASE, exist_ok=True)

    step("Icon and version info")
    ico = os.path.join(BUILD, "localdevx.ico")
    ver = os.path.join(BUILD, "version_info.txt")
    buildinfo.write_icon(ico)
    with open(ver, "w", encoding="utf-8") as f:
        f.write(buildinfo.render())

    step(r"PyInstaller (unpacked app in dist\LocalDevX)")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
                    "--uac-admin", "--name", APP_NAME, "--icon", ico, "--version-file", ver,
                    "--distpath", DIST, "--workpath", os.path.join(BUILD, "pyinstaller"),
                    "--specpath", BUILD, os.path.join(ROOT, "localdevx.pyw")], check=True, cwd=ROOT)
    app_dir = os.path.join(DIST, APP_NAME)
    with open(os.path.join(app_dir, "README.txt"), "w", encoding="utf-8") as f:
        f.write(PORTABLE_README)

    step(f"Portable folder + zip ({PORTABLE})")
    portable_dir = os.path.join(RELEASE, PORTABLE)
    shutil.rmtree(portable_dir, ignore_errors=True)
    shutil.copytree(app_dir, portable_dir)
    zpath = portable_dir + ".zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for base, _, files in os.walk(portable_dir):
            for name in files:
                full = os.path.join(base, name)
                z.write(full, os.path.join(PORTABLE, os.path.relpath(full, portable_dir)))

    step("Installer (Inno Setup)")
    iscc = find_iscc()
    if not iscc:
        print("Inno Setup 6 not found - skipped the installer.\n"
              "Install it with:  winget install JRSoftware.InnoSetup   then run build.bat again.")
    else:
        subprocess.run([iscc, "/Q", f"/DAppVersion={APP_VERSION}", os.path.join(ROOT, "installer", "LocalDevX.iss")],
                       check=True, cwd=os.path.join(ROOT, "installer"))

    step("GITHUB folder")
    import sync_github
    sync_github.main()

    step("Done")
    for name in sorted(os.listdir(RELEASE)):
        p = os.path.join(RELEASE, name)
        if APP_VERSION not in name:
            continue
        if os.path.isdir(p):
            size = sum(os.path.getsize(os.path.join(b, f)) for b, _, fs in os.walk(p) for f in fs)
            name += BS
        else:
            size = os.path.getsize(p)
        print(f"  release{BS}{name}  ({size / 1048576:.1f} MB)")


if __name__ == "__main__":
    main()
