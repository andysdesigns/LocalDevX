"""Read / write httpd-vhosts.conf and control the XAMPP Apache server."""
import os
import re
import socket
import subprocess
import tempfile
import time

from . import APP_TITLE
from .model import Site, fwd

NO_WINDOW = 0x08000000
DETACHED = 0x00000008

HEADER = """# ======================================================================
#  Managed by {title}.
#  This file is regenerated every time you click "Apply Changes".
#  Put extra directives in a site's "Custom directives" box instead of
#  editing here. Backups of previous versions are kept by the app.
# ======================================================================
""".format(title=APP_TITLE)


def run(cmd, timeout=60, **kw):
    """Run a command hidden, return (code, combined output)."""
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       creationflags=NO_WINDOW, errors="replace", **kw)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


# ---------------------------------------------------------------- parsing

def _unquote(s):
    s = s.strip()
    return s[1:-1] if len(s) >= 2 and s[0] == s[-1] == '"' else s


def _split_args(s):
    return [_unquote(x) for x in re.findall(r'"[^"]*"|\S+', s)]


def parse_vhosts(text):
    """Parse a vhosts file into a list of Site objects (port 80/443 blocks merged by ServerName)."""
    sites = {}
    order = []
    for m in re.finditer(r"^[ \t]*<VirtualHost\s+([^>]+)>(.*?)^[ \t]*</VirtualHost>",
                         text, re.S | re.M | re.I):
        addr, body = m.group(1), m.group(2)
        port_m = re.search(r":(\d+)", addr)
        port = int(port_m.group(1)) if port_m else 80
        b = _parse_block(body)
        if not b["name"] or b["name"] == "localhost":
            continue
        is_ssl = b["ssl_on"] or port == 443
        s = sites.get(b["name"])
        if s is None:
            s = Site(name=b["name"], http=False, ssl=False, options="", allow_override="")
            sites[b["name"]] = s
            order.append(b["name"])
        for a in b["aliases"]:
            if a not in s.aliases and a != s.name:
                s.aliases.append(a)
        s.docroot = s.docroot or b["docroot"]
        s.admin = s.admin or b["admin"]
        s.log_dir = s.log_dir or b["log_dir"]
        s.options = s.options or b["options"]
        s.allow_override = s.allow_override or b["allow_override"]
        if b["custom"] and b["custom"] not in s.custom:
            s.custom = (s.custom + "\n" + b["custom"]).strip()
        if is_ssl:
            s.ssl, s.https_port = True, port
            s.cert_file = s.cert_file or b["cert"]
            s.key_file = s.key_file or b["key"]
        else:
            s.http, s.http_port = True, port
    for s in sites.values():
        s.options = s.options or "Indexes FollowSymLinks Includes ExecCGI"
        s.allow_override = s.allow_override or "All"
    return [sites[n] for n in order]


def _parse_block(body):
    out = dict(name="", aliases=[], docroot="", admin="", log_dir="", options="",
               allow_override="", ssl_on=False, cert="", key="", custom="")
    custom = []
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        i += 1
        if not line or line.startswith("#"):
            continue
        sec = re.match(r"<(\w+)\s*([^>]*)>", line)
        if sec:
            tag = sec.group(1)
            inner, depth = [], 1
            while i < len(lines) and depth:
                l2 = lines[i].strip()
                i += 1
                if re.match(rf"<{tag}[\s>]", l2, re.I):
                    depth += 1
                elif re.match(rf"</{tag}\s*>", l2, re.I):
                    depth -= 1
                    if not depth:
                        break
                inner.append(l2)
            path = _unquote(sec.group(2))
            if tag.lower() == "directory" and out["docroot"] and fwd(path).lower() == fwd(out["docroot"]).lower():
                leftovers = []
                for l2 in inner:
                    k, _, v = l2.partition(" ")
                    kl = k.lower()
                    if kl == "options":
                        out["options"] = v.strip()
                    elif kl == "allowoverride":
                        out["allow_override"] = v.strip()
                    elif kl in ("require", "order", "allow") or not l2 or l2.startswith("#"):
                        continue
                    else:
                        leftovers.append(l2)
                if leftovers:
                    custom.append(f'<Directory "{path}">')
                    custom += ["\t" + x for x in leftovers]
                    custom.append("</Directory>")
            else:
                custom.append(f"<{tag} {sec.group(2)}>".replace(" >", ">"))
                custom += ["\t" + x for x in inner]
                custom.append(f"</{tag}>")
            continue
        key, _, rest = line.partition(" ")
        k = key.lower()
        args = _split_args(rest)
        if k == "servername" and args:
            out["name"] = args[0].split(":")[0].lower()
        elif k == "serveralias":
            out["aliases"] += [a.lower() for a in args]
        elif k == "documentroot" and args:
            out["docroot"] = fwd(args[0])
        elif k == "serveradmin" and args:
            out["admin"] = args[0]
        elif k == "errorlog" and args:
            out["log_dir"] = fwd(os.path.dirname(fwd(args[0])))
        elif k == "customlog":
            continue
        elif k == "sslengine":
            out["ssl_on"] = bool(args) and args[0].lower() == "on"
        elif k == "sslcertificatefile" and args:
            out["cert"] = fwd(args[0])
        elif k == "sslcertificatekeyfile" and args:
            out["key"] = fwd(args[0])
        else:
            custom.append(line)
    out["custom"] = "\n".join(custom)
    return out


# ---------------------------------------------------------------- generating

def lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent
            return s.getsockname()[0]
    except OSError:
        return ""


def lan_alias(site, ip=None):
    ip = ip or lan_ip()
    return f"{site.name}.{ip}.nip.io" if ip else ""


def existing_listens(settings):
    ports = set()
    for f in (settings.httpd_conf, settings.apache_dir + "/conf/extra/httpd-ssl.conf"):
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = re.match(r"\s*Listen\s+(?:[\d.\[\]:a-f]*:)?(\d+)", line, re.I)
                    if m:
                        ports.add(int(m.group(1)))
        except OSError:
            pass
    return ports


def _block(site, port, ssl, ip):
    t = "\t"
    names = [a for a in site.aliases if a]
    if site.share_lan and ip:
        names.append(lan_alias(site, ip))
    L = [f"<VirtualHost *:{port}>"]
    if site.admin:
        L.append(f"{t}ServerAdmin {site.admin}")
    L.append(f'{t}DocumentRoot "{fwd(site.docroot)}"')
    L.append(f"{t}ServerName {site.name}")
    if names:
        L.append(f"{t}ServerAlias {' '.join(names)}")
    if site.log_dir:
        ld = fwd(site.log_dir)
        L += ["", f'{t}ErrorLog "{ld}/error_log.log"', f'{t}CustomLog "{ld}/access_log.log" common']
    L += ["", f'{t}<Directory "{fwd(site.docroot)}">']
    if site.options.strip():
        L.append(f"{t}{t}Options {site.options.strip()}")
    L += [f"{t}{t}AllowOverride {site.allow_override.strip() or 'None'}",
          f"{t}{t}Require all granted", f"{t}</Directory>"]
    if ssl:
        L += ["", f"{t}SSLEngine On",
              f'{t}SSLCertificateFile "{fwd(site.cert_file)}"',
              f'{t}SSLCertificateKeyFile "{fwd(site.key_file)}"']
    if site.custom.strip():
        L += ["", f"{t}# --- custom directives ---"]
        L += [t + x.rstrip() for x in site.custom.strip().splitlines()]
    L.append("</VirtualHost>")
    return "\n".join(L)


def generate_vhosts(settings, sites):
    ip = lan_ip() if any(s.share_lan for s in sites) else ""
    enabled = [s for s in sites if s.enabled]
    out = [HEADER]
    have = existing_listens(settings)
    extra = sorted({p for s in enabled for p, on in ((s.http_port, s.http), (s.https_port, s.ssl)) if on} - have)
    for p in extra:
        out.append(f"Listen {p}")
    if extra:
        out.append("")
    out.append("# ===== HTTPS =====\n")
    for s in enabled:
        if s.ssl:
            out += [_block(s, s.https_port, True, ip), ""]
    out.append("# ===== HTTP =====\n")
    out.append(f'<VirtualHost *:80>\n\tServerName localhost\n\tDocumentRoot "{fwd(settings.xampp_dir)}/htdocs"\n</VirtualHost>\n')
    for s in enabled:
        if s.http:
            out += [_block(s, s.http_port, False, ip), ""]
    disabled = [s.name for s in sites if not s.enabled]
    if disabled:
        out.append("# Disabled sites (kept in LocalDevX, not served): " + ", ".join(disabled))
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------- testing

def test_config(settings, candidate_text):
    """Validate a candidate vhosts file WITHOUT touching the live config.

    Copies httpd.conf to a temp file with the vhosts Include pointed at the candidate,
    then runs `httpd -t` against it. Returns (ok, output)."""
    with open(settings.httpd_conf, encoding="utf-8", errors="replace") as f:
        conf = f.read()
    target = os.path.normcase(os.path.normpath(settings.vhosts_file))
    tmpdir = tempfile.mkdtemp(prefix="localdevx-")
    cand = os.path.join(tmpdir, "candidate-vhosts.conf")
    with open(cand, "w", encoding="utf-8") as f:
        f.write(candidate_text)
    replaced = False

    def repl(m):
        nonlocal replaced
        inc = _unquote(m.group(2))
        full = inc if os.path.isabs(inc) else os.path.join(settings.apache_dir, inc)
        if os.path.normcase(os.path.normpath(full)) == target:
            replaced = True
            return f'{m.group(1)}"{fwd(cand)}"'
        return m.group(0)

    conf2 = re.sub(r'^(\s*Include(?:Optional)?\s+)("[^"]+"|\S+)', repl, conf, flags=re.M | re.I)
    if not replaced:
        return False, (f"httpd.conf has no active Include for {settings.vhosts_file}.\n"
                       "Add this line to httpd.conf:\n  Include conf/extra/httpd-vhosts.conf")
    tconf = os.path.join(tmpdir, "httpd-test.conf")
    with open(tconf, "w", encoding="utf-8") as f:
        f.write(conf2)
    try:
        code, out = run([settings.httpd_exe, "-t", "-f", tconf], timeout=60)
    finally:
        for p in (cand, tconf):
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass
    return code == 0, out.strip()


# ---------------------------------------------------------------- server control

def service_state(name):
    code, out = run(["sc", "query", name], timeout=15)
    if code != 0:
        return None  # not installed as a service
    m = re.search(r"STATE\s*:\s*\d+\s+(\w+)", out)
    return m.group(1) if m else "UNKNOWN"


def apache_running(settings):
    st = service_state(settings.service_name)
    if st is not None:
        return st == "RUNNING"
    code, out = run(["tasklist", "/FI", "IMAGENAME eq httpd.exe", "/NH"], timeout=15)
    return "httpd.exe" in out


def _wait_service(name, want, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if service_state(name) == want:
            return True
        time.sleep(0.5)
    return False


def stop_apache(settings):
    st = service_state(settings.service_name)
    if st is not None:
        if st != "STOPPED":
            code, out = run(["sc", "stop", settings.service_name])
            if not _wait_service(settings.service_name, "STOPPED"):
                return False, "Service did not stop:\n" + out
        return True, "Apache service stopped."
    run(["taskkill", "/F", "/IM", "httpd.exe"])
    return True, "Apache stopped."


def start_apache(settings):
    st = service_state(settings.service_name)
    if st is not None:
        code, out = run(["sc", "start", settings.service_name])
        if _wait_service(settings.service_name, "RUNNING", 30):
            return True, "Apache service started."
        return False, "Service failed to start.\n" + out + "\n" + tail_error_log(settings)
    subprocess.Popen([settings.httpd_exe], cwd=fwd(settings.xampp_dir),
                     creationflags=NO_WINDOW | DETACHED, close_fds=True)
    time.sleep(2)
    if apache_running(settings):
        return True, "Apache started."
    return False, "Apache failed to start.\n" + tail_error_log(settings)


def restart_apache(settings):
    ok, msg = stop_apache(settings)
    if not ok:
        return ok, msg
    return start_apache(settings)


def tail_error_log(settings, n=15):
    path = settings.apache_dir + "/logs/error.log"
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return ""


FIREWALL_RULE = "LocalDevX Apache"


def allow_firewall(settings):
    """(Re)create the inbound Windows Firewall rule for Apache on private/domain networks."""
    exe = settings.httpd_exe.replace("/", "\\")
    run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FIREWALL_RULE}"])
    code, out = run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={FIREWALL_RULE}",
                     "dir=in", "action=allow", f"program={exe}", "profile=private,domain", "enable=yes"])
    return code == 0, ("Firewall rule added: Apache is reachable from private networks." if code == 0 else out)


def remove_firewall():
    code, out = run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FIREWALL_RULE}"])
    return code == 0, out
