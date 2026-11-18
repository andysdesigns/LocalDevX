"""One-time migration from the app's previous name.

This is the ONLY module that knows the old identifiers. It needs them to find and clean up
what the previous version left on this PC: the data folder, the hosts-file block, comments in
httpd-vhosts.conf, the Start-with-Windows task, the firewall rule and the local certificate
authority. Everything is backed up before it's changed, and the old data folder is left alone
as a backup copy.
"""
import ctypes
import json
import os
import shutil

from .model import APP_DIR, fwd

OLD_NAME = "VirtualHostX"
OLD_APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "VirtualHostX-Win")
OLD_HOSTS_BEGIN = "# >>> VirtualHostX BEGIN (managed - do not edit inside this block)"
OLD_HOSTS_END = "# <<< VirtualHostX END"
OLD_TASK = "VirtualHostX for Windows"
OLD_FIREWALL_RULE = "VirtualHostX Apache"
OLD_CA_NAME = "VirtualHostX for Windows Local CA"
OLD_CA_CRT = "VirtualHostX-RootCA.crt"
OLD_CA_KEY = "VirtualHostX-RootCA.key"
OLD_CA_SRL = "VirtualHostX-RootCA.srl"
OLD_ICON = "vhostx-icon-1.ico"


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ---------------------------------------------------------------- step 1: data (before the app loads)

def migrate_data():
    """Copy the old data folder to the new one on the first launch after the rename.
    Returns True if something was copied. The old folder is kept untouched as a backup."""
    if os.path.isdir(APP_DIR) or not os.path.isdir(OLD_APP_DIR):
        return False
    shutil.copytree(OLD_APP_DIR, APP_DIR, ignore=shutil.ignore_patterns(OLD_ICON, "*.part"))
    data_file = os.path.join(APP_DIR, "sites.json")
    try:
        with open(data_file, encoding="utf-8") as f:
            data = json.load(f)
        old, new = fwd(OLD_APP_DIR), fwd(APP_DIR)
        for k, v in data.get("settings", {}).items():  # e.g. backup_dir pointed inside the old folder
            if isinstance(v, str) and fwd(v).lower().startswith(old.lower()):
                data["settings"][k] = new + fwd(v)[len(old):]
        with open(data_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except (OSError, ValueError):
        pass
    return True


# ---------------------------------------------------------------- step 2: system leftovers (elevated)

def _rename_comments(path, replacements, settings, log):
    """Rewrite old-name comment lines / markers in a text file (backed up first)."""
    from .backup import backup_file
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return
    text = raw.decode("utf-8-sig", errors="replace")
    if OLD_NAME not in text:
        return
    lines = text.splitlines(keepends=True)
    out = []
    for line in lines:
        if line.lstrip().startswith("#"):
            for a, b in replacements:
                line = line.replace(a, b)
        out.append(line)
    new = "".join(out)
    if new == text:
        return
    b = backup_file(path, settings.backup_dir)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(new)
    log(f"Renamed old app references in {fwd(path)}" + (f" (backup: {fwd(b)})" if b else ""))


def migrate_system(settings, log):
    """Clean up leftovers from the old name. Safe to run every launch: it does nothing once done."""
    from . import apache, hosts, sslcerts, startup
    if not is_admin():
        return
    # Start-with-Windows task: replace the old one (it points at the old launcher)
    code, _ = apache.run(["schtasks", "/Query", "/TN", OLD_TASK], timeout=15)
    if code == 0:
        apache.run(["schtasks", "/Delete", "/TN", OLD_TASK, "/F"], timeout=30)
        ok, out = startup.enable()
        log("Moved the Start-with-Windows task to the new name." if ok else "Could not re-create the startup task:\n" + out)

    # firewall rule
    code, _ = apache.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={OLD_FIREWALL_RULE}"], timeout=15)
    if code == 0:
        apache.run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={OLD_FIREWALL_RULE}"], timeout=15)
        ok, out = apache.allow_firewall(settings)
        log("Renamed the Windows Firewall rule." if ok else out)

    # hosts-file block markers and vhosts-file comments (comments only - nothing Apache reads changes)
    _rename_comments(settings.hosts_file, [(OLD_HOSTS_BEGIN, hosts.BEGIN), (OLD_HOSTS_END, hosts.END)], settings, log)
    _rename_comments(settings.vhosts_file, [("VirtualHostX for Windows", "LocalDevX"), (OLD_NAME, "LocalDevX")],
                     settings, log)

    # local certificate authority
    if os.path.isfile(os.path.join(sslcerts.CA_DIR, OLD_CA_CRT)):
        migrate_ca(settings, log)


def migrate_ca(settings, log):
    """Replace the old-name CA with a LocalDevX CA and re-issue every site certificate it signed.

    Order matters so the sites never lose their padlock: new CA -> trust it -> re-issue certs ->
    remove the old CA from Windows -> restart Apache -> move the old CA files to the backup folder."""
    from . import apache, sslcerts
    from .backup import backup_file
    from .model import Store
    store = Store()
    old_crt = os.path.join(sslcerts.CA_DIR, OLD_CA_CRT)
    old_key = os.path.join(sslcerts.CA_DIR, OLD_CA_KEY)
    code, out = apache.run(["certutil", "-store", "Root", OLD_CA_NAME], timeout=20)
    was_trusted = code == 0 and OLD_CA_NAME in out

    # which site certificates did the old CA sign? (imported / self-made certs are left alone)
    todo = []
    for s in store.sites:
        if s.ssl and s.cert_file and s.key_file and os.path.isfile(s.cert_file):
            code, issuer = apache.run([settings.openssl_exe, "x509", "-noout", "-issuer", "-in", s.cert_file], timeout=20)
            if OLD_CA_NAME in issuer:
                todo.append(s)

    if todo or was_trusted:
        log("Replacing the old local certificate authority with a LocalDevX one…")
        ok, out = sslcerts.ensure_ca(settings)
        if not ok:
            log("Could not create the LocalDevX certificate authority (will retry next launch):\n" + out)
            return
        if was_trusted:
            ok, out = sslcerts.trust_ca()
            if not ok:
                log("Could not trust the new certificate authority (will retry next launch):\n" + out)
                return
        failed = []
        for s in todo:
            for p in (s.cert_file, s.key_file):
                backup_file(p, settings.backup_dir)
            extra = [apache.lan_alias(s)] if s.share_lan and apache.lan_ip() else []
            ok, out = sslcerts.issue_cert(settings, s, extra)
            if not ok:
                failed.append(s.name)
        if failed:
            log("Could not re-issue certificates for: " + ", ".join(failed) + " (will retry next launch).")
            return
        if todo:
            log(f"Re-issued {len(todo)} site certificate(s) with the LocalDevX certificate authority.")
        if was_trusted:
            apache.run(["certutil", "-delstore", "Root", OLD_CA_NAME], timeout=30)
        if todo and apache.apache_running(settings):
            ok, msg = apache.restart_apache(settings)  # load the re-issued certificates
            log(msg)

    # nothing uses the old CA any more: move its files to the backup folder
    for p in (old_crt, old_key, os.path.join(sslcerts.CA_DIR, OLD_CA_SRL)):
        if backup_file(p, settings.backup_dir):
            os.remove(p)
    log("Old certificate authority files moved to the backup folder." if not todo else
        "Certificate authority migration complete. Restart your browser if a site shows a certificate warning.")
