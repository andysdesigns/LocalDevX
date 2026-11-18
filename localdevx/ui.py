"""Tkinter user interface."""
import copy
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import APP_AUTHOR, APP_AUTHOR_EMAIL, APP_NAME, APP_TITLE, APP_VERSION, apache, core, legacy, sslcerts, startup, theme, tray, tunnel
from .backup import backup_file
from .model import Store, default_site, fwd, validate_site


class App(tk.Tk):
    def __init__(self, start_hidden=False):
        super().__init__()
        if start_hidden:
            self.withdraw()  # started from Windows sign-in: go straight to the tray
        self.title(APP_TITLE)
        try:
            self.iconbitmap(default=tray.ensure_icon())
        except (tk.TclError, OSError):
            pass
        self.geometry("1180x800")
        self.minsize(960, 640)
        self.store = Store()
        self.current = None
        self.removed = set()
        self.dirty = False
        self.busy = False
        self._loading = False
        self.logq = queue.Queue()
        self.tunnels = {}  # id(site) -> tunnel.Tunnel
        self.texts = []    # (tk.Text, kind) - classic widgets recolored on theme change
        self.toplevels = []
        self.apache_up = None

        self.theme_name = theme.resolve(self.store.settings.theme)
        self.c = theme.apply_style(self, self.theme_name)
        self._build_toolbar()
        self._build_log()   # packed at the bottom first so it's never squeezed out
        self._build_body()
        self.apply_theme(self.theme_name, save=False)
        self.refresh_list()
        self.after(150, self._poll_log)
        self.after(300, self.update_status)
        self.after(400, self._first_run)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # system tray
        self.trayq = queue.Queue()
        self._told_tray = False
        self.tray = None
        try:
            self.tray = tray.Tray(APP_TITLE, self.trayq.put, self._tray_menu)
        except Exception:
            pass
        if not self.tray_ok and start_hidden:
            self.deiconify()
            self.iconify()  # no tray available: start minimized on the taskbar instead
        self.bind("<Unmap>", self._on_unmap)
        self.log(f"{APP_TITLE} started.")
        threading.Thread(target=self._housekeeping, daemon=True).start()

    def _housekeeping(self):
        """Background start-up chores: tidy up after the rename, keep the startup task pointing here."""
        try:
            legacy.migrate_system(self.store.settings, self.log)
            if legacy.is_admin():
                startup.repair(self.log)
        except Exception as e:
            self.log(f"Start-up maintenance failed: {e!r}")
        self.after(0, self._update_header)

    @property
    def tray_ok(self):
        t = getattr(self, "tray", None)
        return bool(t and t.ok)

    # ------------------------------------------------------------ layout
    def apply_theme(self, name, save=True):
        """Switch between 'light' and 'dark' (ttk styles + the classic tk widgets they don't reach)."""
        self.theme_name = name
        self.c = c = theme.apply_style(self, name)
        dark = name == "dark"
        theme.style_toplevel(self, c, dark)
        self.status_canvas.config(bg=c["bg"])
        self._paint_status()
        self.tree.tag_configure("off", foreground=c["muted"])
        self.texts = [(w, k) for w, k in self.texts if w.winfo_exists()]
        for w, kind in self.texts:
            theme.style_text(w, c, kind)
        self.toplevels = [w for w in self.toplevels if w.winfo_exists()]
        for w in self.toplevels:
            theme.style_toplevel(w, c, dark)
        self.theme_btn.config(text="☀  Light" if dark else "☾  Dark")
        if save:
            self.store.settings.theme = name
            self.store.save()

    def toggle_theme(self):
        self.apply_theme("light" if self.theme_name == "dark" else "dark")

    def _text(self, parent, kind="field", **kw):
        w = tk.Text(parent, **kw)
        theme.style_text(w, self.c, kind)
        self.texts.append((w, kind))
        return w

    def _build_toolbar(self):
        bar = ttk.Frame(self, padding=(20, 16, 20, 14))
        bar.pack(fill="x")
        ttk.Button(bar, text="＋  New Site", command=self.add_site).pack(side="left")
        ttk.Button(bar, text="Duplicate", command=self.duplicate_site).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Remove", command=self.remove_site).pack(side="left", padx=(8, 0))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=16)
        self.apply_btn = ttk.Button(bar, text="✔  Apply Changes", style="Accent.TButton", command=self.apply)
        self.apply_btn.pack(side="left")
        self.dirty_lbl = ttk.Label(bar, text="", style="Bad.TLabel")
        self.dirty_lbl.pack(side="left", padx=12)

        ttk.Button(bar, text="⏻  Quit", command=self.quit_app).pack(side="right")
        ttk.Separator(bar, orient="vertical").pack(side="right", fill="y", padx=16)
        self.theme_btn = ttk.Button(bar, text="☾  Dark", command=self.toggle_theme)
        self.theme_btn.pack(side="right")
        ttk.Button(bar, text="⚙  Settings", command=self.open_settings).pack(side="right", padx=(0, 8))
        ttk.Button(bar, text="View Config", command=self.preview_config).pack(side="right", padx=(0, 8))

    def _build_body(self):
        self.paned = ttk.PanedWindow(self, orient="horizontal")
        self.paned.pack(fill="both", expand=True, padx=20, pady=(0, 4))

        # sidebar
        side = ttk.Frame(self.paned, style="Side.TFrame", padding=(14, 14, 14, 12))
        ttk.Label(side, text="VIRTUAL HOSTS", style="Side.TLabel").pack(anchor="w")
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *_: self.refresh_list())
        ttk.Entry(side, textvariable=self.search).pack(fill="x", pady=(8, 12))
        self.tree = ttk.Treeview(side, columns=("st",), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Site")
        self.tree.heading("st", text="")
        self.tree.column("#0", width=220)
        self.tree.column("st", width=64, anchor="e")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", lambda e: self.open_browser())
        foot = ttk.Frame(side, style="Side.TFrame")
        foot.pack(fill="x", pady=(10, 0))
        self.count_lbl = ttk.Label(foot, text="", style="Side.TLabel")
        self.count_lbl.pack(side="left")
        ttk.Label(foot, text=f"v{APP_VERSION}", style="Side.TLabel").pack(side="right")
        self.paned.add(side, weight=1)

        # detail
        detail = ttk.Frame(self.paned, padding=(24, 6, 4, 4))
        self.paned.add(detail, weight=3)
        head = ttk.Frame(detail)
        head.pack(fill="x")
        self.title_lbl = ttk.Label(head, text="No site selected", style="Title.TLabel")
        self.title_lbl.pack(side="left")
        acts = ttk.Frame(head)
        acts.pack(side="right")
        for txt, cmd in (("Open in Browser", self.open_browser), ("Open Folder", self.open_folder),
                         ("Open in Editor", self.open_editor), ("Error Log", self.open_errorlog)):
            ttk.Button(acts, text=txt, command=cmd).pack(side="left", padx=(6, 0))
        self.url_lbl = ttk.Label(detail, text="", style="Muted.TLabel", cursor="hand2")
        self.url_lbl.pack(anchor="w", pady=(4, 16))
        self.url_lbl.bind("<Button-1>", lambda e: self.open_browser())

        self.nb = ttk.Notebook(detail)
        self.nb.pack(fill="both", expand=True)
        self.v = {}
        self._tab_general()
        self._tab_ssl()
        self._tab_advanced()
        self._tab_share()
        self._tab_notes()
        self._set_form_state(False)

    def _var(self, key, kind=tk.StringVar):
        var = kind(self)
        var.trace_add("write", lambda *_: self.on_field(key))
        self.v[key] = var
        return var

    def _row(self, parent, r, label, widget, browse=None, hint=None):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=7, padx=(0, 18))
        widget.grid(row=r, column=1, sticky="ew", pady=7)
        if browse:
            ttk.Button(parent, text="Browse…", command=browse).grid(row=r, column=2, padx=(8, 0))
        if hint:
            ttk.Label(parent, text=hint, style="Muted.TLabel").grid(row=r + 1, column=1, sticky="w", pady=(0, 6))

    def _tab_general(self):
        f = ttk.Frame(self.nb, padding=(24, 22))
        f.columnconfigure(1, weight=1)
        self.nb.add(f, text="General")
        ttk.Checkbutton(f, text="Site enabled", variable=self._var("enabled", tk.BooleanVar)).grid(row=0, column=1, sticky="w", pady=(0, 6))
        self._row(f, 1, "Server name", ttk.Entry(f, textvariable=self._var("name")))
        self._row(f, 2, "Aliases", ttk.Entry(f, textvariable=self._var("aliases")),
                  hint="Space or comma separated, e.g. www.mysite.test  *.mysite.test")
        self._row(f, 4, "Document root", ttk.Entry(f, textvariable=self._var("docroot")),
                  browse=lambda: self._browse_dir("docroot"))
        ports = ttk.Frame(f)
        ttk.Checkbutton(ports, text="HTTP  port", variable=self._var("http", tk.BooleanVar)).pack(side="left")
        ttk.Spinbox(ports, from_=1, to=65535, width=7, textvariable=self._var("http_port")).pack(side="left", padx=(8, 32))
        ttk.Checkbutton(ports, text="HTTPS port", variable=self._var("ssl", tk.BooleanVar)).pack(side="left")
        ttk.Spinbox(ports, from_=1, to=65535, width=7, textvariable=self._var("https_port")).pack(side="left", padx=8)
        self._row(f, 5, "Serve over", ports)
        self._row(f, 6, "Admin e-mail", ttk.Entry(f, textvariable=self._var("admin")))
        self._row(f, 7, "Log folder", ttk.Entry(f, textvariable=self._var("log_dir")),
                  browse=lambda: self._browse_dir("log_dir"),
                  hint="error_log.log and access_log.log are written here. Blank = Apache's main log.")

    def _tab_ssl(self):
        f = ttk.Frame(self.nb, padding=(24, 22))
        f.columnconfigure(1, weight=1)
        self.nb.add(f, text="SSL")
        self._row(f, 0, "Certificate", ttk.Entry(f, textvariable=self._var("cert_file")),
                  browse=lambda: self._browse_file("cert_file"))
        self._row(f, 1, "Private key", ttk.Entry(f, textvariable=self._var("key_file")),
                  browse=lambda: self._browse_file("key_file"),
                  hint="Missing files are generated automatically on Apply, signed by the local LocalDevX CA.")
        btns = ttk.Frame(f)
        btns.grid(row=3, column=1, sticky="w", pady=12)
        ttk.Button(btns, text="Regenerate certificate", command=self.regen_cert).pack(side="left")
        ttk.Button(btns, text="Show certificate info", command=self.show_cert).pack(side="left", padx=8)
        ttk.Separator(f).grid(row=4, column=0, columnspan=3, sticky="ew", pady=20)
        ttk.Label(f, text="Local certificate authority", style="Section.TLabel").grid(row=5, column=0, columnspan=2, sticky="w")
        self.ca_lbl = ttk.Label(f, text="", style="Muted.TLabel", wraplength=600, justify="left")
        self.ca_lbl.grid(row=6, column=0, columnspan=3, sticky="w", pady=(6, 12))
        cab = ttk.Frame(f)
        cab.grid(row=7, column=0, columnspan=3, sticky="w")
        ttk.Button(cab, text="Trust CA in Windows", command=self.trust_ca).pack(side="left")
        ttk.Button(cab, text="Remove trust", command=self.untrust_ca).pack(side="left", padx=8)
        ttk.Button(cab, text="Open CA folder", command=lambda: (sslcerts.ensure_ca(self.store.settings),
                                                                os.startfile(sslcerts.CA_DIR))).pack(side="left")

    def _tab_advanced(self):
        f = ttk.Frame(self.nb, padding=(24, 22))
        f.columnconfigure(1, weight=1)
        f.rowconfigure(3, weight=1)
        self.nb.add(f, text="Advanced")
        self._row(f, 0, "Options", ttk.Entry(f, textvariable=self._var("options")))
        self._row(f, 1, "AllowOverride", ttk.Combobox(f, textvariable=self._var("allow_override"),
                                                       values=("All", "None", "FileInfo", "AuthConfig Limit")))
        ttk.Label(f, text="Custom directives (placed inside each <VirtualHost> block):").grid(row=2, column=0, columnspan=3, sticky="w", pady=(16, 8))
        self.custom_txt = self._text(f, height=10, font=("Consolas", 10), undo=True)
        self.custom_txt.grid(row=3, column=0, columnspan=3, sticky="nsew")
        self.custom_txt.bind("<<Modified>>", self._custom_modified)
        ttk.Label(f, text="Examples:  ProxyPass /api http://127.0.0.1:3000/   ·   php_value upload_max_filesize 64M   ·   "
                          "Header set X-Env local", style="Muted.TLabel").grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))

    def _tab_share(self):
        f = ttk.Frame(self.nb, padding=(24, 22))
        f.columnconfigure(0, weight=1)
        self.nb.add(f, text="Share")
        ttk.Label(f, text="Share this site with phones, tablets and other computers on your network",
                  style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(f, text="Enable LAN sharing (adds a *.nip.io alias that resolves to this PC)",
                        variable=self._var("share_lan", tk.BooleanVar)).grid(row=1, column=0, sticky="w", pady=(10, 8))
        self.share_url = tk.StringVar()
        e = ttk.Entry(f, textvariable=self.share_url, state="readonly", font=("Consolas", 11))
        e.grid(row=2, column=0, sticky="ew")
        b = ttk.Frame(f)
        b.grid(row=3, column=0, sticky="w", pady=12)
        ttk.Button(b, text="Copy URL", command=lambda: (self.clipboard_clear(), self.clipboard_append(self.share_url.get()))).pack(side="left")
        ttk.Button(b, text="Open", command=lambda: self.share_url.get().startswith("http") and webbrowser.open(self.share_url.get())).pack(side="left", padx=8)
        ttk.Button(b, text="Allow Apache through Windows Firewall", command=self.firewall).pack(side="left")
        ttk.Label(f, style="Muted.TLabel", wraplength=680, justify="left", text=(
            "nip.io is public wildcard DNS — <anything>.192.168.1.20.nip.io resolves to 192.168.1.20 — so devices "
            "on your network need no hosts-file edits. Click Apply after enabling; your LAN IP is re-detected each time.")).grid(row=4, column=0, sticky="w")

        ttk.Separator(f).grid(row=5, column=0, sticky="ew", pady=16)
        ttk.Label(f, text="Share this site over the internet",
                  style="Section.TLabel").grid(row=6, column=0, sticky="w")
        self.public_url = tk.StringVar()
        ttk.Entry(f, textvariable=self.public_url, state="readonly",
                  font=("Consolas", 11)).grid(row=7, column=0, sticky="ew", pady=(12, 0))
        b2 = ttk.Frame(f)
        b2.grid(row=8, column=0, sticky="w", pady=12)
        self.tunnel_btn = ttk.Button(b2, text="Start public link", style="Accent.TButton", command=self.toggle_tunnel)
        self.tunnel_btn.pack(side="left")
        ttk.Button(b2, text="Copy URL", command=self.copy_public_url).pack(side="left", padx=8)
        ttk.Button(b2, text="Open", command=lambda: self.public_url.get().startswith("http")
                   and webbrowser.open(self.public_url.get())).pack(side="left")
        ttk.Label(f, style="Muted.TLabel", wraplength=680, justify="left", text=(
            "Free Cloudflare Quick Tunnel: a random https://….trycloudflare.com address that works anywhere, with no "
            "port forwarding or account (cloudflared downloads automatically the first time). Anyone with the link "
            "can open the site until you click Stop or close LocalDevX. Apps that store their own URL "
            "(e.g. WordPress) may redirect visitors to the .test address.")
                  ).grid(row=9, column=0, sticky="w")

    def _tab_notes(self):
        f = ttk.Frame(self.nb, padding=(24, 22))
        f.rowconfigure(0, weight=1)
        f.columnconfigure(0, weight=1)
        self.nb.add(f, text="Notes")
        self.notes_txt = self._text(f, height=10, wrap="word", undo=True, font=("Segoe UI", 10))
        self.notes_txt.grid(row=0, column=0, sticky="nsew")
        self.notes_txt.bind("<<Modified>>", self._notes_modified)

    def _build_log(self):
        frame = ttk.Frame(self, padding=(20, 12, 20, 20))
        frame.pack(fill="x", side="bottom")
        head = ttk.Frame(frame)
        head.pack(fill="x", pady=(0, 8))
        ttk.Label(head, text="ACTIVITY", style="Muted.TLabel").pack(side="left", anchor="s")
        ttk.Button(head, text="Restart", command=lambda: self.server("restart")).pack(side="right")
        ttk.Button(head, text="Stop", command=lambda: self.server("stop")).pack(side="right", padx=(0, 6))
        ttk.Button(head, text="Start", command=lambda: self.server("start")).pack(side="right", padx=(0, 6))
        self.status_lbl = ttk.Label(head, text="Apache: …", style="Section.TLabel")
        self.status_lbl.pack(side="right", padx=(0, 16))
        self.status_canvas = tk.Canvas(head, width=14, height=14, highlightthickness=0)
        self.status_canvas.pack(side="right", padx=(0, 6))
        self.status_dot = self.status_canvas.create_oval(2, 2, 12, 12, outline="")
        self.log_txt = self._text(frame, "log", height=6, font=("Consolas", 9), state="disabled", wrap="word")
        self.log_txt.pack(fill="x")

    # ------------------------------------------------------------ list & form
    def refresh_list(self, select=None):
        q = self.search.get().lower().strip() if hasattr(self, "search") else ""
        self.tree.delete(*self.tree.get_children())
        for i, s in enumerate(sorted(self.store.sites, key=lambda x: x.name)):
            if q and q not in s.name and not any(q in a for a in s.aliases):
                continue
            live = id(s) in getattr(self, "tunnels", {})
            flags = ("🌐 " if live else "") + ("🔒 " if s.ssl else "") + ("" if s.enabled else "off")
            self.tree.insert("", "end", iid=str(id(s)), text=("● " if s.enabled else "○ ") + s.name,
                             values=(flags,), tags=() if s.enabled else ("off",))
        n = len(self.store.sites)
        self.count_lbl.config(text=f"{n} site{'s' if n != 1 else ''}")
        target = select or self.current
        if target is not None and self.tree.exists(str(id(target))):
            self.tree.selection_set(str(id(target)))
            self.tree.see(str(id(target)))

    def on_select(self, _=None):
        sel = self.tree.selection()
        site = next((s for s in self.store.sites if str(id(s)) == sel[0]), None) if sel else None
        if site is self.current:
            return
        self.current = site
        self.load_form()

    def load_form(self):
        s = self.current
        self._loading = True
        try:
            self._set_form_state(s is not None)
            if s is None:
                self.title_lbl.config(text="No site selected")
                self.url_lbl.config(text="")
                return
            for k, var in self.v.items():
                val = getattr(s, k)
                if k == "aliases":
                    val = " ".join(val)
                var.set(val)
            for widget, val in ((self.custom_txt, s.custom), (self.notes_txt, s.notes)):
                widget.delete("1.0", "end")
                widget.insert("1.0", val)
                widget.edit_modified(False)
            self._update_header()
        finally:
            self._loading = False

    def _set_form_state(self, on):
        def walk(w):
            for c in w.winfo_children():
                try:
                    if isinstance(c, (ttk.Entry, ttk.Checkbutton, ttk.Spinbox, ttk.Combobox, ttk.Button)) and c.master is not None:
                        if not (isinstance(c, ttk.Entry) and str(c.cget("state")) == "readonly"):
                            c.state(["!disabled"] if on else ["disabled"])
                    elif isinstance(c, tk.Text):
                        c.config(state="normal" if on else "disabled")
                except tk.TclError:
                    pass
                walk(c)
        walk(self.nb)

    def on_field(self, key):
        if self._loading or self.current is None:
            return
        s = self.current
        val = self.v[key].get()
        if key == "aliases":
            val = [a.lower() for a in val.replace(",", " ").split() if a]
        elif key in ("http_port", "https_port"):
            try:
                val = int(val)
            except ValueError:
                return
        elif key == "name":
            val = val.strip().lower()
        elif isinstance(val, str):
            val = val.strip()
        if getattr(s, key) == val:
            return
        setattr(s, key, val)
        if key == "ssl" and val and not s.cert_file:
            self._loading = True
            s.cert_file = f"{s.site_root}/ssl/{s.name}.crt"
            s.key_file = f"{s.site_root}/ssl/{s.name}.key"
            self.v["cert_file"].set(s.cert_file)
            self.v["key_file"].set(s.key_file)
            self._loading = False
        self.mark_dirty()
        if key in ("name", "enabled", "ssl"):
            self.refresh_list()
        self._update_header()

    def _custom_modified(self, _):
        if self.custom_txt.edit_modified():
            if not self._loading and self.current:
                self.current.custom = self.custom_txt.get("1.0", "end-1c")
                self.mark_dirty()
            self.custom_txt.edit_modified(False)

    def _notes_modified(self, _):
        if self.notes_txt.edit_modified():
            if not self._loading and self.current:
                self.current.notes = self.notes_txt.get("1.0", "end-1c")
                self.store.save()  # notes don't affect Apache
            self.notes_txt.edit_modified(False)

    def _update_header(self):
        s = self.current
        if not s:
            return
        self.title_lbl.config(text=s.name or "(unnamed)")
        self.url_lbl.config(text=self.site_url(s) + ("" if s.enabled else "   (disabled)"))
        ip = apache.lan_ip()
        if s.share_lan and ip:
            proto, port = ("https", s.https_port) if s.ssl and not s.http else ("http", s.http_port)
            default = 443 if proto == "https" else 80
            self.share_url.set(f"{proto}://{apache.lan_alias(s, ip)}" + ("" if port == default else f":{port}"))
        else:
            self.share_url.set("Sharing is off." if ip else "No network connection detected.")
        self._update_tunnel_view()
        if os.path.isfile(sslcerts.CA_CRT):
            self.ca_lbl.config(text="LocalDevX signs every new site certificate with its own local CA. "
                                    "Trust it once and all sites show a valid padlock in Chrome & Edge "
                                    "(Firefox: set security.enterprise_roots.enabled = true).\n"
                                    f"CA file: {fwd(sslcerts.CA_CRT)}")
        else:
            self.ca_lbl.config(text="The local CA is created the first time a certificate is generated.")

    @staticmethod
    def site_url(s):
        if s.ssl:
            return f"https://{s.name}" + ("" if s.https_port == 443 else f":{s.https_port}")
        return f"http://{s.name}" + ("" if s.http_port == 80 else f":{s.http_port}")

    def mark_dirty(self, on=True):
        self.dirty = on
        self.dirty_lbl.config(text="● Unapplied changes" if on else "")
        self.store.save()

    # ------------------------------------------------------------ actions
    def add_site(self):
        st = self.store.settings
        name = simpledialog.askstring("New Site", f"Host name for the new site (e.g. myproject{st.tld}):", parent=self)
        if not name:
            return
        name = name.strip().lower()
        if "." not in name:
            name += st.tld
        site = default_site(st, name)
        errs = validate_site(site, self.store.sites)
        if errs:
            messagebox.showerror("New Site", "\n".join(errs), parent=self)
            return
        self.store.sites.append(site)
        self.removed.discard(name)
        self.current = None
        self.refresh_list(select=site)
        self.mark_dirty()
        self.log(f"Added {name} → {site.docroot}. Click Apply Changes to create it.")

    def duplicate_site(self):
        if not self.current:
            return
        name = simpledialog.askstring("Duplicate", "Host name for the copy:", parent=self)
        if not name:
            return
        name = name.strip().lower()
        if "." not in name:
            name += self.store.settings.tld
        site = copy.deepcopy(self.current)
        old = site.name
        site.name = name
        site.aliases = [a.replace(old, name) for a in site.aliases]
        site.cert_file = site.key_file = ""
        if site.ssl:
            site.cert_file = f"{site.site_root}/ssl/{name}.crt"
            site.key_file = f"{site.site_root}/ssl/{name}.key"
        errs = validate_site(site, self.store.sites)
        if errs:
            messagebox.showerror("Duplicate", "\n".join(errs), parent=self)
            return
        self.store.sites.append(site)
        self.current = None
        self.refresh_list(select=site)
        self.mark_dirty()

    def remove_site(self):
        s = self.current
        if not s:
            return
        if not messagebox.askyesno("Remove Site", f"Remove {s.name} from Apache and the hosts file?\n\n"
                                   "Your files in\n" + fwd(s.docroot) + "\nare NOT deleted.", parent=self):
            return
        self.stop_tunnel(s)
        self.store.sites.remove(s)
        self.removed.update([s.name, *s.aliases])
        self.current = None
        self.refresh_list()
        self.load_form()
        self.mark_dirty()

    def apply(self):
        if self.busy:
            return
        self.busy = True
        self.apply_btn.state(["disabled"])
        self.log("— Applying changes —")

        def work():
            try:
                ok = core.apply_changes(self.store, self.log, removed=self.removed)
            except Exception as e:  # surface anything unexpected
                ok = False
                self.log(f"Error: {e!r}")
            self.after(0, lambda: self._applied(ok))
        threading.Thread(target=work, daemon=True).start()

    def _applied(self, ok):
        self.busy = False
        self.apply_btn.state(["!disabled"])
        if ok:
            self.removed.clear()
            self.mark_dirty(False)
        self.refresh_list()
        self._loading = True
        if self.current:  # cert paths may have been filled in
            self.v["cert_file"].set(self.current.cert_file)
            self.v["key_file"].set(self.current.key_file)
        self._loading = False
        self.update_status()

    def server(self, action):
        if self.busy:
            return
        self.busy = True
        fn = {"start": apache.start_apache, "stop": apache.stop_apache, "restart": apache.restart_apache}[action]
        self.log(f"{action.title()}ing Apache…")

        def work():
            try:
                ok, msg = fn(self.store.settings)
                self.log(msg)
            finally:
                self.busy = False
                self.after(0, self.update_status)
        threading.Thread(target=work, daemon=True).start()

    def update_status(self):
        def work():
            running = apache.apache_running(self.store.settings)
            self.after(0, lambda: self._show_status(running))
        threading.Thread(target=work, daemon=True).start()

    def _show_status(self, running):
        self.apache_up = running
        self._paint_status()
        self.status_lbl.config(text="Apache: running" if running else "Apache: stopped")
        self._update_tray_tip()

    def _update_tray_tip(self):
        if not self.tray_ok:
            return
        tip = f"{APP_TITLE} · Apache " + ("running" if self.apache_up else "stopped")
        live = sum(1 for t in self.tunnels.values() if t is not None)
        if live:
            tip += f" · {live} public link{'s' if live != 1 else ''}"
        self.tray.set_tip(tip)

    def _paint_status(self):
        fill = self.c["muted"] if self.apache_up is None else self.c["ok" if self.apache_up else "bad"]
        self.status_canvas.itemconfig(self.status_dot, fill=fill)

    def open_browser(self):
        if self.current:
            webbrowser.open(self.site_url(self.current))

    def open_folder(self):
        if self.current and os.path.isdir(self.current.site_root):
            os.startfile(self.current.site_root)

    def open_editor(self):
        s = self.current
        if not s:
            return
        ed = self.store.settings.editor
        if ed:
            subprocess.Popen([ed, s.site_root])
        else:
            for cmd in ("code.cmd", "code"):
                try:
                    subprocess.Popen([cmd, s.site_root], creationflags=0x08000000)
                    return
                except OSError:
                    continue
            self.open_folder()

    def open_errorlog(self):
        s = self.current
        if not s:
            return
        path = f"{s.log_dir}/error_log.log" if s.log_dir else self.store.settings.apache_dir + "/logs/error.log"
        if os.path.isfile(path):
            os.startfile(path)
        else:
            messagebox.showinfo("Error Log", f"No log yet:\n{path}", parent=self)

    def regen_cert(self):
        s = self.current
        if not s:
            return
        if not s.cert_file or not s.key_file:
            s.cert_file = f"{s.site_root}/ssl/{s.name}.crt"
            s.key_file = f"{s.site_root}/ssl/{s.name}.key"
        if not messagebox.askyesno("Regenerate", f"Create a new certificate for {s.name} signed by the local CA?\n"
                                   "The existing certificate and key are backed up first.", parent=self):
            return
        st = self.store.settings
        for p in (s.cert_file, s.key_file):
            b = backup_file(p, st.backup_dir)
            if b:
                self.log(f"Backed up {fwd(p)} → {fwd(b)}")
        extra = [apache.lan_alias(s)] if s.share_lan and apache.lan_ip() else []
        ok, out = sslcerts.issue_cert(st, s, extra)
        self.log(out)
        if ok:
            self.load_form()
            self.mark_dirty()
            self.log("Click Apply Changes (Apache needs a restart to load the new certificate).")

    def show_cert(self):
        if self.current:
            messagebox.showinfo("Certificate", sslcerts.cert_info(self.store.settings, self.current.cert_file), parent=self)

    def trust_ca(self):
        ok, out = sslcerts.ensure_ca(self.store.settings)
        if not ok:
            self.log(out)
            return
        ok, out = sslcerts.trust_ca()
        self.log("Local CA is now trusted by Windows. Restart your browser." if ok else out)
        self._update_header()

    def untrust_ca(self):
        ok, out = sslcerts.untrust_ca()
        self.log("Removed local CA from the Windows trust store." if ok else out)

    def firewall(self):
        ok, msg = apache.allow_firewall(self.store.settings)
        self.log(msg)

    # ------------------------------------------------------------ internet sharing (tunnels)
    def _update_tunnel_view(self):
        s = self.current
        if s is None or id(s) not in self.tunnels:
            self.public_url.set("Not shared on the internet.")
            self.tunnel_btn.config(text="Start public link")
        else:
            t = self.tunnels[id(s)]  # None while cloudflared is being downloaded/started
            self.public_url.set((t and t.url) or "Starting tunnel…")
            self.tunnel_btn.config(text="Stop public link")
        self._update_tray_tip()

    def copy_public_url(self):
        url = self.public_url.get()
        if url.startswith("http"):
            self.clipboard_clear()
            self.clipboard_append(url)

    def toggle_tunnel(self):
        s = self.current
        if not s:
            return
        if id(s) in self.tunnels:
            self.stop_tunnel(s)
        else:
            self.start_tunnel(s)

    def start_tunnel(self, s):
        st = self.store.settings
        if not s.enabled:
            messagebox.showinfo("Public link", f"{s.name} is disabled. Enable it and click Apply Changes first.", parent=self)
            return
        if f"ServerName {s.name}\n" not in core.read_text(st.vhosts_file):
            messagebox.showinfo("Public link", f"{s.name} isn't live in Apache yet.\nClick Apply Changes first.", parent=self)
            return
        exe = tunnel.find_cloudflared(st)
        if not exe and not messagebox.askyesno(
                "Public link", "Internet sharing uses Cloudflare's free 'cloudflared' tool (about 40 MB), "
                "which isn't installed yet.\n\nDownload it now from Cloudflare's official GitHub releases?", parent=self):
            return
        if not messagebox.askyesno(
                "Public link", f"Make {s.name} reachable from the internet?\n\n"
                "Anyone who has the link can open the site (and any admin pages it has) until you stop "
                "sharing or close LocalDevX.", icon="warning", parent=self):
            return
        self.tunnels[id(s)] = None  # placeholder: "starting"
        self.refresh_list()
        self.public_url.set("Starting tunnel…")
        self.tunnel_btn.config(text="Stop public link")

        def work():
            path = exe
            try:
                if not path:
                    self.log("Downloading cloudflared…")
                    path = tunnel.download_cloudflared(self.log)
                    self.log(f"Installed cloudflared to {fwd(path)}")
                if not apache.apache_running(st):
                    self.log("Apache isn't running — starting it…")
                    ok, msg = apache.start_apache(st)
                    self.log(msg)
                self.after(0, lambda: self._launch_tunnel(s, path))
            except Exception as e:
                self.log(f"Could not start the public link: {e}")
                self.after(0, lambda: self._tunnel_gone(s))
        threading.Thread(target=work, daemon=True).start()

    def _launch_tunnel(self, s, path):
        if id(s) not in self.tunnels or self.tunnels[id(s)] is not None:
            return  # user pressed Stop while we were preparing
        t = tunnel.Tunnel(path, s, lambda kind, tun: self.after(0, lambda: self._tunnel_event(kind, tun)))
        try:
            t.start()
        except OSError as e:
            self.log(f"Could not run cloudflared: {e}")
            self._tunnel_gone(s)
            return
        self.tunnels[id(s)] = t
        self.log(f"Opening public tunnel for {s.name} → {t.origin} …")

    def _tunnel_event(self, kind, t):
        if self.tunnels.get(id(t.site)) is not t:
            return
        if kind == "url":
            self.log(f"🌐 {t.site.name} is public at {t.url}  (the address can take ~10 s to start working)")
        elif kind == "exit":
            if not t.stopping:
                self.log(f"Public link for {t.site.name} stopped unexpectedly:\n{t.error_summary()}")
            self._tunnel_gone(t.site)
            return
        self.refresh_list()
        self._update_tunnel_view()

    def _tunnel_gone(self, s):
        self.tunnels.pop(id(s), None)
        self.refresh_list()
        self._update_tunnel_view()

    def stop_tunnel(self, s):
        t = self.tunnels.pop(id(s), None)
        if t is not None:
            t.stop()
            self.log(f"Stopped public link for {s.name}.")
        self.refresh_list()
        if self.current is s:
            self._update_tunnel_view()

    def preview_config(self):
        win = tk.Toplevel(self)
        win.title(f"Generated httpd-vhosts.conf (preview) — {APP_TITLE}")
        win.geometry("860x640")
        self.toplevels.append(win)
        theme.style_toplevel(win, self.c, self.theme_name == "dark")
        f = ttk.Frame(win, padding=16)
        f.pack(fill="both", expand=True)
        t = self._text(f, font=("Consolas", 10), wrap="none")
        sb = ttk.Scrollbar(f, command=t.yview)
        t.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(6, 0))
        t.pack(fill="both", expand=True)
        t.insert("1.0", apache.generate_vhosts(self.store.settings, self.store.sites))
        t.config(state="disabled")

    # ------------------------------------------------------------ settings / first run
    def open_settings(self):
        SettingsDialog(self)

    def _first_run(self):
        st = self.store.settings
        if st.imported or self.store.sites:
            return
        if not os.path.isfile(st.httpd_exe):
            messagebox.showwarning(APP_NAME, f"XAMPP was not found at {st.xampp_dir}.\nSet the correct folder in Settings.", parent=self)
            self.open_settings()
            return
        found = core.import_existing(self.store)
        st.imported = True
        if found and messagebox.askyesno(
                APP_NAME, f"Found {len(found)} existing virtual host(s) in\n{st.vhosts_file}:\n\n" +
                "\n".join("  • " + s.name for s in found[:15]) + ("\n  …" if len(found) > 15 else "") +
                "\n\nImport them? From now on LocalDevX will manage that file "
                "(it is backed up before every change)."):
            self.store.sites = found
            self.log(f"Imported {len(found)} sites. Nothing has been changed yet — review and click Apply Changes.")
        self.store.save()
        self.refresh_list()

    # ------------------------------------------------------------ log
    def log(self, msg):
        self.logq.put(msg)

    def _poll_log(self):
        while not self.logq.empty():
            msg = self.logq.get()
            self.log_txt.config(state="normal")
            self.log_txt.insert("end", msg + "\n")
            self.log_txt.see("end")
            self.log_txt.config(state="disabled")
        while not self.trayq.empty():
            self._tray_command(self.trayq.get())
        self._poll_id = self.after(150, self._poll_log)

    # ------------------------------------------------------------ tray / window lifecycle
    def _tray_menu(self):
        """Called on the tray thread - reads plain attributes only, never Tk."""
        up = self.apache_up
        items = [(APP_TITLE, None, False), None, ("Open LocalDevX", "show", True), None,
                 ("Apache: " + ("running" if up else "stopped" if up is False else "…"), None, False),
                 ("Start Apache", "start", not up), ("Stop Apache", "stop", up is not False),
                 ("Restart Apache", "restart", True)]
        live = sum(1 for t in list(self.tunnels.values()) if t is not None)
        if live:
            items += [None, (f"Stop public links ({live})", "stop_tunnels", True)]
        return items + [None, ("Quit LocalDevX", "quit", True)]

    def _tray_command(self, cmd):
        if cmd == "show":
            self.show_window()
        elif cmd in ("start", "stop", "restart"):
            self.server(cmd)
        elif cmd == "stop_tunnels":
            for s in [t.site for t in list(self.tunnels.values()) if t is not None]:
                self.stop_tunnel(s)
            self._update_tray_tip()
        elif cmd == "quit":
            self.quit_app()
        elif cmd == "force_quit":  # installer/uninstaller: no questions (everything is already saved)
            self.dirty = False
            self.quit_app()

    def show_window(self):
        self.deiconify()
        self.state("normal")
        self.lift()
        self.attributes("-topmost", True)
        self.after(250, lambda: self.attributes("-topmost", False))
        self.focus_force()

    def hide_to_tray(self):
        self.withdraw()
        if not self._told_tray:
            self._told_tray = True
            self.tray.balloon("LocalDevX is still running",
                              "It's in the system tray. Click the icon to open it, or right-click → Quit to exit.")

    def _on_unmap(self, e):
        if e.widget is self and self.tray_ok and self.store.settings.minimize_to_tray:
            self.after(10, lambda: self.state() == "iconic" and self.hide_to_tray())

    def on_close(self):
        """Window X button: hide to the tray (if enabled), otherwise quit."""
        if self.tray_ok and self.store.settings.close_to_tray:
            self.hide_to_tray()
        else:
            self.quit_app()

    def quit_app(self):
        """Fully exit: stop public links, remove the tray icon, end the process."""
        if self.dirty:
            self.show_window()
            if not messagebox.askyesno(APP_NAME, "You have changes that haven't been applied to Apache yet.\n"
                                                 "They are saved and will still be here next time.\n\nQuit anyway?",
                                       parent=self):
                return
        for t in list(self.tunnels.values()):
            if t is not None:
                t.stop()
        self.tunnels.clear()
        if self.tray:
            self.tray.remove()
        if getattr(self, "_poll_id", None):
            self.after_cancel(self._poll_id)
        self.destroy()


class SettingsDialog(tk.Toplevel):
    FIELDS = [
        ("xampp_dir", "XAMPP folder", "dir"),
        ("vhosts_file", "Virtual hosts file", "file"),
        ("sites_root", "New sites folder", "dir"),
        ("docroot_subdir", "Document root sub-folder", None),
        ("tld", "Default domain suffix", None),
        ("service_name", "Apache service name", None),
        ("backup_dir", "Backup folder", "dir"),
        ("hosts_file", "Hosts file", "file"),
        ("editor", "Code editor (.exe)", "file"),
        ("cloudflared", "cloudflared.exe (optional)", "file"),
    ]
    CHECKS = [
        ("default_ssl", "New sites use HTTPS by default"),
        ("add_www_alias", "Add www. alias to new sites"),
        ("create_index", "Put a starter index.php in empty document roots"),
        ("minimize_to_tray", "Minimizing the window hides it in the system tray"),
        ("close_to_tray", "Closing the window (X) keeps LocalDevX running in the tray — use Quit to exit"),
    ]

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title(f"Settings — {APP_TITLE}")
        self.transient(app)
        app.toplevels.append(self)
        theme.style_toplevel(self, app.c, app.theme_name == "dark")
        self.grab_set()
        st = app.store.settings
        f = ttk.Frame(self, padding=(28, 24))
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        self.vars = {}
        r = 0
        for key, label, kind in self.FIELDS:
            var = tk.StringVar(value=getattr(st, key))
            self.vars[key] = var
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=5, padx=(0, 18))
            ttk.Entry(f, textvariable=var, width=55).grid(row=r, column=1, sticky="ew", pady=5)
            if kind:
                ttk.Button(f, text="…", width=3, command=lambda v=var, k=kind: self._browse(v, k)).grid(row=r, column=2, padx=(8, 0))
            r += 1
        ttk.Label(f, text="Appearance").grid(row=r, column=0, sticky="w", pady=5, padx=(0, 18))
        self.theme_var = tk.StringVar(value={"light": "Light", "dark": "Dark"}.get(st.theme, "Match Windows"))
        ttk.Combobox(f, textvariable=self.theme_var, state="readonly", width=18,
                     values=("Match Windows", "Light", "Dark")).grid(row=r, column=1, sticky="w", pady=5)
        r += 1
        ttk.Separator(f).grid(row=r, column=0, columnspan=3, sticky="ew", pady=14)
        r += 1
        for key, label in self.CHECKS:
            var = tk.BooleanVar(value=getattr(st, key))
            self.vars[key] = var
            ttk.Checkbutton(f, text=label, variable=var).grid(row=r, column=1, sticky="w", pady=2)
            r += 1
        self.startup_was = startup.is_enabled()
        self.startup_var = tk.BooleanVar(value=self.startup_was)
        ttk.Checkbutton(f, text="Start LocalDevX when I sign in to Windows (minimized to the tray)",
                        variable=self.startup_var).grid(row=r, column=1, sticky="w", pady=2)
        r += 1
        b = ttk.Frame(f)
        b.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(22, 0))
        ttk.Button(b, text="Re-import from vhosts file…", command=self.reimport).pack(side="left")
        ttk.Button(b, text="Open backups", command=lambda: (os.makedirs(st.backup_dir, exist_ok=True), os.startfile(st.backup_dir))).pack(side="left", padx=8)
        ttk.Button(b, text="Save", style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="right", padx=8)
        credit = ttk.Frame(f)
        credit.grid(row=r + 1, column=0, columnspan=3, sticky="w", pady=(16, 0))
        ttk.Label(credit, text=f"{APP_TITLE}  ·  Created by {APP_AUTHOR}  ·  ",
                  style="Muted.TLabel").pack(side="left")
        mail = ttk.Label(credit, text=APP_AUTHOR_EMAIL, style="Link.TLabel", cursor="hand2")
        mail.pack(side="left")
        mail.bind("<Button-1>", lambda e: webbrowser.open(f"mailto:{APP_AUTHOR_EMAIL}"))

    def _browse(self, var, kind):
        p = filedialog.askdirectory(parent=self) if kind == "dir" else filedialog.askopenfilename(parent=self)
        if p:
            var.set(fwd(p))

    def save(self):
        st = self.app.store.settings
        for k, var in self.vars.items():
            val = var.get()
            setattr(st, k, fwd(val) if isinstance(val, str) and ("dir" in k or "file" in k) else val)
        if st.tld and not st.tld.startswith("."):
            st.tld = "." + st.tld
        st.theme = {"Light": "light", "Dark": "dark"}.get(self.theme_var.get(), "")
        self.app.store.save()
        want = self.startup_var.get()
        if want != self.startup_was:
            ok, out = startup.enable() if want else startup.disable()
            if ok:
                self.app.log("LocalDevX will now start when you sign in to Windows (minimized to the tray)."
                             if want else "LocalDevX will no longer start with Windows.")
            else:
                self.app.log("Could not change the Start with Windows setting:\n" + out)
        self.destroy()
        self.app.apply_theme(theme.resolve(st.theme), save=False)

    def reimport(self):
        found = core.import_existing(self.app.store)
        existing = {s.name for s in self.app.store.sites}
        new = [s for s in found if s.name not in existing]
        if not new:
            messagebox.showinfo("Import", "No new virtual hosts found.", parent=self)
            return
        if messagebox.askyesno("Import", f"Add {len(new)} site(s)?\n\n" + "\n".join(s.name for s in new), parent=self):
            self.app.store.sites += new
            self.app.refresh_list()
            self.app.mark_dirty()


def _command_line(args):
    """Switches used by the installer / uninstaller. Returns an exit code, or None to start the app."""
    if "--enable-startup" in args:
        return 0 if startup.enable()[0] else 1
    if "--disable-startup" in args:
        return 0 if startup.disable()[0] else 1
    if "--quit" in args:
        return 0 if tray.quit_running_instance() else 1
    if "--uninstall-cleanup" in args:
        tray.quit_running_instance()
        startup.disable()
        apache.remove_firewall()
        return 0
    return None


def _single_instance(wait=10.0):
    """Make sure only one LocalDevX ever runs. Returns the instance lock, or None if another copy
    owns it (that copy is asked to show its window, and this one must exit).

    A second copy never starts, even if the first one is still starting up (its tray window
    doesn't exist yet) or has no tray icon: we keep trying to reach it, and if it was just quitting
    we take over the lock once it's gone."""
    import ctypes
    import time
    lock = startup.acquire_instance_lock()
    deadline = time.monotonic() + wait
    while lock is None:
        if tray.signal_running_instance():
            return None
        if time.monotonic() > deadline:
            try:
                ctypes.windll.user32.MessageBoxW(
                    None, f"{APP_NAME} is already running.\n\nLook for its icon in the system tray, "
                    "near the clock (it may be under the ^ arrow).", APP_TITLE, 0x40 | 0x10000)  # info, foreground
            except Exception:
                pass
            return None
        time.sleep(0.25)
        lock = startup.acquire_instance_lock()  # the other copy may have just exited
    return lock


def main():
    code = _command_line(sys.argv[1:])
    if code is not None:
        sys.exit(code)
    try:
        legacy.migrate_data()  # first launch after the rename: bring sites + settings across
    except OSError:
        pass
    lock = _single_instance()
    if lock is None:
        return  # another copy is running: it has been brought to the front instead
    app = App(start_hidden="--minimized" in sys.argv)
    app._instance_lock = lock  # keep the mutex alive for the app's lifetime
    app.mainloop()
