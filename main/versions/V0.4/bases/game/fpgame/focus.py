"""Focus gate: is the game window the one the player is looking at?

Fails CLOSED: if the foreground window cannot be determined (missing tool, error, Wayland, ...), the gate
reports "not focused" and nothing is recorded.

Two ways to feed it:
  * the game tells us      gate = FocusGate();  gate.set_focused(True / False)   (e.g. from the engine's focus event)
  * we look at the desktop gate = FocusGate(WindowMatch(titles=["My Game"], processes=["mygame.exe"], pids=[...]))

Window matching backends (standard library only): Windows (ctypes), macOS (osascript), Linux/X11 (xdotool or xprop).
"""
import os
import subprocess
import sys
import time


class FocusGate:
    def __init__(self, provider=None, min_interval=None):
        self.provider = provider                  # None = manual: the host calls set_focused()
        self._manual = False
        self._value = False
        self._last = 0.0
        self.min_interval = min_interval if min_interval is not None else getattr(provider, "interval", 0.25)
        self.error = None                         # last problem reading the foreground window, for display

    def set_focused(self, focused):
        self._manual = bool(focused)

    def poll(self):
        """-> True while the game window has focus. Cheap to call often; the provider runs at most every min_interval."""
        if self.provider is None:
            self._value = self._manual
            return self._value
        now = time.monotonic()
        if now - self._last >= self.min_interval:
            self._last = now
            try:
                self._value = bool(self.provider())
                self.error = None
            except Exception as e:                # fail closed
                self._value = False
                self.error = f"{type(e).__name__}: {e}"
        return self._value


class WindowMatch:
    """True while the foreground window matches ANY given title fragment, process name or pid."""

    def __init__(self, titles=(), processes=(), pids=()):
        self.titles = [t.lower() for t in titles if t]
        self.processes = [p.lower() for p in processes if p]
        self.pids = {int(p) for p in pids}
        self.backend = _backend()
        self.interval = getattr(self.backend, "interval", 0.25)
        if not (self.titles or self.processes or self.pids):
            raise ValueError("WindowMatch needs at least one of titles / processes / pids")

    def __call__(self):
        title, proc, pid = self.backend()
        if pid is not None and pid in self.pids:
            return True
        if proc and _stem(proc) in {_stem(p) for p in self.processes}:
            return True
        return bool(title) and any(t in title.lower() for t in self.titles)


def _stem(name):
    name = name.lower()
    return name[:-4] if name.endswith(".exe") else name


def foreground():
    """-> (title, process name, pid) of the window in front right now (for tuning: runner --probe)."""
    return _backend()()


# ---- platform backends: each returns (title, process_name, pid); unknown parts are ""/None ----------
def _backend():
    if sys.platform.startswith("win"):
        return _win
    if sys.platform == "darwin":
        return _mac
    return _x11


def _win():
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "", "", None
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    title = buf.value
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    proc = ""
    h = kernel32.OpenProcess(0x1000, False, pid.value)          # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        try:
            n = wintypes.DWORD(1024)
            path = ctypes.create_unicode_buffer(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, path, ctypes.byref(n)):
                proc = os.path.basename(path.value)
        finally:
            kernel32.CloseHandle(h)
    return title, proc, pid.value or None


_win.interval = 0.2


def _run(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=2).stdout.strip()


def _mac():
    out = _run("osascript", "-e",
               'tell application "System Events" to get {name, unix id} of first application process whose frontmost is true')
    name, _, pid = out.rpartition(",")
    return "", name.strip(), int(pid) if pid.strip().isdigit() else None


_mac.interval = 0.5


def _x11():
    try:                                                          # xdotool: the simplest
        pid = _run("xdotool", "getactivewindow", "getwindowpid")
        title = _run("xdotool", "getactivewindow", "getwindowname")
        pid = int(pid) if pid.isdigit() else None
    except FileNotFoundError:                                     # fall back to xprop
        root = _run("xprop", "-root", "_NET_ACTIVE_WINDOW")
        wid = root.split()[-1] if "window id" in root else ""
        if not wid.startswith("0x") or wid == "0x0":
            return "", "", None
        pid_s = _run("xprop", "-id", wid, "_NET_WM_PID").split("=")[-1].strip()
        name = _run("xprop", "-id", wid, "_NET_WM_NAME")
        title = name.split("=", 1)[-1].strip().strip('"') if "=" in name else ""
        pid = int(pid_s) if pid_s.isdigit() else None
    proc = ""
    if pid:
        try:
            with open(f"/proc/{pid}/comm", encoding="utf-8") as fh:
                proc = fh.read().strip()
        except OSError:
            pass
    return title, proc, pid


_x11.interval = 0.5
