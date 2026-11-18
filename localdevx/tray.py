"""System-tray icon for Windows, written with ctypes (no extra packages).

The icon lives on its own thread with its own Win32 message loop. It never touches Tk:
menu clicks are handed to `on_command(name)`, and the app hands them to the Tk thread
through a queue.
"""
import ctypes
import os
import struct
import threading
from ctypes import wintypes as wt

from .model import APP_DIR

ICON_FILE = os.path.join(APP_DIR, "localdevx-icon-1.ico")
CLASS_NAME = "LocalDevXTrayWindow"
WM_TRAY = 0x8001       # WM_APP + 1: tray icon callback
WM_SHOW_APP = 0x8002   # WM_APP + 2: sent by a second instance ("show yourself")
WM_QUIT_APP = 0x8003   # WM_APP + 3: sent by the installer/uninstaller ("exit now")

# private DLL handles so our argtypes don't clash with other modules using ctypes.windll
user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR)]


class GUID(ctypes.Structure):
    _fields_ = [("d1", wt.DWORD), ("d2", wt.WORD), ("d3", wt.WORD), ("d4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT),
                ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
                ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD), ("szInfo", wt.WCHAR * 256),
                ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", wt.HICON)]


def _sig(fn, res, *args):
    fn.restype, fn.argtypes = res, list(args)


_sig(user32.DefWindowProcW, LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_sig(user32.RegisterClassW, wt.ATOM, ctypes.POINTER(WNDCLASSW))
_sig(user32.CreateWindowExW, wt.HWND, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
     ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID)
_sig(user32.LoadImageW, wt.HANDLE, wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT)
_sig(user32.LoadIconW, wt.HICON, wt.HINSTANCE, ctypes.c_void_p)
_sig(user32.GetSystemMetrics, ctypes.c_int, ctypes.c_int)
_sig(user32.CreatePopupMenu, wt.HMENU)
_sig(user32.AppendMenuW, wt.BOOL, wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR)
_sig(user32.SetMenuDefaultItem, wt.BOOL, wt.HMENU, wt.UINT, wt.UINT)
_sig(user32.TrackPopupMenu, ctypes.c_int, wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
     wt.HWND, ctypes.c_void_p)
_sig(user32.DestroyMenu, wt.BOOL, wt.HMENU)
_sig(user32.GetCursorPos, wt.BOOL, ctypes.POINTER(wt.POINT))
_sig(user32.SetForegroundWindow, wt.BOOL, wt.HWND)
_sig(user32.PostMessageW, wt.BOOL, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_sig(user32.GetMessageW, wt.BOOL, ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT)
_sig(user32.TranslateMessage, wt.BOOL, ctypes.POINTER(wt.MSG))
_sig(user32.DispatchMessageW, LRESULT, ctypes.POINTER(wt.MSG))
_sig(user32.RegisterWindowMessageW, wt.UINT, wt.LPCWSTR)
_sig(user32.FindWindowW, wt.HWND, wt.LPCWSTR, wt.LPCWSTR)
_sig(user32.PostQuitMessage, None, ctypes.c_int)
_sig(user32.ChangeWindowMessageFilterEx, wt.BOOL, wt.HWND, wt.UINT, wt.DWORD, ctypes.c_void_p)
_sig(user32.AllowSetForegroundWindow, wt.BOOL, wt.DWORD)
_sig(shell32.Shell_NotifyIconW, wt.BOOL, wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW))
_sig(kernel32.GetModuleHandleW, wt.HMODULE, wt.LPCWSTR)

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 1, 2, 4, 0x10
WM_DESTROY, WM_CLOSE, WM_CONTEXTMENU = 0x0002, 0x0010, 0x007B
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0202, 0x0203, 0x0205


# ---------------------------------------------------------------- app icon (generated, no image files)

def _render(size, accent=(0x0F, 0x76, 0x6E)):
    """Teal rounded square with a white 'X', anti-aliased by 4x4 supersampling. Returns BGRA rows, top-down."""
    ss, rad, m = 4, 0.22, 0.04

    def in_rrect(x, y):
        lo, hi = m, 1 - m
        if not (lo <= x <= hi and lo <= y <= hi):
            return False
        cx, cy = min(max(x, lo + rad), hi - rad), min(max(y, lo + rad), hi - rad)
        return (x - cx) ** 2 + (y - cy) ** 2 <= rad * rad

    def seg(px, py, ax, ay, bx, by):
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        return ((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** 0.5

    def in_v(x, y):  # the glyph: an X
        return min(seg(x, y, .31, .31, .69, .69), seg(x, y, .69, .31, .31, .69)) <= 0.085

    rows = []
    for j in range(size):
        row = bytearray()
        for i in range(size):
            cov = white = 0
            for sj in range(ss):
                for si in range(ss):
                    x, y = (i + (si + .5) / ss) / size, (j + (sj + .5) / ss) / size
                    if in_rrect(x, y):
                        cov += 1
                        white += in_v(x, y)
            f = white / cov if cov else 0
            r, g, b = (int(c * (1 - f) + 255 * f) for c in accent)
            row += bytes((b, g, r, int(255 * cov / (ss * ss))))
        rows.append(bytes(row))
    return rows


def build_ico(sizes=(16, 20, 24, 32, 40, 48, 64)):
    images = []
    for s in sizes:
        xor = b"".join(reversed(_render(s)))          # ICO bitmaps are bottom-up
        mask = bytes(((s + 31) // 32) * 4 * s)        # all-zero AND mask (alpha does the work)
        hdr = struct.pack("<IiiHHIIiiII", 40, s, s * 2, 1, 32, 0, len(xor) + len(mask), 0, 0, 0, 0)
        images.append(hdr + xor + mask)
    out = struct.pack("<HHH", 0, 1, len(sizes))
    off = 6 + 16 * len(sizes)
    for s, img in zip(sizes, images):
        out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(img), off)  # 256 is stored as 0
        off += len(img)
    return out + b"".join(images)


def ensure_icon():
    """Write the app icon to %APPDATA%\\LocalDevX once; return its path."""
    if not os.path.isfile(ICON_FILE):
        os.makedirs(APP_DIR, exist_ok=True)
        tmp = ICON_FILE + ".tmp"
        with open(tmp, "wb") as f:
            f.write(build_ico())
        os.replace(tmp, ICON_FILE)
    return ICON_FILE


# ---------------------------------------------------------------- tray icon

class Tray:
    """`on_command(name)` is called on the tray thread; `get_menu()` returns a list of
    (label, command, enabled) tuples or None for a separator."""

    def __init__(self, tooltip, on_command, get_menu):
        self.tip = tooltip
        self.on_command = on_command
        self.get_menu = get_menu
        self.hwnd = None
        self.hicon = None
        self.ok = False
        self._ready = threading.Event()
        threading.Thread(target=self._run, daemon=True, name="tray").start()
        self._ready.wait(5)

    # -- thread
    def _run(self):
        try:
            hinst = kernel32.GetModuleHandleW(None)
            self._proc = WNDPROC(self._wndproc)  # keep a reference or it gets garbage-collected
            wc = WNDCLASSW(lpfnWndProc=self._proc, hInstance=hinst, lpszClassName=CLASS_NAME)
            user32.RegisterClassW(ctypes.byref(wc))
            self.hwnd = user32.CreateWindowExW(0, CLASS_NAME, "LocalDevX", 0, 0, 0, 0, 0,
                                               None, None, hinst, None)
            if not self.hwnd:
                return
            # let a non-elevated second instance send us "show yourself"
            user32.ChangeWindowMessageFilterEx(self.hwnd, WM_SHOW_APP, 1, None)
            user32.ChangeWindowMessageFilterEx(self.hwnd, WM_QUIT_APP, 1, None)
            self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
            sm = user32.GetSystemMetrics(49)  # SM_CXSMICON
            try:
                self.hicon = user32.LoadImageW(None, ensure_icon(), 1, sm, sm, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
            except OSError:
                self.hicon = None
            self.hicon = self.hicon or user32.LoadIconW(None, ctypes.c_void_p(32512))  # IDI_APPLICATION
            self.ok = bool(self._notify(NIM_ADD, NIF_MESSAGE | NIF_ICON | NIF_TIP))
        finally:
            self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _wndproc(self, hwnd, msg, wp, lp):
        try:
            if msg == WM_TRAY:
                ev = lp & 0xFFFF
                if ev in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self.on_command("show")
                elif ev in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._popup()
                return 0
            if msg == WM_SHOW_APP:
                self.on_command("show")
                return 0
            if msg == WM_QUIT_APP:
                self.on_command("force_quit")
                return 0
            if msg == getattr(self, "_taskbar_created", -1):  # Explorer restarted: re-add the icon
                self._notify(NIM_ADD, NIF_MESSAGE | NIF_ICON | NIF_TIP)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            pass
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def _popup(self):
        menu = user32.CreatePopupMenu()
        cmds = {}
        for i, item in enumerate(self.get_menu(), start=1):
            if item is None:
                user32.AppendMenuW(menu, 0x800, 0, None)  # MF_SEPARATOR
            else:
                label, cmd, enabled = item
                user32.AppendMenuW(menu, 0 if enabled else 0x1, i, label)  # MF_STRING / MF_GRAYED
                cmds[i] = cmd if enabled else None
        first = next((i for i, c in cmds.items() if c), None)
        if first:
            user32.SetMenuDefaultItem(menu, first, 0)
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)  # required so the menu closes when you click elsewhere
        choice = user32.TrackPopupMenu(menu, 0x0100 | 0x0002 | 0x0020, pt.x, pt.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, 0, 0, 0)  # WM_NULL
        user32.DestroyMenu(menu)
        if choice in cmds and cmds[choice]:
            self.on_command(cmds[choice])

    # -- api (any thread)
    def _notify(self, action, flags, info=None):
        n = NOTIFYICONDATAW()
        n.cbSize = ctypes.sizeof(n)
        n.hWnd, n.uID, n.uFlags = self.hwnd, 1, flags
        n.uCallbackMessage, n.hIcon, n.szTip = WM_TRAY, self.hicon, self.tip[:127]
        if info:
            n.szInfoTitle, n.szInfo, n.dwInfoFlags = info[0][:63], info[1][:255], 0x1  # NIIF_INFO
        return shell32.Shell_NotifyIconW(action, ctypes.byref(n))

    def set_tip(self, text):
        self.tip = text
        if self.ok:
            self._notify(NIM_MODIFY, NIF_TIP)

    def balloon(self, title, text):
        if self.ok:
            self._notify(NIM_MODIFY, NIF_INFO, (title, text))

    def remove(self):
        if self.hwnd:
            if self.ok:
                self._notify(NIM_DELETE, 0)
            self.ok = False
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)


def signal_running_instance():
    """Ask an already-running LocalDevX to show its window. True if one was found."""
    hwnd = user32.FindWindowW(CLASS_NAME, None)
    if not hwnd:
        return False
    user32.AllowSetForegroundWindow(0xFFFFFFFF)  # ASFW_ANY
    return bool(user32.PostMessageW(hwnd, WM_SHOW_APP, 0, 0))


def quit_running_instance(timeout=15):
    """Ask a running LocalDevX to exit (used by the installer before upgrading/uninstalling).
    Waits until it has gone. Returns True if nothing is running any more."""
    import time
    hwnd = user32.FindWindowW(CLASS_NAME, None)
    if not hwnd:
        return True
    user32.PostMessageW(hwnd, WM_QUIT_APP, 0, 0)
    end = time.time() + timeout
    while time.time() < end:
        if not user32.FindWindowW(CLASS_NAME, None):
            time.sleep(0.5)  # let the process finish exiting
            return True
        time.sleep(0.2)
    return False
