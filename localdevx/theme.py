"""Light / dark color themes for the Tkinter UI.

Uses ttk's 'clam' theme because the native Windows 'vista' theme ignores most color options.
"""
import ctypes
from tkinter import ttk

PALETTES = {
    "light": dict(
        bg="#f6f5f2", sidebar="#ecebe6", surface="#ffffff", text="#1d2025", muted="#6b6f76",
        border="#d9d6cf", accent="#0f766e", accent_hover="#115e59", accent_fg="#ffffff",
        select="#cdeae6", select_fg="#0b3d39", ok="#15803d", bad="#c2410c",
        button="#ffffff", button_hover="#efede8", log_bg="#1f2328", log_fg="#d8dbe0",
    ),
    "dark": dict(
        bg="#17191d", sidebar="#1e2126", surface="#24282e", text="#e7e9ec", muted="#959aa3",
        border="#353a42", accent="#2dd4bf", accent_hover="#5eead4", accent_fg="#062a26",
        select="#1f4a45", select_fg="#e7fffb", ok="#4ade80", bad="#fb923c",
        button="#2a2e35", button_hover="#343941", log_bg="#0f1114", log_fg="#c9cdd3",
    ),
}

FONT = ("Segoe UI", 10)
FONT_SEMI = ("Segoe UI Semibold", 10)


def system_theme():
    """'dark' if Windows apps are set to dark mode, else 'light'."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
    except OSError:
        return "light"


def resolve(name):
    return name if name in PALETTES else system_theme()


def apply_style(root, name):
    """Configure every ttk style for palette `name`. Returns the palette dict."""
    c = PALETTES[name]
    s = ttk.Style(root)
    s.theme_use("clam")
    # NB: no root.option_add("*Font") - it would override the fonts set on ttk styles below.
    root.option_add("*TCombobox*Listbox.font", FONT)

    s.configure(".", background=c["bg"], foreground=c["text"], bordercolor=c["border"],
                lightcolor=c["bg"], darkcolor=c["bg"], troughcolor=c["sidebar"], focuscolor=c["accent"],
                selectbackground=c["select"], selectforeground=c["select_fg"],
                fieldbackground=c["surface"], insertcolor=c["text"], font=FONT)

    s.configure("TFrame", background=c["bg"])
    s.configure("Side.TFrame", background=c["sidebar"])
    s.configure("TLabel", background=c["bg"], foreground=c["text"])
    s.configure("Muted.TLabel", background=c["bg"], foreground=c["muted"], font=("Segoe UI", 9))
    s.configure("Title.TLabel", background=c["bg"], foreground=c["text"], font=("Segoe UI Semibold", 16))
    s.configure("Section.TLabel", background=c["bg"], foreground=c["text"], font=FONT_SEMI)
    s.configure("Side.TLabel", background=c["sidebar"], foreground=c["muted"], font=("Segoe UI Semibold", 9))
    s.configure("Link.TLabel", background=c["bg"], foreground=c["accent"], font=("Segoe UI", 9, "underline"))
    s.configure("Bad.TLabel", background=c["bg"], foreground=c["bad"], font=FONT_SEMI)

    # buttons
    s.configure("TButton", background=c["button"], foreground=c["text"], bordercolor=c["border"],
                lightcolor=c["button"], darkcolor=c["button"], padding=(14, 7), relief="flat", width=-7,
                focusthickness=0, focuscolor=c["button"])
    s.map("TButton",
          background=[("disabled", c["bg"]), ("pressed", c["border"]), ("active", c["button_hover"])],
          lightcolor=[("pressed", c["border"]), ("active", c["button_hover"])],
          darkcolor=[("pressed", c["border"]), ("active", c["button_hover"])],
          foreground=[("disabled", c["muted"])],
          focuscolor=[("active", c["button_hover"])])
    s.configure("Accent.TButton", background=c["accent"], foreground=c["accent_fg"], bordercolor=c["accent"],
                lightcolor=c["accent"], darkcolor=c["accent"], focuscolor=c["accent"], font=FONT_SEMI)
    s.map("Accent.TButton",
          background=[("disabled", c["border"]), ("pressed", c["accent_hover"]), ("active", c["accent_hover"])],
          lightcolor=[("active", c["accent_hover"])], darkcolor=[("active", c["accent_hover"])],
          bordercolor=[("active", c["accent_hover"])], focuscolor=[("active", c["accent_hover"])],
          foreground=[("disabled", c["muted"])])

    # input fields
    field = dict(fieldbackground=c["surface"], foreground=c["text"], bordercolor=c["border"],
                 lightcolor=c["surface"], darkcolor=c["surface"], insertcolor=c["text"], padding=6,
                 background=c["button"], arrowcolor=c["muted"])
    field_map = dict(bordercolor=[("focus", c["accent"])], lightcolor=[("focus", c["surface"])],
                     fieldbackground=[("readonly", c["sidebar"]), ("disabled", c["bg"])],
                     foreground=[("disabled", c["muted"])], arrowcolor=[("disabled", c["border"])])
    for st in ("TEntry", "TSpinbox", "TCombobox"):
        s.configure(st, **field)
        s.map(st, **field_map)
    s.map("TCombobox", fieldbackground=[("readonly", c["surface"]), ("disabled", c["bg"])],
          selectbackground=[("readonly", c["surface"])], selectforeground=[("readonly", c["text"])])
    root.option_add("*TCombobox*Listbox.background", c["surface"])
    root.option_add("*TCombobox*Listbox.foreground", c["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", c["select"])
    root.option_add("*TCombobox*Listbox.selectForeground", c["select_fg"])

    s.configure("TCheckbutton", background=c["bg"], foreground=c["text"], padding=(0, 4),
                indicatorbackground=c["surface"], indicatorforeground=c["accent_fg"],
                upperbordercolor=c["border"], lowerbordercolor=c["border"], focuscolor=c["bg"])
    s.map("TCheckbutton", background=[("active", c["bg"])],
          indicatorbackground=[("disabled", c["bg"]), ("selected", c["accent"])],
          foreground=[("disabled", c["muted"])])

    # site list
    s.configure("Treeview", background=c["sidebar"], fieldbackground=c["sidebar"], foreground=c["text"],
                rowheight=32, bordercolor=c["sidebar"], lightcolor=c["sidebar"], darkcolor=c["sidebar"],
                font=FONT)
    s.map("Treeview", background=[("selected", c["select"])], foreground=[("selected", c["select_fg"])])
    s.configure("Treeview.Heading", background=c["sidebar"], foreground=c["muted"], relief="flat",
                bordercolor=c["sidebar"], lightcolor=c["sidebar"], darkcolor=c["sidebar"],
                font=("Segoe UI Semibold", 9), padding=(6, 4))
    s.map("Treeview.Heading", background=[("active", c["sidebar"])])

    # tabs
    s.configure("TNotebook", background=c["bg"], bordercolor=c["border"], lightcolor=c["bg"],
                darkcolor=c["bg"], tabmargins=(0, 4, 0, 0))
    s.configure("TNotebook.Tab", background=c["sidebar"], foreground=c["muted"], padding=(18, 8),
                bordercolor=c["border"], lightcolor=c["sidebar"], darkcolor=c["sidebar"], focuscolor=c["bg"])
    s.map("TNotebook.Tab", background=[("selected", c["bg"]), ("active", c["button_hover"])],
          foreground=[("selected", c["accent"]), ("active", c["text"])],
          lightcolor=[("selected", c["bg"])], darkcolor=[("selected", c["bg"])],
          focuscolor=[("selected", c["bg"])], expand=[("selected", (0, 2, 0, 0))])

    s.configure("TPanedwindow", background=c["bg"])
    s.configure("Sash", sashthickness=10, gripcount=0, background=c["bg"], lightcolor=c["bg"],
                darkcolor=c["bg"], bordercolor=c["bg"])
    s.configure("TSeparator", background=c["border"])
    for sb in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        s.configure(sb, background=c["button"], troughcolor=c["bg"], bordercolor=c["bg"],
                    lightcolor=c["button"], darkcolor=c["button"], arrowcolor=c["muted"], gripcount=0)
        s.map(sb, background=[("active", c["button_hover"])])
    return c


def style_text(widget, c, kind="field"):
    """Color a classic tk.Text (ttk styles don't reach it)."""
    if kind == "log":
        widget.config(bg=c["log_bg"], fg=c["log_fg"], insertbackground=c["log_fg"],
                      selectbackground=c["select"], selectforeground=c["select_fg"],
                      relief="flat", bd=0, highlightthickness=0, padx=12, pady=10)
    else:
        widget.config(bg=c["surface"], fg=c["text"], insertbackground=c["text"],
                      selectbackground=c["select"], selectforeground=c["select_fg"],
                      relief="flat", bd=0, highlightthickness=1, highlightbackground=c["border"],
                      highlightcolor=c["accent"], padx=10, pady=8)


def set_titlebar(win, dark):
    """Dark / light window title bar on Windows 10 1809+ / 11 (no-op elsewhere)."""
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        val = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new id, then pre-20H1 id)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
        # force the non-client area to repaint: SWP_NOMOVE|NOSIZE|NOZORDER|NOACTIVATE|FRAMECHANGED
        ctypes.windll.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
    except Exception:
        pass


def style_toplevel(win, c, dark):
    win.configure(bg=c["bg"])
    set_titlebar(win, dark)

