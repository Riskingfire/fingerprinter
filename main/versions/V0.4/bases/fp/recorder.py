"""Global keyboard / mouse recorder (bounded memory). Only records while a session is active."""
import time
import threading
from collections import deque

from .settings import AFK_DEFAULT_KEY, AFK_MOVE_PX, HOTKEY, KEEP

import numpy as np

try:                                   # pynput is only needed to actually record input
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


# ----------------------------------------------------------------------------
# Input recorder (global listeners, bounded memory)
# ----------------------------------------------------------------------------
class InputRecorder:
    def __init__(self):
        if keyboard is None:
            raise RuntimeError(f"pynput is not available ({PYNPUT_IMPORT_ERROR}). Install with: pip install pynput")
        self.lock = threading.Lock()
        self.moves, self.buttons, self.scrolls, self.keys = deque(), deque(), deque(), deque()
        self.n_moves = 0
        self.n_keys = 0
        self.active = False
        self.hotkey_pressed = False
        self._hk_down = False
        self.afk_key = AFK_DEFAULT_KEY
        self.afk_pressed = False
        self._afk_down = False
        self._down = set()
        self._last_move = 0.0
        self._mouse = None
        # Persistent keyboard listener: only reacts to the hotkey while idle.
        self._kb = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._kb.daemon = True
        self._kb.start()

    def _push(self, dq, item):
        with self.lock:
            dq.append(item)
            lo = item[0] - KEEP
            while dq and dq[0][0] < lo:
                dq.popleft()

    # keyboard
    def _on_press(self, key):
        name = key_name(key)
        if name == HOTKEY:
            if not self._hk_down:
                self._hk_down = True
                self.hotkey_pressed = True
            return
        if name == self.afk_key:
            if not self._afk_down:
                self._afk_down = True
                self.afk_pressed = True
            return
        if not self.active or name in self._down:  # ignore auto-repeat
            return
        self._down.add(name)
        self.n_keys += 1
        self._push(self.keys, (time.perf_counter(), True, name))

    def _on_release(self, key):
        name = key_name(key)
        if name == HOTKEY:
            self._hk_down = False
            return
        if name == self.afk_key:
            self._afk_down = False
            return
        if not self.active:
            return
        self._down.discard(name)
        self._push(self.keys, (time.perf_counter(), False, name))

    # mouse
    def _on_move(self, x, y):
        t = time.perf_counter()
        if t - self._last_move < 0.002:
            return
        self._last_move = t
        self.n_moves += 1
        self._push(self.moves, (t, x, y))

    def _on_click(self, x, y, button, pressed):
        self._push(self.buttons, (time.perf_counter(), getattr(button, "name", str(button)), pressed))

    def _on_scroll(self, x, y, dx, dy):
        self._push(self.scrolls, (time.perf_counter(), abs(dx) + abs(dy)))

    def start(self):
        with self.lock:
            for d in (self.moves, self.buttons, self.scrolls, self.keys):
                d.clear()
        self._down.clear()
        self.n_moves = self.n_keys = 0
        self.active = True
        self._mouse = mouse.Listener(on_move=self._on_move, on_click=self._on_click,
                                     on_scroll=self._on_scroll)
        self._mouse.daemon = True
        self._mouse.start()

    def stop(self):
        self.active = False
        if self._mouse:
            self._mouse.stop()
            self._mouse = None

    def shutdown(self):
        self.stop()
        self._kb.stop()

    def is_idle(self, secs):
        """True if there has been no meaningful input for `secs` seconds.

        Meaningful = a key press (or a key currently held), a click, a scroll, or the
        mouse travelling at least AFK_MOVE_PX in total. Tiny jitters don't count."""
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
