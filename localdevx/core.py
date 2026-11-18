"""The "Apply Changes" pipeline and first-run import."""
import os

from . import APP_TITLE, apache, hosts, sslcerts
from .backup import backup_file
from .model import fwd, validate_site

INDEX_PHP = """<!doctype html>
<html><head><meta charset="utf-8"><title>{name}</title>
<style>body{{font:16px system-ui,sans-serif;display:grid;place-items:center;height:100vh;margin:0;background:#f4f6fb;color:#223}}
.c{{text-align:center}}code{{background:#e6e9f2;padding:2px 6px;border-radius:4px}}</style></head>
<body><div class="c"><h1>{name}</h1><p>Your new virtual host is working.</p>
<p>Document root: <code>{root}</code></p><p>PHP <?php echo PHP_VERSION; ?></p>
<p style="color:#889;font-size:13px">Created with {app}</p></div></body></html>
"""


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def import_existing(store):
    """Parse the current vhosts file into sites (does not write anything)."""
    return apache.parse_vhosts(read_text(store.settings.vhosts_file))


def apply_changes(store, log, removed=(), restart=True):
    """Write vhosts + hosts, create folders/certs, restart Apache. `log` is a callable(str)."""
    st = store.settings
    sites = store.sites

    errs = []
    for s in sites:
        errs += validate_site(s, sites)
    if errs:
        log("Fix these problems first:\n  " + "\n  ".join(errs))
        return False

    # 1. folders + starter page
    for s in (x for x in sites if x.enabled):
        for d in filter(None, [s.docroot, s.log_dir]):
            if not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
                log(f"Created folder {fwd(d)}")
        if st.create_index and not os.listdir(s.docroot):
            with open(os.path.join(s.docroot, "index.php"), "w", encoding="utf-8") as f:
                f.write(INDEX_PHP.format(name=s.name, root=fwd(s.docroot), app=APP_TITLE))
            log(f"Added starter index.php to {s.name}")

    # 2. certificates
    for s in (x for x in sites if x.enabled and x.ssl):
        if not s.cert_file or not s.key_file:
            s.cert_file = f"{s.site_root}/ssl/{s.name}.crt"
            s.key_file = f"{s.site_root}/ssl/{s.name}.key"
        if not (os.path.isfile(s.cert_file) and os.path.isfile(s.key_file)):
            ok, out = sslcerts.issue_cert(st, s)
            log(("Issued" if ok else "FAILED to issue") + f" SSL certificate for {s.name}" + ("" if ok else ":\n" + out))
            if not ok:
                return False

    # 3. Apache config
    new_conf = apache.generate_vhosts(st, sites)
    old_conf = read_text(st.vhosts_file)
    conf_changed = new_conf != old_conf
    if conf_changed:
        log("Testing new Apache configuration...")
        ok, out = apache.test_config(st, new_conf)
        if not ok:
            log("Apache rejected the configuration - nothing was changed:\n" + out)
            return False
        b = backup_file(st.vhosts_file, st.backup_dir)
        if b:
            log(f"Backed up vhosts file to {fwd(b)}")
        with open(st.vhosts_file, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_conf)
        log(f"Wrote {fwd(st.vhosts_file)}")
    else:
        log("Apache configuration unchanged.")

    # 4. hosts file
    try:
        old_hosts = hosts.read_hosts(st.hosts_file)
        new_hosts = hosts.build_hosts(old_hosts, sites, unmanage=removed)
        if new_hosts != old_hosts:
            b = backup_file(st.hosts_file, st.backup_dir)
            if b:
                log(f"Backed up hosts file to {fwd(b)}")
            hosts.write_hosts(st.hosts_file, new_hosts)
            apache.run(["ipconfig", "/flushdns"])
            log("Updated hosts file and flushed DNS cache.")
        else:
            log("Hosts file unchanged.")
    except PermissionError:
        log("Could not write the hosts file - run LocalDevX as Administrator.")
        return False

    store.save()

    # 5. restart
    if restart and (conf_changed or not apache.apache_running(st)):
        log("Restarting Apache...")
        ok, msg = apache.restart_apache(st)
        log(msg)
        if not ok:
            return False
    log("Done.")
    return True
