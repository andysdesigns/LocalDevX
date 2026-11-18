"""Start-with-Windows (Task Scheduler) and single-instance handling.

LocalDevX needs Administrator rights. Windows silently skips elevated programs in the
Startup folder / Run key, so we register a logon task with "Run with highest privileges"
instead. It starts the app minimized to the tray with no UAC prompt.
"""
import ctypes
import os
import sys
import tempfile
from ctypes import wintypes as wt
from xml.sax.saxutils import escape

from . import APP_AUTHOR, APP_TITLE, APP_VERSION
from .apache import run

TASK_NAME = "LocalDevX"
MUTEX_NAME = "LocalDevX-SingleInstance"


def launch_command():
    """(program, arguments, working dir) that starts the app minimized to the tray."""
    if getattr(sys, "frozen", False):  # PyInstaller build
        return sys.executable, "--minimized", os.path.dirname(sys.executable)
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        exe = exe[:-10] + "pythonw.exe"
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return exe, f'"{os.path.join(root, "localdevx.pyw")}" --minimized', root


def _task_xml():
    user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".lstrip("\\")
    exe, args, wd = launch_command()
    e = escape
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>{e(APP_AUTHOR)}</Author>
    <Version>{e(APP_VERSION)}</Version>
    <Description>Starts {e(APP_TITLE)} minimized to the system tray when you sign in.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{e(user)}</UserId>
      <Delay>PT10S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{e(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>5</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{e(exe)}</Command>
      <Arguments>{e(args)}</Arguments>
      <WorkingDirectory>{e(wd)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def is_enabled():
    code, _ = run(["schtasks", "/Query", "/TN", TASK_NAME], timeout=15)
    return code == 0


def enable():
    fd, path = tempfile.mkstemp(suffix=".xml", prefix="localdevx-task-")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-16") as f:
            f.write(_task_xml())
        code, out = run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", path, "/F"], timeout=30)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if code != 0 and "access is denied" in out.lower():
        out += "\nRun LocalDevX as Administrator to change this setting."
    return code == 0, out.strip()


def repair(log=lambda m: None):
    """If Start-with-Windows is on but the task points somewhere else (e.g. the app folder was
    moved or renamed), re-create it so it launches this copy."""
    code, out = run(["schtasks", "/Query", "/TN", TASK_NAME, "/XML"], timeout=15)
    if code != 0:
        return
    exe, args, _ = launch_command()
    if (exe.lower() in out.lower() and args.split('"')[1 if args.startswith('"') else 0].lower() in out.lower()
            and f"<Version>{APP_VERSION}</Version>" in out):
        return
    ok, msg = enable()
    log(f"Updated the Start-with-Windows task for {APP_TITLE}." if ok else msg)


def disable():
    if not is_enabled():
        return True, ""
    code, out = run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], timeout=30)
    return code == 0, out.strip()


# ---------------------------------------------------------------- single instance

def acquire_instance_lock():
    """Return a mutex handle if we're the first instance, or None if LocalDevX is already running."""
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = wt.HANDLE
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
        k32.CloseHandle.argtypes = [wt.HANDLE]
        h = k32.CreateMutexW(None, False, MUTEX_NAME)
        err = ctypes.get_last_error()
        if err == 183:  # ERROR_ALREADY_EXISTS
            # Let go of our handle to the other copy's mutex, or the mutex would outlive that copy
            # and every later launch would think LocalDevX is still running.
            if h:
                k32.CloseHandle(h)
            return None
        if not h and err == 5:  # ERROR_ACCESS_DENIED: an elevated copy owns it
            return None
        return h or True
    except Exception:
        return True
