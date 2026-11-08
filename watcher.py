"""
Opens Room ID Overlay when TikTok LIVE Studio opens.

Only runs if the user turns it on. It starts with Windows from a per-user Run entry and
checks the list of running programs every few seconds. app.py runs it before its heavy
imports and this file sticks to the standard library, so it stays a few MB in the background.

The Run entry is the on/off switch: once it's removed, the watcher exits by itself.
"""
import ctypes
import os
import socket
import subprocess
import sys
import time
import winreg
from ctypes import wintypes
from pathlib import Path

APP_PORT = 8080  # the app listens here while it's open
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "RoomIDOverlayWatcher"
MUTEX = "Local\\RoomIDOverlayWatcher"
LIVE_STUDIO_EXE = "tiktok live studio.exe"  # the main program, not its launcher or crash helpers
POLL_SECONDS = 3
ERROR_ALREADY_EXISTS = 183
DETACHED = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP


class _ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260)]


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_k32.Process32FirstW.argtypes = _k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.CreateMutexW.restype = wintypes.HANDLE
_k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_INVALID_HANDLE = wintypes.HANDLE(-1).value


def live_studio_running():
    snapshot = _k32.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
    if snapshot in (None, _INVALID_HANDLE):
        return False
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(_ProcessEntry)
        more = _k32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.szExeFile.lower() == LIVE_STUDIO_EXE:
                return True
            more = _k32.Process32NextW(snapshot, ctypes.byref(entry))
        return False
    finally:
        _k32.CloseHandle(snapshot)


def app_open():
    try:
        socket.create_connection(("127.0.0.1", APP_PORT), 0.5).close()
        return True
    except OSError:
        return False


def _app_command():
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, str(Path(__file__).with_name("app.py"))]


def _spawn(extra_args=()):
    subprocess.Popen(_app_command() + list(extra_args), creationflags=DETACHED, close_fds=True)


def is_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except OSError:
        return False


def set_enabled(on):
    """Turn the watcher on (it starts right away and with Windows) or off (it exits by itself)."""
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            command = subprocess.list2cmdline(_app_command() + ["--watch"])
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass
    if on:
        _spawn(["--watch"])


def refresh():
    """Called when the app opens: restarts the watcher after an update and keeps its path current."""
    if is_enabled():
        set_enabled(True)


def _log(message):
    """Windowed programs have no console, so a failed launch would otherwise leave no trace."""
    try:
        folder = Path(os.environ.get("APPDATA", Path.home())) / "RoomIDOverlay"
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / "watcher-log.txt", "a", encoding="utf-8") as f:
            f.write(f"{time.ctime()}  {message}\n")
    except OSError:
        pass


def run():
    mutex = _k32.CreateMutexW(None, False, MUTEX)  # held until this process ends
    if mutex and ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return 0  # one is already watching

    was_open = False
    while is_enabled():
        is_open = live_studio_running()
        # Only when LIVE Studio has just opened, so closing the app mid-stream doesn't reopen it.
        if is_open and not was_open and not app_open():
            try:
                _spawn()
            except OSError as e:
                _log(f"Couldn't open the app: {e}")
        was_open = is_open
        time.sleep(POLL_SECONDS)
    return 0
