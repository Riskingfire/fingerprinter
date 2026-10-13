"""Input recorder for the game base.

Unlike the client's recorder there is NO always-on listener and no hotkey: the mouse and keyboard hooks
exist only between attach() and detach(), and the session attaches them only while the game window has
focus. Detaching removes the hooks and throws away everything buffered.
"""
import threading
import time
from collections import deque

from .settings import AFK_MOVE_PX, KEEP

import numpy as np

try:                                   # pynput is needed to record input at all
    from pynput import mouse, keyboard
except Exception as _pynput_error:     # missing package, or no display server on Linux
    mouse = keyboard = None
    PYNPUT_IMPORT_ERROR = f"{type(_pynput_error).__name__}: {_pynput_error}"
else:
    PYNPUT_IMPORT_ERROR = None


def key_name(k):
    try:
        if hasattr(k, "char") and k.char:
            return k.char.lower()
        if hasattr(k, "name") and k.name:
            return k.name
        if getattr(k, "vk", None) is not None:
            return f"vk{k.vk}"
    except Exception:
        pass
    return str(k)


class GatedRecorder:
    def __init__(self):
        if keyboard is None:
            raise RuntimeError(f"pynput is not available ({PYNPUT_IMPORT_ERROR}). Install with: pip install pynput")
        self.lock = threading.Lock()
        self.moves, self.buttons, self.scrolls, self.keys = deque(), deque(), deque(), deque()
        self.hooked = False
        self._down = set()
        self._last_move = 0.0
        self._mouse = self._kb = None

    def _push(self, dq, item):
        with self.lock:
            dq.append(item)
            lo = item[0] - KEEP
            while dq and dq[0][0] < lo:
                dq.popleft()

    # every callback re-checks `hooked`, so a late event after detach() is dropped
    def _on_press(self, key):
        name = key_name(key)
        if not self.hooked or name in self._down:      # ignore auto-repeat
            return
        self._down.add(name)
        self._push(self.keys, (time.perf_counter(), True, name))

    def _on_release(self, key):
        if not self.hooked:
            return
        name = key_name(key)
        self._down.discard(name)
        self._push(self.keys, (time.perf_counter(), False, name))

    def _on_move(self, x, y):
        if not self.hooked:
            return
        t = time.perf_counter()
        if t - self._last_move < 0.002:
            return
        self._last_move = t
        self._push(self.moves, (t, x, y))

    def _on_click(self, x, y, button, pressed):
        if self.hooked:
            self._push(self.buttons, (time.perf_counter(), getattr(button, "name", str(button)), pressed))

    def _on_scroll(self, x, y, dx, dy):
        if self.hooked:
            self._push(self.scrolls, (time.perf_counter(), abs(dx) + abs(dy)))

    def attach(self):
        """Install the hooks (the game window just got focus)."""
        if self.hooked:
            return
        self._clear()
        self.hooked = True
        self._mouse = mouse.Listener(on_move=self._on_move, on_click=self._on_click, on_scroll=self._on_scroll)
        self._kb = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        for lst in (self._mouse, self._kb):
            lst.daemon = True
            lst.start()

    def detach(self):
        """Remove the hooks and forget everything (the game window lost focus)."""
        self.hooked = False                            # first: late events are ignored from here on
        for lst in (self._mouse, self._kb):
            if lst is not None:
                lst.stop()
        self._mouse = self._kb = None
        self._clear()

    def _clear(self):
        with self.lock:
            for d in (self.moves, self.buttons, self.scrolls, self.keys):
                d.clear()
        self._down.clear()

    def is_idle(self, secs):
        """True if there has been no meaningful input for `secs` seconds (same rule as the client)."""
        if self._down:
            return False
        moves, buttons, scrolls, keys = self.snapshot(secs)
        if scrolls or any(e[1] for e in keys) or any(e[2] for e in buttons):
            return False
        if len(moves) >= 2:
            a = np.asarray(moves, float)
            if np.hypot(np.diff(a[:, 1]), np.diff(a[:, 2])).sum() >= AFK_MOVE_PX:
                return False
        return True

    def snapshot(self, window):
        lo = time.perf_counter() - window
        with self.lock:
            return ([e for e in self.moves if e[0] >= lo],
                    [e for e in self.buttons if e[0] >= lo],
                    [e for e in self.scrolls if e[0] >= lo],
                    [e for e in self.keys if e[0] >= lo])
