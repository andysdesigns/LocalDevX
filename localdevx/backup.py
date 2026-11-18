"""Backups using the naming scheme  filename-month-dd-yyyy.ext  (then -01, -02 ...)."""
import datetime
import os
import shutil


def backup_name(path, folder=None, today=None):
    today = today or datetime.date.today()
    folder = folder or os.path.dirname(path)
    base = os.path.basename(path)
    stem, ext = os.path.splitext(base)
    if not stem:  # e.g. ".htaccess"
        stem, ext = base, ""
    stamp = f"{today.strftime('%B').lower()}-{today.day:02d}-{today.year}"
    candidate = os.path.join(folder, f"{stem}-{stamp}{ext}")
    n = 1
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f"{stem}-{stamp}-{n:02d}{ext}")
        n += 1
    return candidate


def backup_file(path, folder=None):
    """Copy `path` to a dated backup. Returns the backup path, or None if `path` doesn't exist."""
    if not os.path.isfile(path):
        return None
    if folder:
        os.makedirs(folder, exist_ok=True)
    dest = backup_name(path, folder)
    shutil.copy2(path, dest)
    return dest
