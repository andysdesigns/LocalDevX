"""Internet sharing through Cloudflare Quick Tunnels (cloudflared).

A quick tunnel gives a random public https://<words>.trycloudflare.com URL that forwards
to Apache on this PC. It needs no Cloudflare account, no router port forwarding and works
behind CGNAT, because cloudflared only makes outbound connections.

The tunnel rewrites the Host header to the site's ServerName, so Apache picks the right
<VirtualHost> without any config change.
"""
import ctypes
import os
import re
import shutil
import subprocess
import threading
import urllib.request
from ctypes import wintypes

from . import APP_NAME, APP_VERSION
from .model import APP_DIR

NO_WINDOW = 0x08000000
DOWNLOAD_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
BIN_DIR = os.path.join(APP_DIR, "bin")
LOCAL_EXE = os.path.join(BIN_DIR, "cloudflared.exe")
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


# ---------------------------------------------------------------- locating / installing

def find_cloudflared(settings):
    """Return the path to cloudflared.exe, or '' if it isn't installed."""
    custom = (getattr(settings, "cloudflared", "") or "").strip()
    if custom and os.path.isfile(custom):
        return custom
    for cand in (shutil.which("cloudflared"), LOCAL_EXE,
                 r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
                 r"C:\Program Files\cloudflared\cloudflared.exe"):
        if cand and os.path.isfile(cand):
            return cand
    return ""


def download_cloudflared(log=lambda m: None):
    """Download Cloudflare's official cloudflared.exe into %APPDATA%\\LocalDevX\\bin."""
    os.makedirs(BIN_DIR, exist_ok=True)
    tmp = LOCAL_EXE + ".part"
    req = urllib.request.Request(DOWNLOAD_URL, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done, last = 0, -1
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                pct = done * 100 // total
                if pct // 20 != last:
                    last = pct // 20
                    log(f"  downloading cloudflared… {pct}%")
    with open(tmp, "rb") as f:
        if f.read(2) != b"MZ" or os.path.getsize(tmp) < 5_000_000:
            os.remove(tmp)
            raise RuntimeError("Downloaded file is not a valid cloudflared.exe")
    os.replace(tmp, LOCAL_EXE)
    return LOCAL_EXE


# ---------------------------------------------------------------- kill-on-exit job object
# cloudflared is put in a Windows Job object, so if LocalDevX exits or crashes the
# tunnel dies with it and the site is never left exposed by accident.

class _IO(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]


class _Basic(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _Extended(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", _IO),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


_job = None


def _attach_to_job(proc):
    global _job
    try:
        k32 = ctypes.windll.kernel32
        if _job is None:
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            h = k32.CreateJobObjectW(None, None)
            info = _Extended()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            if not h or not k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):
                return
            _job = h
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.AssignProcessToJobObject(_job, int(proc._handle))
    except Exception:
        pass  # best effort; tunnels are still stopped on normal close


# ---------------------------------------------------------------- tunnel process

class Tunnel:
    """One running quick tunnel for one site.

    `on_event(kind, tunnel)` is called from a background thread with kind "url" when the
    public URL is known and "exit" when cloudflared stops."""

    def __init__(self, exe, site, on_event):
        self.exe = exe
        self.site = site
        self.host = site.name
        self.on_event = on_event
        self.url = ""
        self.proc = None
        self.output = []
        self.stopping = False
        if site.ssl:  # use HTTPS origin so apps that force HTTPS don't redirect-loop
            port = site.https_port
            self.origin = f"https://127.0.0.1:{port}"
        else:
            port = site.http_port
            self.origin = f"http://127.0.0.1:{port}"

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def command(self):
        cmd = [self.exe, "tunnel", "--no-autoupdate", "--url", self.origin,
               "--http-host-header", self.host]
        if self.origin.startswith("https"):
            # SNI must match the site; the local cert isn't publicly trusted, which is fine for loopback
            cmd += ["--origin-server-name", self.host, "--no-tls-verify"]
        return cmd

    def start(self):
        self.proc = subprocess.Popen(self.command(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, text=True, errors="replace",
                                     creationflags=NO_WINDOW)
        _attach_to_job(self.proc)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            line = line.rstrip()
            self.output = (self.output + [line])[-40:]
            if not self.url:
                m = URL_RE.search(line)
                if m and "api.trycloudflare.com" not in m.group(0):
                    self.url = m.group(0)
                    self.on_event("url", self)
        self.proc.wait()
        self.on_event("exit", self)

    def stop(self):
        self.stopping = True
        if self.running:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def error_summary(self, n=8):
        errs = [l for l in self.output if " ERR " in l or "error" in l.lower()]
        return "\n".join((errs or self.output)[-n:])
