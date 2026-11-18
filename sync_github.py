r"""Mirror this project into the GITHUB folder (the copy that gets uploaded to GitHub).

    python sync_github.py        (build.bat also runs this at the end of every build)

What it does:
- Copies the source code, launchers, build scripts and installer script into GITHUB\, and removes
  files there that no longer exist here. Backups (filename-month-dd-yyyy.ext) and caches are skipped.
- Copies the newest installer and portable zip from release\ into GITHUB\downloads\, replacing
  older versions.
- Updates the version number in GITHUB\README.md (download links and badge).

GITHUB\README.md (the GitHub pitch), GITHUB\docs\ (logo and screenshots), GITHUB\LICENSE,
GITHUB\.gitignore, GITHUB\.gitattributes and the GITHUB\.git repository
belong to the GITHUB folder only and are never overwritten.

Created by Andy Christian S. Loyola <acl20032003@gmail.com>
"""
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
GH = os.path.join(ROOT, "GITHUB")
sys.path.insert(0, ROOT)
from localdevx import APP_VERSION  # noqa: E402

BACKUP = re.compile(r"-(january|february|march|april|may|june|july|august|september|october|"
                    r"november|december)-\d{2}-\d{4}", re.I)
TOP_FILES = ["localdevx.pyw", "LocalDevX.bat", "build.py", "build.bat", "sync_github.py"]
MIRROR_DIRS = {"localdevx": (".py",), "installer": (".iss",)}
OWNED = {"README.md", ".gitignore", ".gitattributes", "LICENSE"}  # GITHUB-only files at the top level


def wanted(name, exts):
    return name.endswith(exts) and not BACKUP.search(name)


def same(a, b):
    if not os.path.isfile(b) or os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        return fa.read() == fb.read()


def put(src, dst, changes):
    if same(src, dst):
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    changes.append("updated " + os.path.relpath(dst, GH))


def drop(path, changes):
    os.remove(path)
    changes.append("removed " + os.path.relpath(path, GH))


def main():
    os.makedirs(GH, exist_ok=True)
    changes = []

    # top-level files
    for name in TOP_FILES:
        put(os.path.join(ROOT, name), os.path.join(GH, name), changes)
    for name in os.listdir(GH):
        p = os.path.join(GH, name)
        if os.path.isfile(p) and name not in TOP_FILES and name not in OWNED:
            drop(p, changes)

    # mirrored folders
    for folder, exts in MIRROR_DIRS.items():
        src_dir, dst_dir = os.path.join(ROOT, folder), os.path.join(GH, folder)
        keep = {n for n in os.listdir(src_dir) if wanted(n, exts)}
        for n in sorted(keep):
            put(os.path.join(src_dir, n), os.path.join(dst_dir, n), changes)
        if os.path.isdir(dst_dir):
            for n in os.listdir(dst_dir):
                p = os.path.join(dst_dir, n)
                if os.path.isdir(p):
                    shutil.rmtree(p)
                    changes.append("removed " + os.path.relpath(p, GH))
                elif n not in keep:
                    drop(p, changes)

    # downloads: the current version's installer + portable zip
    names = [f"LocalDevX-{APP_VERSION}-setup.exe", f"LocalDevX-{APP_VERSION}-portable.zip"]
    built = [n for n in names if os.path.isfile(os.path.join(ROOT, "release", n))]
    dl = os.path.join(GH, "downloads")
    for n in built:
        put(os.path.join(ROOT, "release", n), os.path.join(dl, n), changes)
    if len(built) == len(names) and os.path.isdir(dl):  # only prune once the new version is in place
        for n in os.listdir(dl):
            if n not in names:
                drop(os.path.join(dl, n), changes)
    missing = [n for n in names if not os.path.isfile(os.path.join(dl, n))]

    # version number in the GitHub README
    readme = os.path.join(GH, "README.md")
    if os.path.isfile(readme):
        with open(readme, encoding="utf-8") as f:
            text = f.read()
        new = re.sub(r"LocalDevX-\d+\.\d+\.\d+-", f"LocalDevX-{APP_VERSION}-", text)
        new = re.sub(r"badge/version-\d+\.\d+\.\d+-", f"badge/version-{APP_VERSION}-", new)
        new = re.sub(r"installer \(v\d+\.\d+\.\d+\)", f"installer (v{APP_VERSION})", new)
        if new != text:
            with open(readme, "w", encoding="utf-8", newline="\n") as f:
                f.write(new)
            changes.append("updated README.md version links")

    print("GITHUB folder: " + ("up to date." if not changes else f"{len(changes)} change(s)"))
    for c in changes:
        print("  " + c)
    if missing:
        print("  note: not built yet - run build.bat to add: " + ", ".join(missing))


if __name__ == "__main__":
    main()
