"""Site + settings data model, persisted as JSON in %APPDATA%\\LocalDevX."""
import json
import os
import re
from dataclasses import asdict, dataclass, field, fields

APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "LocalDevX")
DATA_FILE = os.path.join(APP_DIR, "sites.json")

DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")


def fwd(p):
    """Apache-friendly path (forward slashes)."""
    return (p or "").replace("\\", "/").rstrip("/") if p else ""


@dataclass
class Settings:
    xampp_dir: str = "C:/xampp"
    vhosts_file: str = "C:/xampp/apache/conf/extra/httpd-vhosts.conf"
    sites_root: str = "C:/Sites/www"
    docroot_subdir: str = "public_html"
    tld: str = ".test"
    default_ssl: bool = True
    add_www_alias: bool = True
    create_index: bool = True
    service_name: str = "Apache2.4"
    backup_dir: str = os.path.join(APP_DIR, "backups").replace("\\", "/")
    hosts_file: str = "C:/Windows/System32/drivers/etc/hosts"
    editor: str = ""  # optional path to code editor; blank = Explorer
    cloudflared: str = ""  # optional path to cloudflared.exe; blank = auto-detect / download
    theme: str = ""  # "light", "dark" or "" = follow Windows
    close_to_tray: bool = True     # window X hides to the tray; use Quit to exit
    minimize_to_tray: bool = True  # minimize button hides to the tray
    imported: bool = False

    @property
    def apache_dir(self):
        return fwd(self.xampp_dir) + "/apache"

    @property
    def httpd_exe(self):
        return self.apache_dir + "/bin/httpd.exe"

    @property
    def httpd_conf(self):
        return self.apache_dir + "/conf/httpd.conf"

    @property
    def openssl_exe(self):
        return self.apache_dir + "/bin/openssl.exe"

    @property
    def openssl_cnf(self):
        return self.apache_dir + "/conf/openssl.cnf"


@dataclass
class Site:
    name: str = ""                       # ServerName, e.g. mysite.test
    aliases: list = field(default_factory=list)
    docroot: str = ""
    enabled: bool = True
    http: bool = True                    # serve on port 80
    ssl: bool = False                    # serve on port 443
    http_port: int = 80
    https_port: int = 443
    admin: str = ""                      # ServerAdmin
    log_dir: str = ""                    # blank = Apache's default log
    cert_file: str = ""
    key_file: str = ""
    options: str = "Indexes FollowSymLinks Includes ExecCGI"
    allow_override: str = "All"
    share_lan: bool = False              # add <name>.<lan-ip>.nip.io alias
    custom: str = ""                     # extra directives inside <VirtualHost>
    notes: str = ""

    @property
    def site_root(self):
        """Folder that holds public_html / logs / ssl (if the site follows that layout)."""
        d = fwd(self.docroot)
        parent, leaf = d.rsplit("/", 1) if "/" in d else ("", d)
        return parent if leaf.lower() in ("public_html", "public", "htdocs", "www", "web") else d

    @classmethod
    def from_dict(cls, d):
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


def default_site(settings, name):
    name = name.strip().lower()
    root = f"{fwd(settings.sites_root)}/{name}"
    sub = settings.docroot_subdir.strip("/\\")
    s = Site(
        name=name,
        aliases=[f"www.{name}"] if settings.add_www_alias else [],
        docroot=f"{root}/{sub}" if sub else root,
        ssl=settings.default_ssl,
        admin=f"admin@{name}",
        log_dir=f"{root}/logs",
    )
    if s.ssl:
        s.cert_file = f"{root}/ssl/{name}.crt"
        s.key_file = f"{root}/ssl/{name}.key"
    return s


def validate_site(site, others):
    errs = []
    if not DOMAIN_RE.match(site.name or ""):
        errs.append(f"'{site.name}' is not a valid host name.")
    if site.name in ("localhost",):
        errs.append("'localhost' is reserved (it always points at XAMPP's htdocs).")
    if not site.docroot:
        errs.append(f"{site.name}: document root is required.")
    if not (site.http or site.ssl):
        errs.append(f"{site.name}: enable HTTP, HTTPS or both.")
    for a in site.aliases:
        if not DOMAIN_RE.match(a[2:] if a.startswith("*.") else a):
            errs.append(f"{site.name}: alias '{a}' is not a valid host name.")
    taken = {}
    for o in others:
        if o is site:
            continue
        for h in [o.name, *o.aliases]:
            taken[h] = o.name
    for h in [site.name, *site.aliases]:
        if h in taken:
            errs.append(f"{h} is already used by {taken[h]}.")
    return errs


class Store:
    def __init__(self):
        self.settings = Settings()
        self.sites = []
        self.load()

    def load(self):
        if not os.path.isfile(DATA_FILE):
            return
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
        st = Settings()
        for k, v in data.get("settings", {}).items():
            if hasattr(st, k) and not isinstance(getattr(type(st), k, None), property):
                setattr(st, k, v)
        self.settings = st
        self.sites = [Site.from_dict(d) for d in data.get("sites", [])]

    def save(self):
        os.makedirs(APP_DIR, exist_ok=True)
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            from . import APP_VERSION
            json.dump({"app_version": APP_VERSION, "settings": asdict(self.settings),
                       "sites": [asdict(s) for s in self.sites]}, f, indent=2)
        os.replace(tmp, DATA_FILE)
