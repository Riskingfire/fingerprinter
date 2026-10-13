"""GameFingerprinter: fingerprint the player while (and only while) the game window has focus.

    from fpgame import GameFingerprinter, FocusGate, WindowMatch

    fp = GameFingerprinter("player123",
                           focus=FocusGate(WindowMatch(titles=["My Game"], processes=["mygame.exe"])),
                           server={"url": "https://....onrender.com", "key": "..."})
    fp.start()                       # background thread, no UI
    ...                              # play
    fp.stop()                        # saves and uploads

or let the game say when it is focused:   fp = GameFingerprinter("player123");  fp.set_focused(True/False)

What happens, every ~0.25 s:
  focused  -> mouse/keyboard hooks are installed and a 15 s window is analysed every 7.5 s
  not focused -> hooks are REMOVED and everything buffered is discarded; nothing is observed at all
A window is only kept if the game had focus for the whole of it (and the player was not idle / flagged AFK).
The fingerprint file is the same .fpr the client makes, saved locally and (optionally) uploaded.
"""
import json
import os
import threading
import time
from collections import deque

from . import upload
from .context import dominant_tag
from .features import window_vector
from .focus import FocusGate
from .keybinds import slice_key_stats
from .profile import Profile
from .recorder import GatedRecorder
from .settings import AFK_IDLE_SECS, AFK_MAX_FRAC, APP_DATA_DIR, EXT, KEEP, TRAIN_STEP, WINDOW_SEC
from .store import sanitize


class GameFingerprinter:
    def __init__(self, player, focus=None, server=None, data_dir=None, track_keys=True,
                 autosave_secs=300, upload_secs=900, idle_secs=AFK_IDLE_SECS, log=None):
        self.name = sanitize(str(player))
        if not self.name:
            raise ValueError("player id is empty after removing characters other than letters, digits, - _ and space")
        self.gate = focus if isinstance(focus, FocusGate) else FocusGate()
        self.server = server or {}
        self.data_dir = data_dir or os.path.join(APP_DATA_DIR, "fingerprints")
        self.track_keys, self.autosave_secs, self.upload_secs, self.idle_secs = track_keys, autosave_secs, upload_secs, idle_secs
        self.log = log or (lambda msg: None)
        self.path = os.path.join(self.data_dir, self.name + EXT)
        self._marks = os.path.join(self.data_dir, "uploaded.json")       # {name: 'updated' we last uploaded}
        self.rec = None
        self.profile = None
        self._thread = self._uploader = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        # context the host game may provide
        self._tag, self._afk_hint = "", False
        self._reset_session_state()
        self.new_windows = self.skipped = self.afk_skipped = 0
        self.last_save = self.last_upload = 0.0
        self.last_upload_msg = ""
        self._dirty = False
        self._unsaved_secs = 0.0

    # ---- what the host game can call ----------------------------------------------------------
    def set_focused(self, focused):
        """Manual focus (only used when no window matcher was given)."""
        self.gate.set_focused(focused)

    def set_context(self, tag=None, afk=None):
        """Optional: tag the current game state (e.g. "mode=ranked") and/or mark non-play time (menus, spectating,
        dead) as AFK so it is left out. Windows holding a tag for 80% of their length carry it."""
        if tag is not None:
            self._tag = str(tag)
        if afk is not None:
            self._afk_hint = bool(afk)

    def status(self):
        return {"focused": self.gate.poll(), "recording": bool(self.rec and self.rec.hooked), "player": self.name,
                "windows": len(self.profile.windows) if self.profile else 0, "new_windows": self.new_windows,
                "low_activity": self.skipped, "afk_skipped": self.afk_skipped, "last_save": self.last_save,
                "last_upload": self.last_upload_msg, "focus_error": self.gate.error}

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        os.makedirs(self.data_dir, exist_ok=True)
        self.profile = self._load()
        self.rec = GatedRecorder()                    # creates no hooks yet
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="fpgame", daemon=True)
        self._thread.start()
        self.log(f"fingerprinting '{self.name}' - recording only while the game window has focus")

    def stop(self, final_upload_wait=20.0):
        """Remove the hooks, save, and try one last upload (waits up to final_upload_wait seconds)."""
        self._stop.set()
        if self._thread:
            self._thread.join(5)
        if self.rec:
            self.rec.detach()
        self._save()
        self._upload(force=True)
        if self._uploader:
            self._uploader.join(final_upload_wait)

    # ---- internals ----------------------------------------------------------------------------
    def _reset_session_state(self):
        self.focus_since = None
        self.next_eval = 0.0
        self._key_t = 0.0
        self._last_sample = 0.0
        self.afk_samples = deque(maxlen=40)
        self.ctx_samples = deque(maxlen=60)

    def _load(self):
        if os.path.exists(self.path):
            try:
                return Profile.load(self.path)
            except Exception as e:                    # unreadable: keep it aside, start fresh
                os.replace(self.path, self.path + ".bad")
                self.log(f"could not read the old fingerprint ({e}); starting a new one")
        return Profile(self.name)

    def _run(self):
        last_t = time.perf_counter()
        while not self._stop.is_set():
            try:
                now = time.perf_counter()
                focused = self.gate.poll()
                if focused and not self.rec.hooked:
                    self._on_focus_gain(now)
                elif not focused and self.rec.hooked:
                    self._on_focus_loss()
                if self.rec.hooked:
                    self._unsaved_secs += now - last_t
                    self._sample(now)
                    if now >= self.next_eval and now - self.focus_since >= WINDOW_SEC:
                        self._eval(now)
                last_t = now
                if self._dirty and time.time() - self.last_save >= self.autosave_secs:
                    self._save()
                if self.last_save and time.time() - self.last_upload >= self.upload_secs:
                    self._upload()
            except Exception as e:                     # never kill the loop
                self.log(f"error: {type(e).__name__}: {e}")
            self._stop.wait(0.25)

    def _on_focus_gain(self, now):
        self._reset_session_state()
        self.rec.attach()
        self.focus_since = self._key_t = now
        self.next_eval = now + WINDOW_SEC            # first window only after a full window of focus
        self.log("game focused - recording")

    def _on_focus_loss(self):
        self.rec.detach()                            # hooks removed, buffers dropped
        self.focus_since = None
        self.log("game not focused - recording stopped, hooks removed")
        if self._dirty:
            self._save()

    def _sample(self, now):
        if now - self._last_sample < 1.0:
            return
        self._last_sample = now
        idle = now - self.focus_since >= self.idle_secs and self.rec.is_idle(self.idle_secs)
        self.afk_samples.append((now, bool(idle or self._afk_hint)))
        self.ctx_samples.append((now, self._tag, self._afk_hint))

    def _eval(self, now):
        self.next_eval = now + TRAIN_STEP
        key_lo = max(self._key_t, now - 1.5 * TRAIN_STEP)
        self._key_t = now
        recent = [a for t, a in self.afk_samples if t >= now - WINDOW_SEC]
        if recent and sum(recent) / len(recent) > AFK_MAX_FRAC:
            self.afk_skipped += 1
            return
        vec = window_vector(self.rec.snapshot(WINDOW_SEC), WINDOW_SEC)
        if vec is None:
            self.skipped += 1
            return
        tag = dominant_tag(self.ctx_samples, now, WINDOW_SEC)
        with self._lock:
            self.profile.add_window(vec, tag, None)
            if self.track_keys:
                self.profile.add_key_slice(slice_key_stats(self.rec.snapshot(KEEP)[3], key_lo, now))
        self.new_windows += 1
        self._dirty = True

    def _save(self):
        with self._lock:
            if not (self.profile and self._dirty):
                return
            self.profile.seconds += self._unsaved_secs
            self._unsaved_secs = 0.0
            tmp = self.path + ".tmp"
            size = self.profile.save(tmp)
            os.replace(tmp, self.path)                 # never leave a half-written file
            self._dirty = False
            self.last_save = time.time()
        self.log(f"saved {len(self.profile.windows)} windows ({size / 1024:.0f} KB)")

    # ---- upload (own thread: waking a sleeping server can take a minute) -----------------------------
    def _marks_read(self):
        try:
            with open(self._marks, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def _upload(self, force=False):                 # force is kept for stop(); the check above covers both
        if not upload.configured(self.server) or not os.path.exists(self.path):
            return
        if self._uploader and self._uploader.is_alive():
            return
        updated = _file_updated(self.path)
        if self._marks_read().get(self.name) == updated:    # nothing new since the last successful upload
            self.last_upload = time.time()
            return

        def work():
            try:
                ok, msg = upload.upload_file(self.server, self.name, self.path)
            except Exception as e:
                ok, msg = False, f"{type(e).__name__}: {e}"
            self.last_upload_msg = msg
            if ok:
                marks = self._marks_read()
                marks[self.name] = updated
                with open(self._marks, "w", encoding="utf-8") as fh:
                    json.dump(marks, fh)
            self.log(f"upload: {msg}")
        self.last_upload = time.time()
        self._uploader = threading.Thread(target=work, daemon=True)
        self._uploader.start()


def _file_updated(path):
    import gzip
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return str(json.load(fh).get("updated", ""))
    except Exception:
        return ""
