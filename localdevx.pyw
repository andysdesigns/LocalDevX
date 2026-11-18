"""LocalDevX - launcher. Re-launches itself as Administrator
(needed to edit the hosts file, restart the Apache service and trust certificates).

Created by Andy Christian S. Loyola <acl20032003@gmail.com>
"""
import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


if __name__ == "__main__":
    if not is_admin() and "--no-elevate" not in sys.argv:
        exe = sys.executable
        if exe.lower().endswith("python.exe"):
            exe = exe[:-10] + "pythonw.exe"
        script = os.path.abspath(sys.argv[0]) if not getattr(sys, "frozen", False) else ""
        params = " ".join(f'"{a}"' for a in ([script] if script else []) + sys.argv[1:])
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe if script else sys.executable, params, None, 1)
        if rc > 32:
            sys.exit(0)
        # user declined UAC: continue unelevated (read-only use still works)
    from localdevx.ui import main
    main()
