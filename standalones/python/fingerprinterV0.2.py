#!/usr/bin/env python3
"""
Play Fingerprint
================
Learns HOW you play (mouse + keyboard habits) and later recognises you by it.

  pip install pynput numpy
  python play_fingerprint.py

TRAIN  mode : pick (or create) a fingerprint, press Start (or F8), alt-tab to your
              game and play normally for 10+ minutes. Press F8 to stop; the
              fingerprint is saved/updated automatically.
DETECT mode : tick one or more fingerprints, press Start (or F8), play. The tool
              estimates who is at the controls every few seconds.

What is stored (in ./fingerprints/*.fpr, gzip-compressed JSON, typically 50-300 KB,
hard cap 2 MB): statistics of ~15-second windows - mouse speed/acceleration/jerk/
curvature/straightness/direction, click timing, scroll habits, key hold times,
key-to-key timing, rollover, and usage spread over 12 hashed key buckets.
NOT stored: coordinates, key sequences/order, typed text, or any transcript.
Per-key totals ARE stored (key name, press count, hold-time stats; top 60 keys) so keybinds
can be detected - untick "Track individual keys" in the Train tab to skip this. Anything you
type while training counts toward those totals, so train while actually playing.

Nothing is recorded unless a session is running (red RECORDING indicator).
F8 is the only key watched while idle, and it is never recorded.

STYLE CLASS: every fingerprint (and the live player in Detect mode) gets a compact code
like "k05m12a03cr04p01s03ads02ko04cv01ch03". It contains ten independent
classification sections: keyboard, mouse, aim-path, click rate, playstyle, scrolling, ADS,
key overlap, keyboard timing variation, and click-hold duration. The Class guide explains each.

FINGERPRINT ID: the class is only a coarse label (400 combinations). The fingerprint itself
is a point in a continuous space - nothing is binned - and its ID is a readout of that point:
"FP-" + base-32 digits interleaved across 16 dimensions, coarse to fine. Longer ID = finer
resolution (up to 160 characters, the limit of float64). The first characters stay stable
between sessions; the fine tail reflects small, session-to-session differences.

ADS / RIGHT-CLICK (Train + Detect): how often you hold right-click (share of time and presses
per second), tap vs hold style (tap share, median/typical hold, press-to-press rhythm) and how
you fire while aiming (share of left-clicks made while right is held, delay from ADS to first
shot). Shown as e.g. "Heavy ADS (31% of time) - hold style, ~0.9 s per press".

KEYBINDS (Keys tab): per-key share of presses, hold time and how unusual each key is (Movement
= over 10% of presses, usually your move keys / Core / Regular / Occasional / Rare, plus how many other players use it). The core keys give a keybind
signature (movement scheme such as WASD, unusual binds). Keybind similarity (0-100%) compares
two key-usage profiles, weighting keys few other players use; Detect shows it live as "KB" per
fingerprint, and Screen adds a Keybinds column.

SERVER (optional, "Server..." button): sync fingerprints and the ban list with the Render server in
./fingerprint-server. When the app starts it pings the server's /health so a sleeping Render
instance is already awake by the time you need it. Settings live in server_config.json
(next to this script - it holds your API key, so don't share it).

ACCURACY: v2 adds ~30 extra features (stroke peak-speed position, sub-movements, pauses
between strokes, direction reversals, slow-aim jitter, stroke-end-to-click latency, click and
key timing spread, per-key-bucket flight times, rhythm autocorrelation). Matching uses robust
(median/MAD-blended) spreads and, when 3+ fingerprints exist, weights each feature by how well
it separates players (between-player / within-player variance). Training shows a stability
score (first half vs second half of the data). Detect mode also raises heuristic flags for
automation-like input (macro/auto-clicker timing, ruler-straight constant-speed mouse) and for
a sudden player change. Old .fpr files still load - missing features are simply skipped.

BAN-EVASION SCREENING (Screen tab): mark fingerprints as banned, then screen other
accounts against them. Each candidate is scored against every banned fingerprint in both
directions, compared with how similar it is to the rest of the player population, and
flagged NONE / REVIEW / HIGH. In Detect mode a live match to a banned fingerprint is
flagged on screen. Flags are leads for HUMAN REVIEW - similar players, shared PCs and
siblings can look alike, so never auto-ban on a fingerprint match alone.

IN-GAME INTEGRATIONS (optional, "Integrations" tab)
---------------------------------------------------
The tool can also read GAME STATE (which mode / situation the player is in) and use it two ways:
  1. AFK RULES - "while state X is active, count me as AFK unless condition Y holds". Windows
     that overlap such time are skipped in Train (same AFK_MAX_FRAC logic as the manual key and
     idle auto-detect) and scoring is paused in Detect while a rule applies.
  2. CONTEXT TAGS - chosen game values become a short tag (e.g. "mode=a") saved per training
     window. In Detect you may tick "Compare only against windows with the current context tag";
     it is used only once 10+ windows share the tag, otherwise the whole fingerprint is used.

SANCTIONED SOURCES ONLY. Integrations may only consume data a game deliberately provides: its
official game-state / telemetry feature, official APIs or SDKs, documented log files, or data
your own game config exports. This tool never reads process memory, never injects into or hooks
a game process, never scrapes the screen and never touches anti-cheat-protected components - and
it must not be extended to do so.

Source types (each runs on its own daemon thread with bounded memory; a crash, disconnect or
malformed payload only shows up as an error in the tab - input recording and the Tk loop are
never affected):
  http_push  local-only listener on 127.0.0.1 that accepts JSON POSTed by a game's state feature.
             Needs a shared token (header "Authorization: Bearer <t>", "X-Integration-Token", or
             "auth.token" in the JSON) and caps the body size.
  http_poll  polls a local HTTP endpoint (127.0.0.1 / localhost only, no redirects) for JSON.
  log_tail   follows a log/telemetry file and extracts values with configurable regexes.
  udp        local-only UDP listener (127.0.0.1) with a pluggable packet decoder
             ("json", "text_kv", "struct", or one registered by a plugin).
  callback   runs a Python function you register: fn(emit, stop_event).
A value that stops updating goes stale (stale_after seconds) and falls back to its default:
rules do not apply and tags are omitted (a rule can opt into on_unknown="afk" instead).

PRIVACY. Raw game payloads are held only in memory (bounded, flattened, scalars only) and are
never written to disk or uploaded. Only the derived context tag - and optionally a few custom
numeric context values - is stored in a .fpr file (optional "window_tags" / "window_num" fields;
files without them load exactly as before, and files without tags are written exactly as before).

OPT-IN. Nothing listens until you press Start on an integration. With no config file, nothing
exists. Entries need "enabled": true. Listeners bind to 127.0.0.1 only. Reloading the config is
blocked while a Train/Detect session is running.

Config: integrations.json in the app-data folder next to server_config.json. The
"Write example config" button writes the template below (everything disabled; placeholders only).
Optional plugins live in the integration_plugins folder next to it: *.json files are declarative
field mappings {"name", "rename", "drop", "keep_only"}; *.py files must define register(api)
and are loaded only if "plugins_enabled" is true (they run as your own code - only put your own
files there).

integrations.json example:
    {
      "version": 1,
      "plugins_enabled": false,
      "stale_after": 15,
      "integrations": {
        "example_push": {"kind": "http_push", "enabled": false, "port": 3000,
                         "token": "REPLACE_WITH_A_LONG_RANDOM_STRING", "max_body": 65536},
        "example_poll": {"kind": "http_poll", "enabled": false,
                         "url": "http://127.0.0.1:8080/state.json", "interval": 1.0},
        "example_log": {"kind": "log_tail", "enabled": false, "path": "C:/path/to/game_log.txt",
                        "patterns": [{"key": "mode", "regex": "mode=([a-z_]+)"}]},
        "example_udp": {"kind": "udp", "enabled": false, "port": 40000, "decoder": "json"},
        "example_cb": {"kind": "callback", "enabled": false, "callback": "my_callback_name"}
      },
      "afk_rules": [
        {"name": "menu_unless_moving",
         "when": {"ref": "example_push.game.screen", "op": "in", "value": ["menu", "lobby"]},
         "unless": {"ref": "example_push.player.moving", "op": "==", "value": true},
         "on_unknown": "ignore"}
      ],
      "tags": [
        {"name": "mode", "ref": "example_push.game.mode",
         "map": {"mode_a": "a", "mode_b": "b"}, "other": "other"}
      ],
      "numeric": []
    }
    --- end example ---
Refs are "<integration name>.<flattened.json.path>". Condition ops: == != > >= < <= in not_in
contains regex exists missing, combinable with {"all": [...]}, {"any": [...]}, {"not": {...}}.
"""

import os
import sys
import re
import gzip
import json
import math
import time
import hmac
import shutil
import socket
import struct
import types
import zlib
import threading
import queue
import importlib.util
import http.server
import urllib.request
import urllib.error
import urllib.parse
import warnings
from collections import deque, Counter
from datetime import datetime

PSUTIL_IMPORT_ERROR = None
try:
    import psutil  # optional: live per-process CPU/RAM metrics
except Exception as _psutil_error:
    psutil = None
    PSUTIL_IMPORT_ERROR = f"{type(_psutil_error).__name__}: {_psutil_error}"

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

try:
    import numpy as np
    from pynput import mouse, keyboard
except ImportError as _e:  # friendly message instead of a traceback
    msg = f"Missing dependency: {_e.name}\n\nInstall with:\n    pip install pynput numpy"
    print(msg)
    try:
        _r = tk.Tk(); _r.withdraw(); messagebox.showerror("Missing dependency", msg)
    except Exception:
        pass
    sys.exit(1)

# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------
WINDOW_SEC = 15.0        # length of one analysed window
TRAIN_STEP = 7.5         # a new training window every N seconds (windows overlap)
DETECT_STEP = 3.0        # detection refresh interval
TRAIN_WARMUP = 20.0      # skip the first seconds (alt-tabbing to the game)
DETECT_WARMUP = 10.0
MIN_MOVES = 60           # minimum mouse samples for a window to count
MIN_KEYS = 8             # ...or minimum key presses
MAX_WINDOWS = 800        # windows kept per fingerprint file
MAX_BYTES = 2 * 1024 * 1024
TARGET_WINDOWS = 80      # "good" amount of training (~10 min)
N_BUCKETS = 12
GRID_DT = 0.02           # mouse resample step (50 Hz)
HOTKEY = "f8"
AFK_DEFAULT_KEY = "num_lock"   # press to mark yourself AFK / back (training only)
AFK_KEYS = ["num_lock", "scroll_lock", "pause", "caps_lock", "insert", "f9", "f10", "f11"]
AFK_IDLE_SECS = 8              # no meaningful input for this long = auto-detected AFK
AFK_MOVE_PX = 120              # less total mouse travel than this counts as "not doing anything"
AFK_MAX_FRAC = 0.15            # skip a training window if more than 15% of it was AFK
KEEP = WINDOW_SEC + 5
# Store persistent user data outside the script / PyInstaller temporary extraction folder.
# Windows path: %USERPROFILE%\AppData\fingerprint
APP_DATA_DIR = os.path.join(os.path.expanduser("~"), "AppData", "fingerprint")
PROFILE_DIR = os.path.join(APP_DATA_DIR, "fingerprints")
EXT = ".fpr"
BAN_FILE = os.path.join(PROFILE_DIR, "banlist.json")
NEW_LABEL = "+ New fingerprint"
NONE_LABEL = "(none)"

# In-game integrations / context tags (all optional; see the module docstring)
TAG_MIN_WINDOWS = 10         # tag-restricted matching needs at least this many windows with the tag
TAG_MAX_LEN = 64             # longest stored context tag
TAG_MIN_SHARE = 0.8          # a window gets a tag only if one tag held for >= 80% of it
NUM_MAX = 8                  # custom numeric context values kept per window
INTEG_CONFIG_FILE = os.path.join(APP_DATA_DIR, "integrations.json")
INTEG_PLUGIN_DIR = os.path.join(APP_DATA_DIR, "integration_plugins")
INTEG_MAX_CFG_BYTES = 256 * 1024
INTEG_MAX_SOURCES = 16
INTEG_STALE_DEFAULT = 15.0   # seconds without an update before a value falls back to its default
INTEG_BODY_DEFAULT = 64 * 1024
INTEG_BODY_HARD_MAX = 1024 * 1024
INTEG_MAX_KEYS = 64          # flattened values kept per integration
INTEG_MAX_KEY_LEN = 64
INTEG_MAX_STR = 128
INTEG_MAX_DEPTH = 6
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

# Per-key tracking / keybind detection
KEY_MAX = 60                 # keys kept per fingerprint file
CORE_SHARE = 0.04            # a key with >= 4% of presses is part of the keybind signature
RARE_SHARE = 0.01            # "used" threshold when counting how many players use a key
MOVE_SHARE = 0.10            # a key with MORE than 10% of presses is classed "Movement"
KEY_CLASSES = [(0.05, "Core"), (0.02, "Regular"), (0.005, "Occasional"), (0.0, "Rare")]
SCHEMES = [("WASD", ("w", "a", "s", "d")), ("Arrow keys", ("up", "down", "left", "right")),
           ("ESDF", ("e", "s", "d", "f")), ("IJKL", ("i", "j", "k", "l")),
           ("TFGH", ("t", "f", "g", "h"))]
COMMON_BINDS = set("wasdqerfcvxzg1234567") | {"space", "shift", "shift_l", "shift_r", "ctrl", "ctrl_l",
                                              "ctrl_r", "tab", "alt", "alt_l", "esc", "enter", "caps_lock", "up", "down", "left", "right"}

# Human-readable class code: ten independent behavior sections (k/m/a/cr/p/s/ads/ko/cv/ch).
# class number = pace_level * 5 + style_level + 1   (4 pace levels x 5 style levels = 20)
# The cut points are sensible estimates, not calibrated on a population - tune freely.
KB_PACE_NAMES = ["Slow", "Steady", "Quick", "Rapid"]
KB_PACE_CUTS = [1.0, 2.5, 4.5]               # key presses per second
KB_STYLE_NAMES = ["Staccato", "Crisp", "Balanced", "Deliberate", "Sustained"]
KB_STYLE_CUTS = [0.08, 0.13, 0.20, 0.35]     # median key-hold time, seconds
MS_PACE_NAMES = ["Slow", "Moderate", "Fast", "Blazing"]
MS_PACE_CUTS = [600, 1200, 2200]             # mean pointer speed while moving, px/s
MS_STYLE_NAMES = ["Wandering", "Curvy", "Balanced", "Direct", "Laser"]
MS_STYLE_CUTS = [0.55, 0.70, 0.82, 0.91]     # stroke straightness, 0-1

# ----------------------------------------------------------------------------
# Feature definitions
# ----------------------------------------------------------------------------
MOUSE_F = (["m_move_ratio", "m_speed_mean", "m_speed_std", "m_speed_p50", "m_speed_p90",
            "m_speed_p99", "m_speed_cv", "m_acc_mean", "m_acc_p90", "m_jerk_mean",
            "m_turn_mean", "m_turn_p90", "m_straight", "m_stroke_len", "m_stroke_dur",
            "m_stroke_rate", "m_horiz_ratio"] + [f"m_dir{i}" for i in range(8)])
CLICK_F = ["c_rate", "c_hold_mean", "c_hold_std", "c_int_mean", "c_int_std",
           "c_fast_ratio", "c_left_ratio"]
SCROLL_F = ["s_rate", "s_mag"]
KEY_F = (["k_rate", "k_hold_mean", "k_hold_std", "k_hold_med", "k_hold_p90",
          "k_flight_mean", "k_flight_std", "k_flight_med", "k_overlap", "k_unique",
          "k_entropy"]
         + [f"k_hold_b{i}" for i in range(N_BUCKETS)]
         + [f"k_freq_b{i}" for i in range(N_BUCKETS)])
FEATURE_NAMES_V1 = MOUSE_F + CLICK_F + SCROLL_F + KEY_F      # original set (keeps IDs stable)
EXTRA_F = (["m_peak_pos", "m_peak_speed", "m_submoves", "m_stroke_len_cv", "m_pause_mean",
            "m_pause_cv", "m_reversal_rate", "m_jitter", "m_click_lat",
            "c_hold_med", "c_int_med", "c_int_cv",
            "k_hold_cv", "k_hold_p10", "k_flight_cv", "k_flight_p10", "k_flight_p90",
            "k_flight_ac1", "k_burst_ratio", "k_pause_ratio",
            "r_rate", "r_time_ratio", "r_hold_mean", "r_hold_med", "r_hold_std", "r_hold_p90",
            "r_tap_ratio", "r_hold_ratio", "r_int_med", "r_int_cv", "r_fire_ratio", "r_fire_lat"]
           + [f"k_flight_b{i}" for i in range(N_BUCKETS)])
FEATURE_NAMES = FEATURE_NAMES_V1 + EXTRA_F
NAN = float("nan")


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


# ----------------------------------------------------------------------------
# Feature extraction
# ----------------------------------------------------------------------------
def _window_end(times, dur):
    """End time of the analysed window: 'now' for live data, else the last event time."""
    now = time.perf_counter()
    if times and abs(now - max(times)) < dur + 5:
        return now
    return max(times) if times else now


def extract_features(moves, buttons, scrolls, keys, dur):
    f = dict.fromkeys(FEATURE_NAMES, NAN)
    stroke_ends = []

    # ---- mouse movement -----------------------------------------------------
    if len(moves) >= 20:
        a = np.asarray(moves, float)
        t, x, y = a[:, 0], a[:, 1], a[:, 2]
        grid = np.arange(t[0], t[-1], GRID_DT)
        if len(grid) >= 20:
            gx, gy = np.interp(grid, t, x), np.interp(grid, t, y)
            idx = np.clip(np.searchsorted(t, grid, side="right"), 1, len(t) - 1)
            still = (t[idx] - t[idx - 1]) > 0.12          # idle gap -> no motion
            dx, dy = np.diff(gx), np.diff(gy)
            dx[still[1:]] = 0
            dy[still[1:]] = 0
            dist = np.hypot(dx, dy)
            speed = dist / GRID_DT
            moving = speed > 30
            f["m_move_ratio"] = float(moving.sum() * GRID_DT / dur)
            if moving.sum() >= 10:
                sp = speed[moving]
                f["m_speed_mean"] = float(sp.mean())
                f["m_speed_std"] = float(sp.std())
                f["m_speed_p50"] = float(np.percentile(sp, 50))
                f["m_speed_p90"] = float(np.percentile(sp, 90))
                f["m_speed_p99"] = float(np.percentile(sp, 99))
                f["m_speed_cv"] = float(sp.std() / max(sp.mean(), 1e-6))

                dv = np.diff(speed) / GRID_DT
                acc = np.abs(dv[moving[1:] & moving[:-1]])
                if len(acc) >= 5:
                    f["m_acc_mean"] = float(acc.mean())
                    f["m_acc_p90"] = float(np.percentile(acc, 90))
                jerk = np.abs(np.diff(dv)) / GRID_DT
                jm = moving[2:] & moving[1:-1] & moving[:-2]
                if jm.sum() >= 5:
                    f["m_jerk_mean"] = float(jerk[jm].mean())

                ang = np.arctan2(dy, dx)
                da = (np.diff(ang) + np.pi) % (2 * np.pi) - np.pi
                tmask = moving[1:] & moving[:-1]
                tm = np.abs(da[tmask])
                if len(tm) >= 5:
                    f["m_turn_mean"] = float(tm.mean())
                    f["m_turn_p90"] = float(np.percentile(tm, 90))
                    f["m_reversal_rate"] = float((tm > 2.4).sum() / dur)
                slow = np.abs(da[tmask & (speed[1:] < 250)])     # slow aiming micro-corrections
                if len(slow) >= 5:
                    f["m_jitter"] = float(slow.mean())

                hist, _ = np.histogram(ang[moving], bins=8, range=(-np.pi, np.pi),
                                       weights=dist[moving])
                if hist.sum() > 0:
                    for i, v in enumerate(hist / hist.sum()):
                        f[f"m_dir{i}"] = float(v)

                ax, ay = np.abs(dx).sum(), np.abs(dy).sum()
                f["m_horiz_ratio"] = float(ax / max(ax + ay, 1e-6))

                # strokes = runs of continuous motion
                m = np.concatenate(([0], moving.astype(np.int8), [0]))
                e = np.diff(m)
                starts, ends = np.where(e == 1)[0], np.where(e == -1)[0]
                straight, lens, durs = [], [], []
                peaks, peakpos, subs, s_list, e_list = [], [], [], [], []
                for s, en in zip(starts, ends):
                    d_s = (en - s) * GRID_DT
                    path = dist[s:en].sum()
                    if d_s < 0.08 or path < 40:
                        continue
                    net = np.hypot(gx[en] - gx[s], gy[en] - gy[s])
                    straight.append(net / path)
                    lens.append(path)
                    durs.append(d_s)
                    seg = speed[s:en]
                    peaks.append(float(seg.max()))
                    peakpos.append(float(seg.argmax()) / max(len(seg) - 1, 1))
                    if len(seg) >= 7:                       # count speed dips = corrective sub-movements
                        sm = np.convolve(seg, np.ones(5) / 5, mode="same")
                        dd = np.diff(sm)
                        mn = (dd[:-1] < 0) & (dd[1:] >= 0)
                        subs.append(float((sm[1:-1][mn] < 0.7 * seg.max()).sum()))
                    s_list.append(s)
                    e_list.append(en)
                if straight:
                    f["m_straight"] = float(np.mean(straight))
                    f["m_stroke_len"] = float(np.mean(lens))
                    f["m_stroke_dur"] = float(np.mean(durs))
                    f["m_stroke_rate"] = len(straight) / dur
                    f["m_peak_speed"] = float(np.mean(peaks))
                    f["m_peak_pos"] = float(np.mean(peakpos))
                    if subs:
                        f["m_submoves"] = float(np.mean(subs))
                    if len(lens) >= 3:
                        f["m_stroke_len_cv"] = float(np.std(lens) / max(np.mean(lens), 1e-6))
                    if len(s_list) >= 4:
                        gaps = np.array([(s_list[i + 1] - e_list[i]) * GRID_DT
                                         for i in range(len(s_list) - 1)])
                        gaps = gaps[(gaps >= 0) & (gaps < 2.0)]
                        if len(gaps) >= 3:
                            f["m_pause_mean"] = float(gaps.mean())
                            f["m_pause_cv"] = float(gaps.std() / max(gaps.mean(), 1e-6))
                    stroke_ends = [float(grid[min(en, len(grid) - 1)]) for en in e_list]

    # ---- clicks ------------------------------------------------------------
    downs, holds, pend, left = [], [], {}, 0
    for t, b, d in buttons:
        if d:
            downs.append(t)
            pend[b] = t
            left += (b == "left")
        elif b in pend:
            holds.append(t - pend.pop(b))
    f["c_rate"] = len(downs) / dur
    if downs:
        f["c_left_ratio"] = left / len(downs)
    holds = [h for h in holds if h < 1.5]
    if len(holds) >= 3:
        f["c_hold_mean"], f["c_hold_std"] = float(np.mean(holds)), float(np.std(holds))
        f["c_hold_med"] = float(np.median(holds))
    if len(downs) >= 4:
        iv = np.diff(downs)
        iv = iv[iv < 3]
        if len(iv) >= 3:
            f["c_int_mean"], f["c_int_std"] = float(iv.mean()), float(iv.std())
            f["c_fast_ratio"] = float((iv < 0.25).mean())
            f["c_int_med"] = float(np.median(iv))
            f["c_int_cv"] = float(iv.std() / max(iv.mean(), 1e-6))
    if stroke_ends and downs:                                # how soon after a stroke ends do you click?
        se = np.asarray(stroke_ends)
        lat = []
        for d_ in downs:
            prev = se[se <= d_]
            if len(prev) and d_ - prev[-1] < 1.0:
                lat.append(d_ - prev[-1])
        if len(lat) >= 3:
            f["m_click_lat"] = float(np.mean(lat))

    # ---- ADS / right-click ---------------------------------------------------
    t_end = _window_end([e[0] for e in buttons] + ([moves[-1][0]] if moves else []), dur)
    t_start = t_end - dur
    r_downs, r_holds, ivs, rp = [], [], [], None
    for t, b, d in sorted(buttons, key=lambda e: e[0]):
        if b != "right":
            continue
        if d:
            if rp is None:
                r_downs.append(t)
                rp = t
        elif rp is not None:
            r_holds.append(t - rp)
            ivs.append((rp, t, True))
            rp = None
        else:                                   # released a hold that began before this window
            ivs.append((t_start, t, False))
    if rp is not None:                          # still held at the end of the window
        ivs.append((rp, t_end, True))
    f["r_rate"] = len(r_downs) / dur
    f["r_time_ratio"] = float(min(1.0, sum(max(0.0, b - a) for a, b, _ in ivs) / dur))
    rh = np.asarray([h for h in r_holds if 0 < h < 10.0])
    if len(rh) >= 3:
        f["r_hold_mean"], f["r_hold_std"] = float(rh.mean()), float(rh.std())
        f["r_hold_med"], f["r_hold_p90"] = float(np.median(rh)), float(np.percentile(rh, 90))
        f["r_tap_ratio"] = float((rh < 0.25).mean())
        f["r_hold_ratio"] = float((rh >= 0.6).mean())
    if len(r_downs) >= 4:
        riv = np.diff(r_downs)
        riv = riv[riv < 5]
        if len(riv) >= 3:
            f["r_int_med"] = float(np.median(riv))
            f["r_int_cv"] = float(riv.std() / max(riv.mean(), 1e-6))
    lefts = [t for t, b, d in buttons if d and b == "left"]
    if ivs and len(lefts) >= 3:                 # how much of your shooting happens while aiming
        f["r_fire_ratio"] = float(sum(any(a <= t <= b for a, b, _ in ivs) for t in lefts) / len(lefts))
    lat = []
    for a, b, known in ivs:
        if known:
            first = [t for t in lefts if a <= t <= b]
            if first and first[0] - a < 5.0:
                lat.append(first[0] - a)
    if len(lat) >= 3:
        f["r_fire_lat"] = float(np.mean(lat))

    # ---- scroll ------------------------------------------------------------
    f["s_rate"] = len(scrolls) / dur
    if scrolls:
        f["s_mag"] = float(np.mean([s[1] for s in scrolls]))

    # ---- keyboard ----------------------------------------------------------
    pend, holds, down_t, names, overlap = {}, [], [], [], 0
    bucket_holds = {i: [] for i in range(N_BUCKETS)}
    for t, d, n in keys:
        if d:
            if pend:
                overlap += 1
            pend[n] = t
            down_t.append(t)
            names.append(n)
        elif n in pend:
            h = t - pend.pop(n)
            if h < 2.0:
                holds.append(h)
                bucket_holds[zlib.crc32(n.encode()) % N_BUCKETS].append(h)
    f["k_rate"] = len(down_t) / dur
    if len(down_t) >= 5:
        f["k_overlap"] = overlap / len(down_t)
        f["k_unique"] = float(len(set(names)))
        cnt = Counter(names)
        p = np.array(list(cnt.values()), float) / len(names)
        f["k_entropy"] = float(-(p * np.log2(p)).sum())
        for i in range(N_BUCKETS):
            f[f"k_freq_b{i}"] = 0.0
        for n, c in cnt.items():
            f[f"k_freq_b{zlib.crc32(n.encode()) % N_BUCKETS}"] += c / len(names)
        fl_all = np.diff(down_t)
        keep = fl_all < 2.0
        fl = fl_all[keep]
        if len(fl) >= 4:
            f["k_flight_mean"], f["k_flight_std"] = float(fl.mean()), float(fl.std())
            f["k_flight_med"] = float(np.median(fl))
            f["k_flight_cv"] = float(fl.std() / max(fl.mean(), 1e-6))
            f["k_flight_p10"], f["k_flight_p90"] = float(np.percentile(fl, 10)), float(np.percentile(fl, 90))
            f["k_burst_ratio"] = float((fl < 0.15).mean())
            f["k_pause_ratio"] = float((fl > 0.8).mean())
            if len(fl) >= 8 and fl[:-1].std() > 0 and fl[1:].std() > 0:
                ac = float(np.corrcoef(fl[:-1], fl[1:])[0, 1])
                if ac == ac:
                    f["k_flight_ac1"] = ac
            fb = {i: [] for i in range(N_BUCKETS)}           # flight time INTO each key bucket
            for j in np.where(keep)[0]:
                fb[zlib.crc32(names[j + 1].encode()) % N_BUCKETS].append(fl_all[j])
            for i, vs in fb.items():
                if len(vs) >= 2:
                    f[f"k_flight_b{i}"] = float(np.mean(vs))
    if len(holds) >= 4:
        h = np.asarray(holds)
        f["k_hold_mean"], f["k_hold_std"] = float(h.mean()), float(h.std())
        f["k_hold_med"], f["k_hold_p90"] = float(np.median(h)), float(np.percentile(h, 90))
        f["k_hold_cv"] = float(h.std() / max(h.mean(), 1e-6))
        f["k_hold_p10"] = float(np.percentile(h, 10))
    for i, hs in bucket_holds.items():
        if len(hs) >= 2:
            f[f"k_hold_b{i}"] = float(np.mean(hs))
    return f


def window_vector(snap, dur):
    moves, buttons, scrolls, keys = snap
    n_down = sum(1 for e in keys if e[1])
    if len(moves) < MIN_MOVES and n_down < MIN_KEYS:
        return None
    f = extract_features(moves, buttons, scrolls, keys, dur)
    vec = np.array([f[n] for n in FEATURE_NAMES], float)
    return vec if np.sum(~np.isnan(vec)) >= 10 else None


def distance(vec, mu, sd, w=None):
    """Mean clipped z-distance over shared features; w = optional per-feature weights."""
    ok = ~np.isnan(vec) & ~np.isnan(mu)
    if ok.sum() < 10:
        return None
    z = np.minimum(np.abs(vec[ok] - mu[ok]) / sd[ok], 4.0)
    if w is None:
        return float(np.mean(z))
    ww = w[ok]
    return float((z * ww).sum() / ww.sum())


# ----------------------------------------------------------------------------
# Human-readable classes (k01-k20 / m01-m20)
# ----------------------------------------------------------------------------
IDX = {n: i for i, n in enumerate(FEATURE_NAMES)}


def _level(v, cuts):
    return int(sum(v > c for c in cuts))


def typical_vector(windows):
    """Robust 'typical' feature vector (median over windows)."""
    W = np.vstack(windows)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        vec = np.nanmedian(W, 0)
    kv = ~np.isnan(W[:, IDX["k_hold_med"]])      # key rate only where keys were used
    if kv.any():
        vec[IDX["k_rate"]] = np.median(W[kv, IDX["k_rate"]])
    return vec


def classify_ads(vec):
    """Human-readable ADS (right-click) habits from a typical feature vector."""
    def g(n):
        return float(vec[IDX[n]])
    tr, rr = g("r_time_ratio"), g("r_rate")
    if tr != tr or rr != rr:
        return {"desc": "no ADS data yet", "short": "-", "usage": None, "style": None}
    usage = ("Rarely" if tr < 0.03 else "Light" if tr < 0.12 else "Moderate" if tr < 0.30 else "Heavy")
    if usage == "Rarely":
        return {"desc": f"rarely uses right-click/ADS ({tr * 100:.0f}% of time, {rr:.2f}/s)",
                "short": "Rarely ADS", "usage": usage, "style": None}
    tap, hm, hr = g("r_tap_ratio"), g("r_hold_med"), g("r_hold_ratio")
    if tap != tap:
        style = "style unknown"
    elif tap >= 0.6:
        style = "tap / quick-peek style"
    elif hm == hm and hm >= 1.5:
        style = "long-hold style"
    elif tap <= 0.25:
        style = "hold style"
    else:
        style = "mixed tap + hold"
    desc = f"{usage} ADS ({tr * 100:.0f}% of time, {rr:.1f} presses/s) - {style}"
    if hm == hm:
        desc += f", ~{hm:.1f} s per press"
    fr, fl = g("r_fire_ratio"), g("r_fire_lat")
    if fr == fr:
        desc += f", {fr * 100:.0f}% of shots while aiming"
    if fl == fl:
        desc += f", first shot {fl * 1000:.0f} ms after ADS"
    return {"desc": desc, "short": f"{usage} - {style}", "usage": usage, "style": style}


AIM_STRAIGHT_CUTS = [0.25, 0.45, 0.65, 0.82]  # mouse path straightness, 0-1
CLICK_RATE_CUTS = [0.5, 1.5, 3.0, 5.0]         # clicks per second


def classify_vector(vec):
    def g(n):
        return float(vec[IDX[n]])

    def level_code(value, cuts, prefix):
        if value != value:
            return prefix + "??"
        return f"{prefix}{_level(value, cuts) + 1:02d}"

    out = {"k": None, "m": None,
           "k_desc": "not enough keyboard data", "m_desc": "not enough mouse data"}
    kr, kh = g("k_rate"), g("k_hold_med")
    if kr == kr and kh == kh:
        p, s = _level(kr, KB_PACE_CUTS), _level(kh, KB_STYLE_CUTS)
        out["k"] = p * 5 + s + 1
        out["k_desc"] = f"{KB_PACE_NAMES[p]} pace, {KB_STYLE_NAMES[s]} key holds"
    ms, st = g("m_speed_mean"), g("m_straight")
    if ms == ms and st == st:
        p, s = _level(ms, MS_PACE_CUTS), _level(st, MS_STYLE_CUTS)
        out["m"] = p * 5 + s + 1
        out["m_desc"] = f"{MS_PACE_NAMES[p]} pointer, {MS_STYLE_NAMES[s]} movement"
    # Ten independent classification sections, each encoded as a short prefix + two digits.
    # A segment is ?? when the recorded telemetry does not contain enough data.
    out["a"] = _level(g("m_straight"), AIM_STRAIGHT_CUTS) + 1 if g("m_straight") == g("m_straight") else None
    out["cr"] = _level(g("c_rate"), CLICK_RATE_CUTS) + 1 if g("c_rate") == g("c_rate") else None
    out["s"] = _level(g("s_rate"), [0.05, 0.25, 0.75, 1.5]) + 1 if g("s_rate") == g("s_rate") else None
    out["ads"] = _level(g("r_time_ratio"), [0.01, 0.08, 0.20, 0.40]) + 1 if g("r_time_ratio") == g("r_time_ratio") else None
    out["ko"] = _level(g("k_overlap"), [0.02, 0.10, 0.25, 0.50]) + 1 if g("k_overlap") == g("k_overlap") else None
    out["cv"] = _level(g("k_flight_cv"), [0.25, 0.50, 1.0, 2.0]) + 1 if g("k_flight_cv") == g("k_flight_cv") else None
    out["ch"] = _level(g("c_hold_med"), [0.05, 0.12, 0.25, 0.50]) + 1 if g("c_hold_med") == g("c_hold_med") else None
    out["ads_desc"] = classify_ads(vec)["desc"]
    out["playstyle"] = classify_playstyle(vec, out)
    playstyle_codes = {
        "ADS-focused": 1,
        "Click-intensive": 2,
        "Keyboard-led": 3,
        "Mouse-led": 4,
        "Balanced keyboard + mouse": 5,
        "High-input hybrid": 6,
        "Precision / direct-aim": 7,
        "Scroll-intensive": 8,
        "Low-intensity / mixed": 9,
    }
    pcode = playstyle_codes.get(out["playstyle"])
    out["p"] = pcode
    out["code"] = ((f"k{out['k']:02d}" if out["k"] else "k??") +
                   (f"m{out['m']:02d}" if out["m"] else "m??") +
                   level_code(g("m_straight"), AIM_STRAIGHT_CUTS, "a") +
                   level_code(g("c_rate"), CLICK_RATE_CUTS, "cr") +
                   (f"p{pcode:02d}" if pcode is not None else "p??") +
                   level_code(g("s_rate"), [0.05, 0.25, 0.75, 1.5], "s") +
                   level_code(g("r_time_ratio"), [0.01, 0.08, 0.20, 0.40], "ads") +
                   level_code(g("k_overlap"), [0.02, 0.10, 0.25, 0.50], "ko") +
                   level_code(g("k_flight_cv"), [0.25, 0.50, 1.0, 2.0], "cv") +
                   level_code(g("c_hold_med"), [0.05, 0.12, 0.25, 0.50], "ch"))
    return out


def classify_playstyle(vec, device_classes=None):
    """Add a readable playstyle class using telemetry already collected by the app.

    These are behavior labels, not claims about physical hardware. The current
    recorder measures keyboard and mouse events only, so it cannot infer gamepad,
    touchscreen, or controller-only play reliably.
    """
    def g(name):
        try:
            value = float(vec[IDX[name]])
            return value if value == value else None
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    kr, ms = g("k_rate"), g("m_speed_mean")
    click, ads, scroll = g("c_rate"), g("r_time_ratio"), g("s_rate")
    straight, stroke = g("m_straight"), g("m_stroke_rate")
    available = sum(v is not None for v in (kr, ms, click, ads, scroll))
    if available < 2:
        return "Not enough data"
    # Prioritize distinctive behaviors, then broad input balance.
    if ads is not None and ads >= 0.28:
        return "ADS-focused"
    if click is not None and click >= 4.0:
        return "Click-intensive"
    if kr is not None and ms is not None and kr > 0 and ms < 600:
        return "Keyboard-led"
    if stroke is not None and stroke >= 2.5 and straight is not None and straight >= 0.82:
        return "Precision / direct-aim"
    if scroll is not None and scroll >= 1.0:
        return "Scroll-intensive"
    if kr is not None and ms is not None:
        if kr >= 3.0 and ms >= 1200:
            return "High-input hybrid"
        if kr >= 1.0 and ms >= 600:
            return "Balanced keyboard + mouse"
        if ms >= 600:
            return "Mouse-led"
        if kr >= 1.0:
            return "Keyboard-led"
    return "Low-intensity / mixed"


# ----------------------------------------------------------------------------
# Continuous fingerprint embedding + unbounded-resolution ID
# ----------------------------------------------------------------------------
EMB_DIM = 16
ID_DEFAULT_LEN = 64
ID_MAX_LEN = 160                      # ~10 base-32 digits per dimension = float64 precision
ID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_SCALE_RULES = [("m_speed_cv", 1.0), ("m_speed", 1000.0), ("m_acc", 20000.0),
                ("m_jerk", 1e6), ("m_stroke_len", 300.0), ("m_stroke_dur", 0.3),
                ("m_stroke_rate", 2.0), ("m_dir", 0.125), ("c_hold", 0.1),
                ("c_int", 0.5), ("k_rate", 2.0), ("k_hold", 0.1), ("k_flight", 0.3),
                ("k_unique", 5.0), ("k_entropy", 2.0), ("k_freq", 0.1)]


def _ref_scale(name):
    for prefix, s in _SCALE_RULES:
        if name.startswith(prefix):
            return s
    return 1.0


SCALES = np.array([_ref_scale(n) for n in FEATURE_NAMES_V1], float)
# Fixed random projection: the same behaviour always maps to the same point.
_PROJ = np.random.RandomState(20240607).normal(size=(EMB_DIM, len(FEATURE_NAMES_V1))) / np.sqrt(len(FEATURE_NAMES_V1))


def embed(vec):
    """Typical feature vector -> point in the continuous unit hypercube (0,1)^16."""
    vec = np.asarray(vec, float)[:len(FEATURE_NAMES_V1)]      # ID uses the original features only
    t = np.log1p(np.abs(np.nan_to_num(vec, nan=0.0)) / SCALES)
    v = np.where(np.isnan(vec), 0.5, t / (1.0 + t))
    return 0.5 + 0.5 * np.tanh(6.0 * (_PROJ @ (v - 0.5)))


def fingerprint_id(emb, length=ID_MAX_LEN):
    """Raw ID string: digit k of every dimension, k = 1, 2, ... (coarse -> fine)."""
    if emb is None:
        return None
    n = max(1, min(length // EMB_DIM, ID_MAX_LEN // EMB_DIM))
    xs = [min(max(float(u), 0.0), 1.0 - 2.0 ** -53) for u in emb]
    out = []
    for _ in range(n):
        for j in range(EMB_DIM):
            xs[j] *= 32.0                 # exact (power of two)
            d = int(xs[j])
            xs[j] -= d
            out.append(ID_ALPHABET[d])
    return "".join(out)


def format_id(raw, length=ID_DEFAULT_LEN):
    if not raw:
        return "(not enough data yet)"
    n = max(1, min(length // EMB_DIM, len(raw) // EMB_DIM))
    s = raw[:n * EMB_DIM]
    return "FP-" + "-".join(s[i:i + 8] for i in range(0, len(s), 8))


# ----------------------------------------------------------------------------
# Fingerprint profile
# ----------------------------------------------------------------------------
def _clean_tag(tag):
    """Context tags are short plain strings; anything else becomes 'untagged' ('')."""
    return tag[:TAG_MAX_LEN] if isinstance(tag, str) else ""


def _clean_nums(x):
    """Optional per-window custom numeric context: {name: finite float}, at most NUM_MAX entries."""
    if not isinstance(x, dict):
        return None
    out = {}
    for k, v in list(x.items())[:NUM_MAX]:
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
            out[str(k)[:32]] = float(v)
    return out or None


class Profile:
    def __init__(self, name):
        self.name = name
        self.created = self.updated = datetime.now().isoformat(timespec="seconds")
        self.seconds = 0.0
        self.windows = []
        self.mu = self.sd = None
        self.ref_mean, self.ref_std = 1.0, 0.3
        self.keys = {}          # key -> [presses, hold_n, hold_sum, hold_sqsum, slices_seen]
        self.key_slices = 0
        self.tags = []          # optional context tag per window ("" = untagged); parallel to windows
        self.nums = []          # optional custom numeric context per window (dict or None)
        self._tag_cache = {}

    def fit(self):
        nf = len(FEATURE_NAMES)
        if not self.windows:
            self.mu, self.sd = np.full(nf, NAN), np.ones(nf)
            return
        W = np.vstack(self.windows)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mu, sd = np.nanmean(W, 0), np.nanstd(W, 0)
            med = np.nanmedian(W, 0)
            mad = 1.4826 * np.nanmedian(np.abs(W - med), 0)       # robust spread
            sd = 0.5 * sd + 0.5 * np.nan_to_num(mad)
        mu[(~np.isnan(W)).sum(0) < 3] = NAN
        self.mu = mu
        self.sd = np.maximum.reduce([np.nan_to_num(sd), 0.1 * np.abs(np.nan_to_num(mu)),
                                     np.full(nf, 1e-3)])
        ds = [d for d in (distance(w, self.mu, self.sd) for w in self.windows) if d is not None]
        if ds:
            self.ref_mean = max(float(np.mean(ds)), 0.25)
            self.ref_std = float(np.std(ds))

    def _aligned(self):
        """(tags, nums) padded / trimmed to exactly one entry per window."""
        n = len(self.windows)
        tg = [_clean_tag(t) for t in self.tags][:n]
        nm = list(self.nums)[:n]
        return tg + [""] * (n - len(tg)), nm + [None] * (n - len(nm))

    def add_window(self, vec, tag="", num=None):
        """Append a training window together with its optional context tag / numeric context."""
        self.tags, self.nums = self._aligned()
        self.windows.append(vec)
        self.tags.append(_clean_tag(tag))
        self.nums.append(_clean_nums(num))
        self._tag_cache.clear()

    def tag_counts(self):
        return Counter(t for t in self._aligned()[0] if t)

    def match_params(self, tag, w=None, min_n=TAG_MIN_WINDOWS):
        """(mu, sd, self_ref, used_tag) used to score a live window.

        With a tag and at least `min_n` windows sharing it, the fingerprint is fitted on only those
        windows; otherwise (no tag, or too few windows) the whole fingerprint is used."""
        if tag:
            idx = [i for i, t in enumerate(self._aligned()[0]) if t == tag]
            if len(idx) >= min_n:
                key = (tag, len(self.windows))
                sub = self._tag_cache.get(key)
                if sub is None:
                    sub = Profile(self.name + "#" + tag)
                    sub.windows = [self.windows[i] for i in idx]
                    sub.fit()
                    self._tag_cache[key] = sub
                return sub.mu, sub.sd, sub.self_ref(w), True
        return self.mu, self.sd, self.self_ref(w), False

    def add_key_slice(self, st):
        """Fold one (non-overlapping) time slice of per-key stats into the profile."""
        self.key_slices += 1
        for n, r in st.items():
            e = self.keys.setdefault(n, [0, 0, 0.0, 0.0, 0])
            e[0] += r[0]
            e[1] += r[1]
            e[2] += r[2]
            e[3] += r[3]
            e[4] += 1

    def keys_for_save(self):
        top = sorted(self.keys.items(), key=lambda kv: -kv[1][0])[:KEY_MAX]
        return {n: [e[0], e[1], round(e[2], 4), round(e[3], 5), e[4]] for n, e in top}

    def self_ref(self, w=None):
        """Typical self-distance of this profile's own windows (optionally feature-weighted)."""
        if w is None:
            return self.ref_mean
        cache = getattr(self, "_ref_cache", None)
        if cache and cache[0] is w:
            return cache[1]
        ds = [d for d in (distance(x, self.mu, self.sd, w) for x in self.windows) if d is not None]
        val = max(float(np.mean(ds)), 0.25) if ds else self.ref_mean
        self._ref_cache = (w, val)
        return val

    def classify(self):
        if not self.windows:
            return classify_vector(np.full(len(FEATURE_NAMES), NAN))
        return classify_vector(typical_vector(self.windows))

    def embedding(self):
        if not self.windows:
            return None
        vec = typical_vector(self.windows)
        if np.sum(~np.isnan(vec)) < 10:
            return None
        return embed(vec)

    def _summary(self):
        out = {}
        if not self.windows:
            return out
        W = np.vstack(self.windows)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for i, n in enumerate(FEATURE_NAMES):
                col = W[:, i][~np.isnan(W[:, i])]
                if len(col) >= 3:
                    out[n] = {"mean": _r(col.mean()), "std": _r(col.std()),
                              "p10": _r(np.percentile(col, 10)),
                              "p50": _r(np.percentile(col, 50)),
                              "p90": _r(np.percentile(col, 90))}
        return out

    def save(self, path):
        self.fit()
        cls = self.classify()
        emb = self.embedding()
        st = stability(self)
        wins = self.windows
        tg, nm = self._aligned()
        if len(wins) > MAX_WINDOWS:
            idx = np.linspace(0, len(wins) - 1, MAX_WINDOWS).round().astype(int)
            wins = [wins[i] for i in idx]
            tg = [tg[i] for i in idx]
            nm = [nm[i] for i in idx]
        while True:
            self.windows = wins
            self.tags, self.nums = tg, nm
            self._tag_cache.clear()
            data = {"format": "play-fingerprint", "version": 2, "name": self.name,
                    "created": self.created, "updated": datetime.now().isoformat(timespec="seconds"),
                    "window_sec": WINDOW_SEC, "seconds_recorded": round(self.seconds, 1),
                    "feature_names": FEATURE_NAMES,
                    "ref": {"mean": _r(self.ref_mean), "std": _r(self.ref_std)},
                    "class": cls,
                    "stability": None if st is None else _r(st),
                    "keys": self.keys_for_save(), "key_slices": self.key_slices,
                    "embedding": None if emb is None else [float(u) for u in emb],
                    "id": fingerprint_id(emb),
                    "summary": self._summary(),
                    "windows": [[_r(v) for v in w] for w in wins]}
            # Optional extras: only written when something was tagged, so untagged files are
            # byte-for-byte the same shape as before and old readers simply ignore the fields.
            if any(tg):
                data["window_tags"] = tg
            if any(nm):
                data["window_num"] = [None if x is None else {k: _r(v) for k, v in x.items()} for x in nm]
            raw = gzip.compress(json.dumps(data, separators=(",", ":")).encode(), 9)
            if len(raw) <= MAX_BYTES or len(wins) <= 10:
                break
            wins, tg, nm = wins[::2], tg[::2], nm[::2]
        with open(path, "wb") as fh:
            fh.write(raw)
        return len(raw)

    @staticmethod
    def load(path):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            d = json.load(fh)
        p = Profile(d.get("name", os.path.splitext(os.path.basename(path))[0]))
        p.created = d.get("created", p.created)
        p.seconds = float(d.get("seconds_recorded", 0))
        p.keys = _parse_keys(d.get("keys"))
        p.key_slices = int(d.get("key_slices", 0))
        pos = {n: i for i, n in enumerate(d["feature_names"])}
        for row in d["windows"]:
            p.windows.append(np.array(
                [NAN if (n not in pos or row[pos[n]] is None) else row[pos[n]]
                 for n in FEATURE_NAMES], float))
        n_win = len(p.windows)                      # optional context fields (absent in old files)
        tg, nm = d.get("window_tags"), d.get("window_num")
        if isinstance(tg, list):
            p.tags = [_clean_tag(t) for t in tg[:n_win]]
        if isinstance(nm, list):
            p.nums = [_clean_nums(x) for x in nm[:n_win]]
        p.tags, p.nums = p._aligned()
        p.fit()
        return p


def _r(x):
    x = float(x)
    return None if x != x else float(f"{x:.5g}")


def sanitize(name):
    return "".join(c for c in name if c.isalnum() or c in "-_ ").strip()


def list_profiles():
    os.makedirs(PROFILE_DIR, exist_ok=True)
    return sorted(os.path.splitext(f)[0] for f in os.listdir(PROFILE_DIR) if f.endswith(EXT))


def profile_path(name):
    return os.path.join(PROFILE_DIR, name + EXT)


def read_summary(path):
    """Quickly read (class code, embedding) from a fingerprint file."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            d = json.load(fh)
        emb = d.get("embedding")
        return d.get("class", {}).get("code", "k??m??a??cr??p??"), (np.array(emb, float) if emb else None)
    except Exception:
        return "k??m??a??cr??p??", None


def _range_text(cuts, unit=""):
    parts = [f"<{cuts[0]}"] + [f"{a}-{b}" for a, b in zip(cuts, cuts[1:])] + [f">{cuts[-1]}"]
    return ", ".join(parts) + unit


def tags_line(p):
    """One-line summary of a profile's context tags ('' if none)."""
    c = p.tag_counts()
    if not c:
        return ""
    return "Context tags: " + ", ".join(f"{t} x{n}" for t, n in c.most_common(6))

# ----------------------------------------------------------------------------
# Ban list + ban-evasion screening
# ----------------------------------------------------------------------------
def load_banlist():
    try:
        with open(BAN_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_banlist(ban):
    os.makedirs(PROFILE_DIR, exist_ok=True)
    with open(BAN_FILE, "w", encoding="utf-8") as fh:
        json.dump(ban, fh, indent=1)


def _parse_keys(raw):
    out = {}
    for n, v in (raw or {}).items():
        try:
            out[n] = [int(v[0]), int(v[1]), float(v[2]), float(v[3]), int(v[4])]
        except Exception:
            pass
    return out


def read_keys(path):
    """Per-key table of a fingerprint file ({} if none)."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return _parse_keys(json.load(fh).get("keys"))
    except Exception:
        return {}


def slice_key_stats(keys, t_lo, t_hi):
    """Per-key [presses, hold_n, hold_sum, hold_sqsum] for presses in (t_lo, t_hi]."""
    pend, out = {}, {}
    for t, down, n in sorted(keys, key=lambda e: e[0]):
        if len(n) == 1 and not n.isprintable():
            continue
        if down:
            pend[n] = t
            if t_lo < t <= t_hi:
                out.setdefault(n, [0, 0, 0.0, 0.0])[0] += 1
        elif n in pend:
            td = pend.pop(n)
            h = t - td
            if t_lo < td <= t_hi and 0 < h < 2.0:
                r = out.setdefault(n, [0, 0, 0.0, 0.0])
                r[1] += 1
                r[2] += h
                r[3] += h * h
    return out


def aggregate_slices(slices):
    agg = {}
    for st in slices:
        for n, r in st.items():
            e = agg.setdefault(n, [0, 0, 0.0, 0.0, 0])
            e[0] += r[0]
            e[1] += r[1]
            e[2] += r[2]
            e[3] += r[3]
            e[4] += 1
    return agg


def display_key(n):
    return n.upper() if len(n) == 1 else n.replace("_", " ").title()


def key_rows(keys):
    """Rows sorted by share of presses: key, share, class, presses, hold mean/sd (seconds or None)."""
    total = sum(e[0] for e in keys.values())
    rows = []
    for n, e in keys.items():
        share = e[0] / total if total else 0.0
        cls = "Movement" if share > MOVE_SHARE else next(name for cut, name in KEY_CLASSES if share >= cut)
        hm = hs = None
        if e[1] >= 3:
            hm = e[2] / e[1]
            hs = max(e[3] / e[1] - hm * hm, 0.0) ** 0.5
        rows.append({"key": n, "share": share, "cls": cls, "n": e[0], "hold": hm, "hold_sd": hs})
    rows.sort(key=lambda r: -r["share"])
    return rows


def key_df(keysdicts):
    """(number of players with key data, {key: number of players who use it})."""
    df = {}
    n = 0
    for k in keysdicts:
        tot = sum(e[0] for e in k.values())
        if not tot:
            continue
        n += 1
        for key, e in k.items():
            if e[0] / tot >= RARE_SHARE:
                df[key] = df.get(key, 0) + 1
    return n, df


def key_idf(keysdicts):
    """Weight per key: 1.0 for keys everyone uses, up to ~2.4 for keys almost nobody uses."""
    import math
    n, df = key_df(keysdicts)
    if n < 3:
        return None
    return {k: 1.0 + math.log((n + 1) / (c + 1)) for k, c in df.items()}


def key_similarity(a, b, idf=None):
    """Keybind similarity 0-1 between two per-key tables (None if too little data).

    60% key-usage overlap (Bhattacharyya coefficient of the press shares) + 40% overlap of the
    core-key sets (weighted so sharing an unusual key counts more than sharing W or Space)."""
    if not a or not b:
        return None
    ta, tb = sum(e[0] for e in a.values()), sum(e[0] for e in b.values())
    if ta < 20 or tb < 50:
        return None
    pa = {k: e[0] / ta for k, e in a.items()}
    pb = {k: e[0] / tb for k, e in b.items()}
    bc = sum((pa.get(k, 0.0) * pb.get(k, 0.0)) ** 0.5 for k in set(pa) | set(pb))
    wt = (lambda k: idf.get(k, 2.0)) if idf else (lambda k: 1.0)
    ca = {k for k, v in pa.items() if v >= CORE_SHARE}
    cb = {k for k, v in pb.items() if v >= CORE_SHARE}
    uni = ca | cb
    jac = sum(wt(k) for k in ca & cb) / sum(wt(k) for k in uni) if uni else 0.0
    return float(min(1.0, 0.6 * bc + 0.4 * jac))


def key_hold_agreement(live, prof):
    """(keys whose live hold time is within 2 sd of the fingerprint's, keys compared)."""
    ok = tot = 0
    for k, e in live.items():
        q = prof.get(k)
        if e[1] < 3 or not q or q[1] < 10:
            continue
        m = q[2] / q[1]
        sd = max(max(q[3] / q[1] - m * m, 0.0) ** 0.5, 0.1 * m, 0.01)
        tot += 1
        ok += abs(e[2] / e[1] - m) <= 2 * sd
    return ok, tot


def keybind_summary(keys):
    """Keybind signature from a per-key table."""
    rows = key_rows(keys)
    share = {r["key"]: r["share"] for r in rows}
    core = [r for r in rows if r["share"] >= CORE_SHARE]
    best, bs = None, 0.0
    for label, ks in SCHEMES:
        if all(share.get(k, 0.0) >= 0.015 for k in ks):
            sc = sum(share[k] for k in ks)
            if sc > bs:
                best, bs = label, sc
    move = [r["key"] for r in rows if r["cls"] == "Movement"]
    if best:
        scheme = best
    elif move:
        scheme = "custom movement keys"
    else:
        scheme = "no standard movement keys"
    return {"core": [r["key"] for r in core],
            "movement": move,
            "movement_text": " ".join(display_key(k) for k in move) or "(none above 10%)",
            "scheme": scheme,
            "unusual": [display_key(r["key"]) for r in core if r["key"] not in COMMON_BINDS],
            "text": " ".join(display_key(r["key"]) for r in core[:10]) or "(none yet)"}


def keys_line(p):
    if not p.keys:
        return "Keybinds: no per-key data"
    k = keybind_summary(p.keys)
    return f"Keybinds: {k['text']}  ({k['scheme']})"


def feature_weights(profiles):
    """Per-feature weights = how well each feature separates players (F-ratio).

    Needs 3+ fingerprints; returns None otherwise (unweighted matching)."""
    profiles = [p for p in profiles if p.mu is not None]
    if len(profiles) < 3:
        return None
    mus = np.vstack([p.mu for p in profiles])
    sds = np.vstack([p.sd for p in profiles])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        between = np.nanvar(mus, 0)
        within = np.nanmean(np.where(np.isnan(mus), np.nan, sds ** 2), 0)
    ratio = np.nan_to_num(between / np.maximum(within, 1e-12), nan=0.0)
    w = np.clip(ratio, 0.05, 6.0)
    return np.clip(w / w.mean(), 0.2, 3.0)


def stability(p):
    """Self-consistency: fit on the first half of the data, score the second half.

    ~1.0 = very consistent, <1.5 good, >2.2 the data varies a lot (train more / more calmly)."""
    n = len(p.windows)
    if n < 12:
        return None
    a = Profile("half")
    a.windows = p.windows[:n // 2]
    a.fit()
    ds = [d for d in (distance(w, a.mu, a.sd) for w in p.windows[n // 2:]) if d is not None]
    return float(np.mean(ds)) / a.ref_mean if ds else None


def stability_text(p):
    st = stability(p)
    if st is None:
        return "Stability: needs 12+ windows"
    label = "good" if st < 1.5 else ("fair" if st < 2.2 else "weak - train more")
    return f"Stability: {st:.2f} ({label})"


def automation_flags(vec):
    """Heuristic flags for input that looks scripted. Leads only - never proof."""
    def g(n):
        return float(vec[IDX[n]])
    out = []
    cv, ch = g("c_int_cv"), g("c_hold_std")
    if cv == cv and cv < 0.06:
        out.append("Click timing is unusually regular (possible auto-clicker / macro)")
    elif ch == ch and ch < 0.003:
        out.append("Click hold times are almost identical (possible macro)")
    kh, kf = g("k_hold_std"), g("k_flight_cv")
    if kh == kh and kh < 0.003:
        out.append("Key hold times are almost identical (possible macro)")
    elif kf == kf and kf < 0.08:
        out.append("Key timing is unusually even (possible macro)")
    st, cvs = g("m_straight"), g("m_speed_cv")
    if st == st and cvs == cvs and st > 0.985 and cvs < 0.3:
        out.append("Mouse paths are near-perfect lines at steady speed (possible scripted movement)")
    return out


def load_all_profiles(min_windows=5):
    out = {}
    for n in list_profiles():
        try:
            p = Profile.load(profile_path(n))
            p.name = n
            if len(p.windows) >= min_windows:
                out[n] = p
        except Exception:
            pass
    return out


def compare_profiles(cand, ref, w=None):
    """How well do cand's windows fit ref's fingerprint? ~1.0 = same person, higher = less alike."""
    ss = []
    rm = ref.self_ref(w)
    for win in cand.windows:
        d = distance(win, ref.mu, ref.sd, w)
        if d is not None:
            ss.append(d / rm)
    return float(np.median(ss)) if len(ss) >= 3 else None


def pair_score(a, b, w=None):
    """Symmetric similarity score: both fingerprints must fit each other."""
    x, y = compare_profiles(a, b, w), compare_profiles(b, a, w)
    return None if x is None or y is None else (x + y) / 2.0


def screen_player(cand, banned, population, thr, w=None, idf=None):
    """Score one candidate against every banned fingerprint.

    banned: {name: Profile}; population: clean Profiles other than cand (used for context).
    Flag: REVIEW if score <= thr; HIGH if clearly inside thr and, when at least 5 other
    players exist, more similar than 95%+ of them. Returns rows sorted best match first."""
    pop = [s for s in (pair_score(cand, p, w) for p in population) if s is not None]
    rows = []
    for name, b in banned.items():
        if name == cand.name:
            continue
        s = pair_score(cand, b, w)
        if s is None:
            continue
        ks = key_similarity(cand.keys, b.keys, idf)
        pct = 100.0 * sum(1 for x in pop if x > s) / len(pop) if len(pop) >= 5 else None
        flag = "NONE"
        if s <= thr:
            flag = "REVIEW"
            if s <= 0.8 * thr and (pct is None or pct >= 95.0):
                flag = "HIGH"
        rows.append({"banned": name, "score": round(s, 3),
                     "similarity": round(100.0 / (1.0 + (s / thr) ** 4), 1),
                     "population_rank": None if pct is None else round(pct, 1),
                     "key_sim": None if ks is None else round(ks, 3),
                     "flag": flag})
    rows.sort(key=lambda r: r["score"])
    return rows


# ----------------------------------------------------------------------------
# Server sync (optional)
# ----------------------------------------------------------------------------
CONFIG_FILE = os.path.join(APP_DATA_DIR, "server_config.json")
WAKE_TOTAL = 120.0       # keep pinging this long - a sleeping Render free instance takes ~30-60 s
WAKE_GAP = 3.0


def load_server_cfg():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:
        d = {}
    return {"url": str(d.get("url", "")).strip().rstrip("/"), "key": str(d.get("key", "")).strip(),
            "wake_on_start": bool(d.get("wake_on_start", True)),
            "auto_upload": bool(d.get("auto_upload", False))}


def save_server_cfg(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=1)
    try:
        os.chmod(CONFIG_FILE, 0o600)          # holds the API key
    except OSError:
        pass


def server_call(cfg, method, path, body=None, timeout=30, content_type="application/octet-stream",
                auth=True):
    """-> (http_status, bytes). Raises on network errors. Only https (or localhost) is allowed
    so the API key is never sent in clear text."""
    url = cfg.get("url", "")
    if not url:
        raise ValueError("no server URL set")
    if not url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        raise ValueError("server URL must start with https://")
    req = urllib.request.Request(url + path, data=body, method=method)
    if auth and cfg.get("key"):
        req.add_header("Authorization", "Bearer " + cfg["key"])
    if body is not None:
        req.add_header("Content-Type", content_type)
    req.add_header("User-Agent", "play-fingerprint/2")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def wake_server(cfg, state):
    """Ping /health until the server answers (wakes a sleeping Render instance).

    Runs in a background thread; `state` is a dict the UI polls (text / fg / ok)."""
    t0 = time.time()
    n = 0
    state.update(text="Server: waking up...", fg="#d77a00", ok=None)
    while time.time() - t0 < WAKE_TOTAL:
        n += 1
        try:
            code, _ = server_call(cfg, "GET", "/health", timeout=15, auth=False)
            if code == 200:
                state.update(text=f"Server: online ({time.time() - t0:.0f}s)", fg="#1a8a3a", ok=True)
                return True
            state.update(text=f"Server: HTTP {code}, retrying (try {n})...", fg="#d77a00")
        except ValueError as e:                      # bad / missing URL - retrying won't help
            state.update(text=f"Server: {e}", fg="#d11", ok=False)
            return False
        except Exception:                            # sleeping instances time out / reset while booting
            state.update(text=f"Server: waking up... ({time.time() - t0:.0f}s, try {n})", fg="#d77a00")
        time.sleep(WAKE_GAP)
    state.update(text="Server: unreachable", fg="#d11", ok=False)
    return False


def read_updated(path):
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return str(json.load(fh).get("updated", ""))
    except Exception:
        return ""


def _q(name):
    return urllib.parse.quote(name, safe="")

# ----------------------------------------------------------------------------
# In-game integrations (optional, opt-in, local only)
#
# SANCTIONED SOURCES ONLY: a game's official state/telemetry feature, official APIs or SDKs,
# documented log files, or data the user's own game config exports. Nothing in here reads process
# memory, injects into / hooks a game process, scrapes the screen, or touches anti-cheat-protected
# components, and it must not be extended to do so.
# ----------------------------------------------------------------------------
INTEG_NOTICE = ("Sanctioned data sources only: a game's official state/telemetry feature, official APIs or "
                "SDKs, documented log files, or data your own game config exports. This tool never reads game "
                "process memory, injects into or hooks game processes, scrapes the screen, or touches "
                "anti-cheat-protected components. Everything here is off until you press Start, listeners "
                "bind to 127.0.0.1 only, and only derived context tags are saved - never raw game data.")


def _clamp(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return default if v != v else max(lo, min(hi, v))


def _ident(s, n=32):
    """Integration / tag names: letters, digits, _ and - only (no dots: refs are 'name.key')."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(s)).strip("_")[:n]


def _slug(s, n=TAG_MAX_LEN):
    return re.sub(r"[^A-Za-z0-9_.+-]+", "_", str(s)).strip("_")[:n]


def _auto_value(s):
    """'12' -> 12, '3.5' -> 3.5, 'true' -> True, anything else stays a (bounded) string."""
    s = str(s).strip()
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(s)
    except ValueError:
        pass
    try:
        f = float(s)
        if math.isfinite(f):
            return f
    except ValueError:
        pass
    return s[:INTEG_MAX_STR]


def _clean_scalar(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v if abs(v) < 2 ** 53 else None
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, str):
        return v[:INTEG_MAX_STR]
    return None                       # None, lists, dicts, bytes ... are never kept


def flatten_payload(obj, prefix="", out=None, depth=0):
    """Nested JSON -> {'a.b.0': scalar}. Bounded in depth, key count, key length and string length."""
    if out is None:
        out = {}
    if len(out) >= INTEG_MAX_KEYS or depth > INTEG_MAX_DEPTH:
        return out
    if isinstance(obj, dict):
        items = obj.items()
    elif isinstance(obj, (list, tuple)):
        items = enumerate(obj)
    else:
        v = _clean_scalar(obj)
        if v is not None and prefix:
            out[prefix[:INTEG_MAX_KEY_LEN]] = v
        return out
    for k, v in items:
        if len(out) >= INTEG_MAX_KEYS:
            break
        k = str(k)[:INTEG_MAX_KEY_LEN]
        flatten_payload(v, f"{prefix}.{k}" if prefix else k, out, depth + 1)
    return out


class GameState:
    """Thread-safe, bounded, in-memory store of the latest scalar game values per source.

    Values go stale: snapshot() only returns values updated within the source's stale_after
    seconds, so a source that stops updating simply falls back to 'no data' (the defaults)."""

    def __init__(self, clock=time.monotonic, stale_after=INTEG_STALE_DEFAULT):
        self._lock = threading.Lock()
        self._data = {}            # source -> {key: (value, time)}
        self._stale = {}
        self._clock = clock
        self.default_stale = stale_after

    def set_stale(self, source, secs):
        self._stale[source] = _clamp(secs, 1.0, 3600.0, self.default_stale)

    def _limit(self, source):
        return self._stale.get(source, self.default_stale)

    def update(self, source, values, replace=False):
        """Store sanitised scalars. replace=True drops the source's earlier keys first."""
        if not isinstance(values, dict):
            return 0
        clean = {}
        for k, v in list(values.items())[:INTEG_MAX_KEYS * 4]:
            k = str(k)[:INTEG_MAX_KEY_LEN]
            v = _clean_scalar(v)
            if k and v is not None and len(clean) < INTEG_MAX_KEYS:
                clean[k] = v
        now = self._clock()
        with self._lock:
            lim = self._limit(source)
            cur = {} if replace else {k: e for k, e in self._data.get(source, {}).items()
                                      if now - e[1] <= lim}
            for k, v in clean.items():
                if k in cur or len(cur) < INTEG_MAX_KEYS:
                    cur[k] = (v, now)
            self._data[source] = cur
        return len(clean)

    def snapshot(self, now=None):
        """{'source.key': value} for every non-stale value."""
        now = self._clock() if now is None else now
        out = {}
        with self._lock:
            for src, d in self._data.items():
                lim = self._limit(src)
                for k, (v, t) in d.items():
                    if now - t <= lim:
                        out[f"{src}.{k}"] = v
        return out

    def clear(self, source=None):
        with self._lock:
            if source is None:
                self._data.clear()
            else:
                self._data.pop(source, None)


# ---- conditions, AFK rules, context tags (pure functions) ---------------------------------
_COND_OPS = {"==", "!=", ">", ">=", "<", "<=", "in", "not_in", "contains", "regex", "exists", "missing"}


def _norm_eq(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return str(a).lower() == str(b).lower()
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def valid_cond(c, depth=0):
    """Is this a well-formed condition (leaf, all / any / not)?"""
    if not isinstance(c, dict) or depth > 4:
        return False
    for k in ("all", "any"):
        if k in c:
            return isinstance(c[k], list) and 0 < len(c[k]) <= 8 and all(valid_cond(x, depth + 1) for x in c[k])
    if "not" in c:
        return valid_cond(c["not"], depth + 1)
    ref, op = c.get("ref"), c.get("op", "==")
    if not isinstance(ref, str) or "." not in ref or op not in _COND_OPS:
        return False
    if op in ("in", "not_in") and not isinstance(c.get("value"), list):
        return False
    if op == "regex":
        try:
            re.compile(str(c.get("value", "")))
        except re.error:
            return False
    return True


def eval_cond(cond, values):
    """True / False, or None when the data the condition needs is missing or stale."""
    if not isinstance(cond, dict):
        return None
    if "all" in cond or "any" in cond:
        is_all = "all" in cond
        rs = [eval_cond(c, values) for c in cond["all" if is_all else "any"]]
        decisive = False if is_all else True
        if any(r is decisive for r in rs):
            return decisive
        return None if any(r is None for r in rs) else is_all
    if "not" in cond:
        r = eval_cond(cond["not"], values)
        return None if r is None else (not r)
    ref, op, want = cond.get("ref"), cond.get("op", "=="), cond.get("value")
    present = ref in values
    if op == "exists":
        return present
    if op == "missing":
        return not present
    if not present:
        return None
    have = values[ref]
    try:
        if op in ("==", "eq"):
            return _norm_eq(have, want)
        if op == "!=":
            return not _norm_eq(have, want)
        if op == ">":
            return float(have) > float(want)
        if op == ">=":
            return float(have) >= float(want)
        if op == "<":
            return float(have) < float(want)
        if op == "<=":
            return float(have) <= float(want)
        if op == "in":
            return any(_norm_eq(have, w) for w in want)
        if op == "not_in":
            return not any(_norm_eq(have, w) for w in want)
        if op == "contains":
            return str(want).lower() in str(have).lower()
        if op == "regex":
            return re.search(str(want), str(have)) is not None
    except (TypeError, ValueError, re.error):
        return None
    return None


def afk_rule_check(rules, values):
    """-> (applies, rule_name): 'while <when> is true, count me as AFK unless <unless> is true'.

    If the data a rule needs is missing/stale the rule does NOT apply, unless it sets
    on_unknown="afk"."""
    for r in rules:
        w = eval_cond(r["when"], values)
        if w is False:
            continue
        u = eval_cond(r["unless"], values) if r.get("unless") else False
        if u is True:
            continue
        if w is True and u is False:
            return True, r["name"]
        if r.get("on_unknown") == "afk":
            return True, r["name"] + " (no data)"
    return False, None


def tag_value(d, values):
    """One tag definition -> short tag string or None. Numbers need map/bins (keeps tags coarse)."""
    v = values.get(d["ref"])
    if v is None:
        return d.get("default") or None
    mp, bins = d.get("map"), d.get("bins")
    if mp:
        key = str(v).lower() if isinstance(v, bool) else str(v)
        hit = mp.get(key, mp.get(key.lower()))
        return str(hit) if hit is not None else d.get("other", "other")
    if bins:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return None
        i = sum(x >= c for c in bins)
        if i == 0:
            return f"lt{bins[0]:g}"
        return f"ge{bins[-1]:g}" if i == len(bins) else f"{bins[i - 1]:g}to{bins[i]:g}"
    if isinstance(v, float) and not v.is_integer():
        return None
    return str(int(v)) if isinstance(v, float) else str(v)


def compute_tag(defs, values):
    """Combine all tag definitions into one derived context tag ('' = untagged)."""
    parts = []
    for d in defs:
        t = tag_value(d, values)
        if t:
            parts.append(f"{_slug(d['name'], 16)}={_slug(t, 24)}")
    return "|".join(parts)[:TAG_MAX_LEN]


def compute_nums(defs, values):
    out = {}
    for d in defs[:NUM_MAX]:
        v = values.get(d["ref"])
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
            out[d["name"]] = round(float(v), 4)
    return out or None


def dominant_tag(samples, now, span, min_share=TAG_MIN_SHARE):
    """Tag held for >= min_share of the last `span` seconds; '' if mixed (a transition) or no data.

    samples: iterable of tuples whose first item is a time and second item the tag."""
    recent = [s[1] for s in samples if s[0] >= now - span]
    if not recent:
        return ""
    tag, n = Counter(recent).most_common(1)[0]
    return tag if n / len(recent) >= min_share else ""


# ---- decoders / callbacks / mappers (plugin extension points) ------------------------------
DECODERS, CALLBACKS, MAPPERS = {}, {}, {}


def register_decoder(name, fn):
    """fn(data: bytes, cfg: dict) -> {key: scalar}. Used by the udp source (cfg 'decoder')."""
    DECODERS[str(name)] = fn


def register_callback(name, fn):
    """fn(emit, stop_event): produce values with emit({...}); return when stop_event is set."""
    CALLBACKS[str(name)] = fn


def register_mapper(name, fn):
    """fn({key: scalar}) -> {key: scalar}. Per-game field mapping (cfg 'mapper' on any integration)."""
    MAPPERS[str(name)] = fn


def make_json_mapper(spec):
    """Declarative per-game mapping: {"rename": {old: new}, "drop": [prefix...], "keep_only": bool}."""
    rename = {str(k): str(v) for k, v in dict(spec.get("rename", {})).items()}
    drop = tuple(str(x) for x in spec.get("drop", []))
    keep_only = bool(spec.get("keep_only", False))

    def fn(values):
        out = {}
        for k, v in values.items():
            if drop and k.startswith(drop):
                continue
            if k in rename:
                out[rename[k]] = v
            elif not keep_only:
                out[k] = v
        return out
    return fn


def _decode_json(data, cfg):
    return flatten_payload(json.loads(data.decode("utf-8")))


def _decode_text_kv(data, cfg):
    out = {}
    for line in data.decode("utf-8", "replace").splitlines()[:INTEG_MAX_KEYS]:
        if "=" in line:
            k, v = line.split("=", 1)
            if k.strip():
                out[k.strip()[:INTEG_MAX_KEY_LEN]] = _auto_value(v)
    return out


_STRUCT_FMT = re.compile(r"^[<>=!@]?[bBhHiIlLqQfd?]$")


def _decode_struct(data, cfg):
    """Fixed binary layout from config: fields = [{"key", "offset", "fmt": "<f", "scale"?}]."""
    out = {}
    for f in list(cfg.get("fields", []))[:INTEG_MAX_KEYS]:
        fmt, off = str(f.get("fmt", "")), int(f.get("offset", 0))
        if not _STRUCT_FMT.match(fmt) or off < 0 or off + struct.calcsize(fmt) > len(data):
            continue
        v = struct.unpack_from(fmt, data, off)[0]
        if isinstance(v, (int, float)) and not isinstance(v, bool) and f.get("scale"):
            v = v * float(f["scale"])
        out[str(f.get("key", f"f{off}"))[:INTEG_MAX_KEY_LEN]] = v
    return out


DECODERS.update({"json": _decode_json, "text_kv": _decode_text_kv, "struct": _decode_struct})


# ---- sources ------------------------------------------------------------------------------
class IntegrationSource:
    """Base class: one daemon thread, bounded memory, never raises into the caller.

    Subclasses implement open() (bind/validate - runs in start(), so errors show up at once),
    run() (loop until self._stop is set) and close() (release resources)."""
    kind = "base"

    def __init__(self, name, cfg, state, clock=time.monotonic, mapper=None):
        self.name, self.cfg, self.state, self._clock, self.mapper = name, dict(cfg or {}), state, clock, mapper
        self.status = "stopped"          # stopped | running | error | finished
        self.last_update = None
        self.last_error, self.last_error_t = "", None
        self.count = self.bad = 0
        self._stop = threading.Event()
        self._thread = None
        state.set_stale(name, self.cfg.get("stale_after", state.default_stale))

    # -- to override
    def open(self):
        pass

    def run(self):
        raise NotImplementedError

    def close(self):
        pass

    def describe(self):
        return ""

    # -- shared plumbing
    def fail(self, e, where=""):
        text = e if isinstance(e, str) else f"{type(e).__name__}: {e}"
        self.last_error = (f"{where}: " if where else "") + text[:200]
        self.last_error_t = self._clock()

    def malformed(self, why):
        self.bad += 1
        self.fail(why)

    def push(self, values, replace=False):
        """Hand a decoded payload to the shared state (mapper applied, everything sanitised)."""
        if self._stop.is_set():
            return 0
        if self.mapper:
            try:
                values = self.mapper(values) or {}
            except Exception as e:
                self.fail(e, "mapper")
                return 0
        n = self.state.update(self.name, values, replace)
        self.last_update = self._clock()
        self.count += 1
        return n

    def active(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.active():
            self.fail("previous run is still shutting down")
            return False
        self._stop.clear()
        self.last_error, self.last_error_t = "", None
        try:
            self.open()
        except Exception as e:
            self.fail(e)
            self.status = "error"
            try:
                self.close()
            except Exception:
                pass
            return False
        self.status = "running"
        self._thread = threading.Thread(target=self._guard, name=f"integration-{self.name}", daemon=True)
        self._thread.start()
        return True

    def _guard(self):
        fails = 0
        while not self._stop.is_set():
            try:
                self.status = "running"
                self.run()
                if not self._stop.is_set():
                    self.status = "finished"
                return
            except Exception as e:                       # a crash must never propagate
                fails += 1
                self.fail(e, "crashed")
                self.status = "error"
                if self._stop.wait(min(30.0, 2.0 ** min(fails, 5))):
                    return
                try:                                     # reopen sockets / files, then run again
                    self.close()
                    self.open()
                except Exception as e2:
                    self.fail(e2, "reopen")

    def stop(self):
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(2.0)
        try:
            self.close()
        except Exception:
            pass
        self.state.clear(self.name)                      # stopped = no data = defaults apply
        self.status = "stopped"


class _LoopbackServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = False      # never share a port (matters on Windows)
    daemon_threads = True


class HttpPushSource(IntegrationSource):
    """127.0.0.1-only listener for JSON pushed by a game's official state-integration feature."""
    kind = "http_push"

    def open(self):
        token = str(self.cfg.get("token", ""))
        if len(token) < 8:
            raise ValueError("http_push needs a shared 'token' of at least 8 characters")
        try:
            port = int(self.cfg.get("port", 0))
        except (TypeError, ValueError):
            raise ValueError("invalid port")
        if not 0 <= port <= 65535:
            raise ValueError("invalid port")
        self._token = token.encode("utf-8")
        self.max_body = int(_clamp(self.cfg.get("max_body"), 256, INTEG_BODY_HARD_MAX, INTEG_BODY_DEFAULT))
        src, sem = self, threading.BoundedSemaphore(8)

        class Handler(http.server.BaseHTTPRequestHandler):
            timeout = 5

            def log_message(self, *a):
                pass

            def _reply(self, code):
                try:
                    self.send_response(code)
                    self.send_header("Content-Length", "0")
                    self.send_header("Connection", "close")
                    self.end_headers()
                except Exception:
                    pass

            def do_POST(self):
                if not sem.acquire(blocking=False):
                    return self._reply(503)
                try:
                    code = src._handle(self)
                except Exception as e:
                    src.malformed(f"request failed: {type(e).__name__}")
                    code = 400
                finally:
                    sem.release()
                self._reply(code)

            do_PUT = do_POST

        self._srv = _LoopbackServer(("127.0.0.1", port), Handler)   # localhost only, always
        self._srv.timeout = 0.25
        self.port = self._srv.server_address[1]

    def _token_ok(self, supplied):
        return isinstance(supplied, str) and hmac.compare_digest(supplied.encode("utf-8"), self._token)

    def _handle(self, h):
        try:
            n = int(h.headers.get("Content-Length", ""))
        except ValueError:
            return 411
        if n < 0:
            return 400
        if n > self.max_body:
            self.malformed(f"body of {n} bytes exceeds max_body ({self.max_body})")
            return 413
        raw = h.rfile.read(n)
        if len(raw) != n:
            return 400
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError):
            self.malformed("body is not valid JSON")
            return 400
        auth = h.headers.get("Authorization", "")
        supplied = auth[7:] if auth.lower().startswith("bearer ") else h.headers.get("X-Integration-Token", "")
        if not self._token_ok(supplied) and not (
                isinstance(obj, dict) and isinstance(obj.get("auth"), dict) and self._token_ok(obj["auth"].get("token"))):
            self.malformed("rejected a request with a missing or wrong token")
            return 401
        if not isinstance(obj, dict):
            self.malformed("JSON body must be an object")
            return 400
        self.push(flatten_payload({k: v for k, v in obj.items() if k != "auth"}), replace=True)   # token never stored
        return 200

    def run(self):
        while not self._stop.is_set():
            self._srv.handle_request()          # returns after <= 0.25 s so stop() is prompt

    def close(self):
        srv, self._srv = getattr(self, "_srv", None), None
        if srv is not None:
            srv.server_close()

    def describe(self):
        return f"127.0.0.1:{getattr(self, 'port', '?')}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class HttpPollSource(IntegrationSource):
    """Polls a LOCAL http endpoint (127.0.0.1 / localhost only, no proxies, no redirects) for JSON."""
    kind = "http_poll"

    def open(self):
        self.url = str(self.cfg.get("url", ""))
        u = urllib.parse.urlsplit(self.url)
        if u.scheme not in ("http", "https") or (u.hostname or "").lower() not in LOOPBACK_HOSTS:
            raise ValueError("http_poll url must point at 127.0.0.1 / localhost")
        self.interval = _clamp(self.cfg.get("interval"), 0.25, 3600.0, 1.0)
        self.timeout = _clamp(self.cfg.get("timeout"), 0.5, 10.0, 3.0)
        self.max_bytes = int(_clamp(self.cfg.get("max_bytes"), 1024, INTEG_BODY_HARD_MAX, 256 * 1024))
        self.headers = {str(k)[:64]: str(v)[:256] for k, v in list(dict(self.cfg.get("headers", {})).items())[:8]}
        self.headers.setdefault("User-Agent", "play-fingerprint/2")
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
        self._desc = f"{u.hostname}:{u.port or (443 if u.scheme == 'https' else 80)}{u.path}"

    def run(self):
        while not self._stop.is_set():
            try:
                with self._opener.open(urllib.request.Request(self.url, headers=self.headers),
                                       timeout=self.timeout) as r:
                    raw = r.read(self.max_bytes + 1)
                if len(raw) > self.max_bytes:
                    self.malformed(f"response larger than max_bytes ({self.max_bytes})")
                else:
                    self.push(flatten_payload(json.loads(raw.decode("utf-8"))), replace=True)
            except Exception as e:                      # endpoint down / bad JSON: keep trying, values go stale
                self.fail(e, "poll")
            self._stop.wait(self.interval)

    def describe(self):
        return getattr(self, "_desc", "")


class LogTailSource(IntegrationSource):
    """Follows a documented log / telemetry file and extracts values with configurable regexes."""
    kind = "log_tail"

    def open(self):
        self.path = os.path.expanduser(str(self.cfg.get("path", "")))
        if not self.path:
            raise ValueError("log_tail needs a 'path'")
        self.pats = []
        for p in list(self.cfg.get("patterns", []))[:16]:
            grp = p.get("group", 1)
            self.pats.append((str(p["key"])[:INTEG_MAX_KEY_LEN], re.compile(str(p["regex"])[:512]),
                              grp, p.get("map") or {}))
        if not self.pats:
            raise ValueError("log_tail needs at least one pattern {key, regex}")
        self.interval = _clamp(self.cfg.get("interval"), 0.1, 60.0, 0.5)
        self._pos = self._ino = None
        self._buf = b""

    def _poll_once(self):
        st = os.stat(self.path)
        if self._pos is None:                           # start at the end: never replay old history
            self._pos = 0 if self.cfg.get("from_start") else st.st_size
            self._ino = st.st_ino
        if st.st_size < self._pos or st.st_ino != self._ino:       # truncated or rotated
            self._pos, self._ino, self._buf = 0, st.st_ino, b""
        if st.st_size <= self._pos:
            return
        with open(self.path, "rb") as fh:
            fh.seek(self._pos)
            chunk = fh.read(min(st.st_size - self._pos, 256 * 1024))
        self._pos += len(chunk)
        lines = (self._buf + chunk).split(b"\n")
        self._buf = lines.pop()[-4096:]                 # incomplete last line, bounded
        out = {}
        for line in lines[-500:]:
            text = line[:2000].decode("utf-8", "replace")
            for key, rx, grp, mp in self.pats:
                m = rx.search(text)
                if m:
                    v = m.group(grp)
                    if v is not None:
                        out[key] = mp.get(v, _auto_value(v)) if mp else _auto_value(v)
        if out:
            self.push(out)

    def run(self):
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as e:                      # missing file, bad group, permissions ...
                self.fail(e, "tail")
            self._stop.wait(self.interval)

    def describe(self):
        return os.path.basename(getattr(self, "path", "")) or ""


class UdpSource(IntegrationSource):
    """127.0.0.1-only UDP listener; packets are turned into values by a pluggable decoder."""
    kind = "udp"

    def open(self):
        try:
            port = int(self.cfg.get("port", 0))
        except (TypeError, ValueError):
            raise ValueError("invalid port")
        name = str(self.cfg.get("decoder", "json"))
        self._fn = DECODERS.get(name)
        if self._fn is None:
            raise ValueError(f"unknown decoder {name!r} (known: {', '.join(sorted(DECODERS))})")
        self.max_packet = int(_clamp(self.cfg.get("max_packet"), 16, 65507, 2048))
        self.max_rate = _clamp(self.cfg.get("max_rate"), 1, 5000, 500)      # packets/second kept
        self._win_t, self._win_n, self.dropped = 0.0, 0, 0
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(0.5)
            s.bind(("127.0.0.1", port))                 # localhost only, always
        except Exception:
            s.close()
            raise
        self._sock, self.port = s, s.getsockname()[1]

    def run(self):
        while not self._stop.is_set():
            try:
                data, _ = self._sock.recvfrom(self.max_packet + 1)
            except socket.timeout:
                continue
            except ConnectionResetError:                # Windows reports ICMP errors on UDP sockets
                continue
            now = time.monotonic()
            if now - self._win_t >= 1.0:
                self._win_t, self._win_n = now, 0
            self._win_n += 1
            if self._win_n > self.max_rate:
                self.dropped += 1
                continue
            if len(data) > self.max_packet:
                self.malformed("oversize packet dropped")
                continue
            try:
                self.push(self._fn(data, self.cfg))
            except Exception as e:
                self.malformed(f"decode failed: {type(e).__name__}: {e}")

    def close(self):
        s, self._sock = getattr(self, "_sock", None), None
        if s is not None:
            s.close()

    def describe(self):
        return f"udp 127.0.0.1:{getattr(self, 'port', '?')} ({self.cfg.get('decoder', 'json')})"


class CallbackSource(IntegrationSource):
    """Runs a registered Python function fn(emit, stop_event) on its own daemon thread."""
    kind = "callback"

    def open(self):
        name = str(self.cfg.get("callback", ""))
        self._fn = CALLBACKS.get(name)
        if self._fn is None:
            raise ValueError(f"no callback registered as {name!r} (see register_callback / plugins)")

    def run(self):
        self._fn(lambda values, replace=False: self.push(values, replace), self._stop)

    def describe(self):
        return f"callback {self.cfg.get('callback', '')}"


SOURCE_KINDS = {c.kind: c for c in (HttpPushSource, HttpPollSource, LogTailSource, UdpSource, CallbackSource)}


# ---- configuration --------------------------------------------------------------------------
def example_config_text():
    """The placeholder-only example from the module docstring (everything disabled)."""
    m = re.search(r"integrations\.json example:\n(.*?)\n\s*--- end example ---", __doc__ or "", re.S)
    return m.group(1) if m else json.dumps({"version": 1, "plugins_enabled": False, "integrations": {}}, indent=2)


def clean_integ_cfg(raw):
    """Validate a raw config dict -> (clean config, warnings). Bad entries are dropped, never fatal."""
    warns = []
    raw = raw if isinstance(raw, dict) else {}
    stale = _clamp(raw.get("stale_after"), 1.0, 3600.0, INTEG_STALE_DEFAULT)
    cfg = {"plugins_enabled": raw.get("plugins_enabled") is True, "stale_after": stale,
           "integrations": {}, "afk_rules": [], "tags": [], "numeric": []}
    for name, sc in list(dict(raw.get("integrations") or {}).items())[:INTEG_MAX_SOURCES]:
        n = _ident(name)
        if not n or not isinstance(sc, dict):
            warns.append(f"integration {name!r}: skipped (bad name or entry)")
            continue
        if sc.get("kind") not in SOURCE_KINDS:
            warns.append(f"integration {n!r}: unknown kind {sc.get('kind')!r}")
            continue
        sc = dict(sc)
        sc["enabled"] = sc.get("enabled") is True
        sc["stale_after"] = _clamp(sc.get("stale_after"), 1.0, 3600.0, stale)
        cfg["integrations"][n] = sc
    for i, r in enumerate(list(raw.get("afk_rules") or [])[:32]):
        if not isinstance(r, dict) or r.get("enabled", True) is False:
            continue
        name = _ident(r.get("name") or f"rule{i + 1}")
        if not valid_cond(r.get("when")) or (r.get("unless") is not None and not valid_cond(r.get("unless"))):
            warns.append(f"afk rule {name!r}: invalid 'when'/'unless' condition - skipped")
            continue
        cfg["afk_rules"].append({"name": name, "when": r["when"], "unless": r.get("unless"),
                                 "on_unknown": "afk" if r.get("on_unknown") == "afk" else "ignore"})
    for i, t in enumerate(list(raw.get("tags") or [])[:8]):
        if not isinstance(t, dict) or not isinstance(t.get("ref"), str) or "." not in t["ref"]:
            warns.append(f"tag #{i + 1}: needs a 'ref' like 'integration.key' - skipped")
            continue
        d = {"name": _ident(t.get("name") or f"tag{i + 1}", 16), "ref": t["ref"]}
        if isinstance(t.get("map"), dict):
            d["map"] = {str(k): str(v) for k, v in list(t["map"].items())[:32]}
        if isinstance(t.get("bins"), list):
            try:
                d["bins"] = sorted(float(b) for b in t["bins"][:8])
            except (TypeError, ValueError):
                warns.append(f"tag {d['name']!r}: bad 'bins' ignored")
        for k in ("other", "default"):
            if isinstance(t.get(k), str):
                d[k] = t[k]
        cfg["tags"].append(d)
    for i, t in enumerate(list(raw.get("numeric") or [])[:NUM_MAX]):
        if isinstance(t, dict) and isinstance(t.get("ref"), str) and "." in t["ref"]:
            cfg["numeric"].append({"name": _ident(t.get("name") or f"num{i + 1}", 16), "ref": t["ref"]})
    return cfg, warns


def load_plugins(plugin_dir, allow_py):
    """Load declarative *.json mappings always, and *.py plugins only if allow_py. -> warnings."""
    warns = []
    if not os.path.isdir(plugin_dir):
        return warns
    for fn in sorted(os.listdir(plugin_dir))[:32]:
        path = os.path.join(plugin_dir, fn)
        try:
            if os.path.getsize(path) > 64 * 1024:
                warns.append(f"plugin {fn}: skipped (over 64 KB)")
            elif fn.endswith(".json"):
                with open(path, encoding="utf-8") as fh:
                    spec = json.load(fh)
                register_mapper(_ident(spec.get("name") or fn[:-5]), make_json_mapper(spec))
            elif fn.endswith(".py"):
                if not allow_py:
                    warns.append(f"plugin {fn}: skipped (set \"plugins_enabled\": true to allow .py plugins)")
                    continue
                ms = importlib.util.spec_from_file_location("pf_plugin_" + _ident(fn[:-3]), path)
                mod = importlib.util.module_from_spec(ms)
                ms.loader.exec_module(mod)
                if not callable(getattr(mod, "register", None)):
                    warns.append(f"plugin {fn}: no register(api) function")
                    continue
                mod.register(types.SimpleNamespace(register_decoder=register_decoder,
                                                   register_callback=register_callback,
                                                   register_mapper=register_mapper))
        except Exception as e:
            warns.append(f"plugin {fn}: {type(e).__name__}: {e}")
    return warns


class IntegrationManager:
    """Owns the shared GameState, the configured sources and the AFK-rule / tag definitions.

    Nothing starts by itself: a source only runs after start(name) (the user pressing Start)."""

    def __init__(self, path=INTEG_CONFIG_FILE, plugin_dir=INTEG_PLUGIN_DIR, clock=time.monotonic,
                 session_running=None):
        self.path, self.plugin_dir, self.clock = path, plugin_dir, clock
        self.session_running = session_running or (lambda: False)
        self.state = GameState(clock)
        self.sources = {}
        self.cfg, self.warnings = clean_integ_cfg({})
        self.load()

    def load(self):
        """(Re)read the config. Blocked while a Train/Detect session runs. -> (ok, message)"""
        if self.session_running():
            return False, "Reloading is blocked while a Train/Detect session is running."
        self.stop_all()
        raw, warns = {}, []
        try:
            if os.path.exists(self.path):
                if os.path.getsize(self.path) > INTEG_MAX_CFG_BYTES:
                    raise ValueError("config file is larger than 256 KB")
                with open(self.path, encoding="utf-8") as fh:
                    raw = json.load(fh)
        except Exception as e:
            warns.append(f"config unreadable: {type(e).__name__}: {e}")
        cfg, w2 = clean_integ_cfg(raw)
        warns += w2
        warns += load_plugins(self.plugin_dir, cfg["plugins_enabled"])
        self.cfg = cfg
        self.state.clear()
        self.state.default_stale = cfg["stale_after"]
        self.sources = {}
        for name, sc in cfg["integrations"].items():
            mapper = None
            if sc.get("mapper"):
                mapper = MAPPERS.get(str(sc["mapper"]))
                if mapper is None:
                    warns.append(f"integration {name!r}: unknown mapper {sc['mapper']!r}")
            self.sources[name] = SOURCE_KINDS[sc["kind"]](name, sc, self.state, self.clock, mapper)
        self.warnings = warns
        return True, f"{len(self.sources)} integration(s), {len(cfg['afk_rules'])} AFK rule(s), {len(cfg['tags'])} tag(s)"

    def add_source(self, src):
        """Programmatic hook (tests / embedding): register an already-built source object."""
        self.sources[src.name] = src
        src.cfg.setdefault("enabled", True)

    def start(self, name):
        src = self.sources.get(name)
        if src is None:
            return False
        if src.cfg.get("enabled") is not True:
            src.fail('disabled in config - set "enabled": true to allow it')
            return False
        return src.start()

    def stop(self, name):
        src = self.sources.get(name)
        if src is not None:
            src.stop()

    def stop_all(self):
        for src in list(self.sources.values()):
            src.stop()

    def any_active(self):
        return any(s.active() for s in self.sources.values())

    def values(self):
        return self.state.snapshot()

    def afk_check(self):
        """-> (applies, rule_name). Only evaluated while at least one integration is running."""
        if not self.cfg["afk_rules"] or not self.any_active():
            return False, None
        return afk_rule_check(self.cfg["afk_rules"], self.state.snapshot())

    def current_tag(self):
        if not self.cfg["tags"] or not self.any_active():
            return ""
        return compute_tag(self.cfg["tags"], self.state.snapshot())

    def current_nums(self):
        if not self.cfg["numeric"] or not self.any_active():
            return None
        return compute_nums(self.cfg["numeric"], self.state.snapshot())

    def rows(self):
        now, snap, out = self.clock(), self.state.snapshot(), []
        for n, s in self.sources.items():
            pre = n + "."
            out.append({"name": n, "kind": s.kind, "enabled": s.cfg.get("enabled") is True,
                        "status": s.status, "detail": s.describe(),
                        "age": None if s.last_update is None else now - s.last_update,
                        "count": s.count, "bad": s.bad, "error": s.last_error,
                        "error_age": None if s.last_error_t is None else now - s.last_error_t,
                        "values": {k[len(pre):]: v for k, v in snap.items() if k.startswith(pre)}})
        return out

# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Play Fingerprint")
        self.geometry("1120x860")
        self.minsize(900, 680)
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        # A consistent, higher-contrast desktop theme.
        self.configure(bg="#eef2f7")
        style.configure(".", font=("Segoe UI", 10))
        style.configure("TFrame", background="#eef2f7")
        style.configure("TLabel", background="#eef2f7", foreground="#182235")
        style.configure("TLabelFrame", background="#eef2f7", bordercolor="#cbd5e1")
        style.configure("TLabelFrame.Label", background="#eef2f7", foreground="#263b5a",
                        font=("Segoe UI", 10, "bold"))
        style.configure("TButton", padding=(12, 7), background="#e0e8f4", foreground="#183153")
        style.map("TButton", background=[("active", "#cbdcf3"), ("disabled", "#e5e7eb")])
        style.configure("TNotebook", background="#eef2f7", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 10), font=("Segoe UI", 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", "#ffffff"), ("!selected", "#dce5f1")],
                  foreground=[("selected", "#1756a9"), ("!selected", "#34445b")])
        style.configure("Treeview", rowheight=28, background="#ffffff", fieldbackground="#ffffff",
                        foreground="#182235", bordercolor="#d5deea")
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"), padding=7,
                        background="#dce5f1", foreground="#233955")
        style.map("Treeview", background=[("selected", "#cfe2ff")],
                  foreground=[("selected", "#102b50")])
        self.rec = InputRecorder()
        self.id_len = tk.IntVar(value=ID_DEFAULT_LEN)
        self._train_emb = None
        self._live_emb = None
        self.afk_manual = False
        self._afk_auto_now = False
        self.afk_samples = deque(maxlen=40)     # (time, was_afk) once per second
        self._last_afk_sample = 0.0
        self.afk_skipped = 0
        self.afk_auto = tk.BooleanVar(value=True)
        self.track_keys = tk.BooleanVar(value=True)
        self.srv_cfg = load_server_cfg()
        self.srv = {"text": "Server: not set up (optional)", "fg": "#888", "ok": None, "dirty": False}
        self._wake_thread = None
        self._srv_win = None
        self.afk_secs = tk.IntVar(value=AFK_IDLE_SECS)
        self.afk_key_var = tk.StringVar(value=AFK_DEFAULT_KEY)
        self.banned_names = set()
        self.weights = None
        self.key_idf = None
        self._key_t = 0.0
        self._det_key_t = 0.0
        self.live_slices = deque(maxlen=12)
        self._kb_scores = {}
        self.mismatch = 0
        self.was_match = False
        self.mode = None           # None | 'train' | 'detect'
        self.t0 = 0.0
        self.next_eval = 0.0
        self.profile = None
        self.new_windows = 0
        self.skipped = 0
        self.det_profiles = []
        self.ema = {}
        self.recent = deque(maxlen=8)
        self.det_vars = {}
        self.det_rows = {}
        self._last_counts = (0, 0, time.perf_counter())
        # In-game integrations (optional; nothing runs until the user starts an integration).
        self.use_tags = tk.BooleanVar(value=False)   # Detect: compare only against same-tag windows
        self.integ = IntegrationManager(session_running=lambda: self.mode is not None)
        self.ctx_samples = deque(maxlen=60)          # (time, tag, rule_applies) once per second
        self._last_ctx_sample = 0.0
        self._afk_rule_now = False
        self._afk_rule_name = None
        self._cur_tag = ""
        self._integ_vals_shown = None

        top = ttk.Frame(self, padding=(14, 12, 14, 0))
        top.pack(fill="x")
        ttk.Label(top, text="Play Fingerprint", font=("TkDefaultFont", 16, "bold")).pack(side="left")
        self.rec_label = tk.Label(top, text="", fg="#d11", font=("TkDefaultFont", 11, "bold"))
        self.rec_label.pack(side="right")
        ttk.Button(top, text="Class guide", command=self.show_guide).pack(side="right", padx=8)
        # Keep server settings in the always-visible header so users can find it
        # even when the main window is short or the status row is below the fold.
        ttk.Button(top, text="Server...", command=self.open_server_dialog).pack(side="right", padx=8)
        spin = ttk.Spinbox(top, from_=16, to=ID_MAX_LEN, increment=16, width=4,
                           textvariable=self.id_len, command=self.refresh_ids)
        spin.pack(side="right")
        spin.bind("<Return>", self.refresh_ids)
        spin.bind("<FocusOut>", self.refresh_ids)
        ttk.Label(top, text="ID length").pack(side="right", padx=(8, 4))

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=12, pady=10)
        self.train_tab = ttk.Frame(self.nb, padding=14)
        self.detect_tab = ttk.Frame(self.nb, padding=14)
        self.nb.add(self.train_tab, text="  Train  ")
        self.nb.add(self.detect_tab, text="  Detect  ")
        self.screen_tab = ttk.Frame(self.nb, padding=14)
        self.nb.add(self.screen_tab, text="  Screen (anti-cheat)  ")
        self.keys_tab = ttk.Frame(self.nb, padding=14)
        self.nb.add(self.keys_tab, text="  Keys  ")
        self.compare_tab = ttk.Frame(self.nb, padding=14)
        self.nb.add(self.compare_tab, text="  Compare  ")
        self.resource_tab = ttk.Frame(self.nb, padding=14)
        self.nb.add(self.resource_tab, text="  Resources  ")
        self.integ_tab = ttk.Frame(self.nb, padding=14)
        self.nb.add(self.integ_tab, text="  Integrations  ")
        self._build_train()
        self._build_detect()
        self._build_screen()
        self._build_keys()
        self._build_compare()
        self._build_resource_monitor()
        self._build_integrations()

        ttk.Label(self, text=f"Hotkey: {HOTKEY.upper()} starts/stops the current tab - works while your game has focus.",
                  foreground="#666").pack(pady=(0, 4))
        srow = ttk.Frame(self)
        srow.pack(pady=(0, 10))
        self.srv_label = tk.Label(srow, text=self.srv["text"], fg=self.srv["fg"])
        self.srv_label.pack(side="left")
        self.refresh_profiles()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._wake_on_start()
        self._resource_init_error = None
        if psutil is not None:
            try:
                self._process = psutil.Process(os.getpid())
                self._process.cpu_percent(interval=None)  # prime non-blocking CPU measurement
            except Exception as _resource_error:
                self._process = None
                self._resource_init_error = f"{type(_resource_error).__name__}: {_resource_error}"
        else:
            self._process = None
            self._resource_init_error = PSUTIL_IMPORT_ERROR or "psutil could not be imported"
        self.after(1000, self.update_resource_monitor)
        self.after(1000, self.update_integrations_ui)
        self.after(250, self.tick)

    def _build_resource_monitor(self):
        """Show resource usage for this Play Fingerprint process, not the whole PC."""
        t = self.resource_tab
        ttk.Label(t, text="Application resource monitor", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(t, text="Measures Play Fingerprint itself (CPU and RAM), not total system usage.",
                  foreground="#52647d", wraplength=850).pack(anchor="w", pady=(4, 14))
        self.resource_values = {}
        self.resource_bars = {}
        for key, title, maximum in (("cpu", "CPU usage", 100), ("ram", "RAM used", 100)):
            card = ttk.LabelFrame(t, text=title, padding=12)
            card.pack(fill="x", pady=7)
            value = ttk.Label(card, text="Starting monitor…", font=("Segoe UI", 14, "bold"))
            value.pack(anchor="w")
            bar = ttk.Progressbar(card, maximum=maximum, mode="determinate")
            bar.pack(fill="x", pady=(8, 3))
            detail = ttk.Label(card, text="")
            detail.pack(anchor="w")
            self.resource_values[key] = (value, detail)
            self.resource_bars[key] = bar
        self.resource_note = ttk.Label(t, text="", foreground="#7a4b00", wraplength=850)
        self.resource_note.pack(anchor="w", pady=(12, 0))
        if psutil is None:
            self.resource_note.config(text=(
                "psutil could not be imported by this Python interpreter. "
                f"Interpreter: {sys.executable}. Install into this exact interpreter with: "
                f'"{sys.executable}" -m pip install --upgrade psutil. '
                f"Details: {PSUTIL_IMPORT_ERROR or 'unknown import error'}"
            ))

    def update_resource_monitor(self):
        """Refresh process-only CPU and resident-memory figures without blocking the UI."""
        try:
            if self._process is None:
                for key in ("cpu", "ram"):
                    value, detail = self.resource_values[key]
                    value.config(text="Unavailable")
                    if psutil is None:
                        detail.config(text=f"Import failed: {self._resource_init_error or PSUTIL_IMPORT_ERROR or 'unknown error'}")
                    else:
                        detail.config(text=f"Process initialization failed: {self._resource_init_error or 'unknown error'}")
                    self.resource_bars[key]["value"] = 0
            else:
                cpu = max(0.0, self._process.cpu_percent(interval=None))
                mem = self._process.memory_info().rss
                total = psutil.virtual_memory().total
                ram_mb = mem / (1024 * 1024)
                ram_pct = (mem / total * 100) if total else 0.0
                cpu_value, cpu_detail = self.resource_values["cpu"]
                cpu_value.config(text=f"{cpu:.1f}% CPU")
                cpu_detail.config(text="Process CPU usage; may exceed 100% when using multiple cores.")
                self.resource_bars["cpu"]["value"] = min(100, cpu)
                ram_value, ram_detail = self.resource_values["ram"]
                ram_value.config(text=f"{ram_mb:.1f} MB RAM")
                ram_detail.config(text=f"{ram_pct:.3f}% of system RAM ({mem:,} bytes resident memory)")
                self.resource_bars["ram"]["value"] = min(100, ram_pct)
                if self.resource_note:
                    self.resource_note.config(text="Updates every second. CPU bar is capped at 100%; the numeric reading can exceed 100% on multi-core systems.")
        except Exception as e:
            self.resource_note.config(text=f"Resource monitor error: {e}")
        finally:
            if self.winfo_exists():
                self.after(1000, self.update_resource_monitor)

    # ---- train tab ---------------------------------------------------------
    def _build_train(self):
        t = self.train_tab
        ttk.Label(t, text="Choose which fingerprint to build or refine:").pack(anchor="w")
        self.train_sel = tk.StringVar(value=NEW_LABEL)
        self.train_combo = ttk.Combobox(t, textvariable=self.train_sel, state="readonly")
        self.train_combo.pack(fill="x", pady=(4, 8))
        self.train_combo.bind("<<ComboboxSelected>>", lambda e: self._train_sel_changed())

        row = ttk.Frame(t)
        row.pack(fill="x")
        ttk.Label(row, text="New name:").pack(side="left")
        self.new_name = tk.StringVar()
        self.name_entry = ttk.Entry(row, textvariable=self.new_name)
        self.name_entry.pack(side="left", fill="x", expand=True, padx=(8, 0))

        self.train_info = ttk.Label(t, text="", foreground="#555")
        self.train_info.pack(anchor="w", pady=(8, 0))

        idrow = ttk.Frame(t)
        idrow.pack(fill="x", pady=(6, 0))
        ttk.Label(idrow, text="Fingerprint ID:").pack(side="left")
        self.train_id_var = tk.StringVar()
        ttk.Entry(idrow, textvariable=self.train_id_var, state="readonly",
                  font=("TkFixedFont", 9)).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(idrow, text="Copy", width=6,
                   command=lambda: self.copy_text(self.train_id_var.get())).pack(side="left")

        self.train_btn = ttk.Button(t, text=f"Start training ({HOTKEY.upper()})", command=self.toggle_train)
        self.train_btn.pack(fill="x", pady=14, ipady=6)

        box = ttk.LabelFrame(t, text="Live", padding=10)
        box.pack(fill="x")
        self.tv = {k: tk.StringVar(value="-") for k in ("time", "wins", "mouse", "keys", "class", "ads", "tag")}
        for label, key in (("Elapsed", "time"), ("Windows captured", "wins"),
                           ("Mouse events/s", "mouse"), ("Key presses/s", "keys"),
                           ("Class so far", "class"), ("ADS so far", "ads"),
                           ("Context tag", "tag")):
            r = ttk.Frame(box)
            r.pack(fill="x", pady=1)
            ttk.Label(r, text=label, width=20).pack(side="left")
            ttk.Label(r, textvariable=self.tv[key]).pack(side="left")
        ttk.Label(box, text="Training amount").pack(anchor="w", pady=(8, 2))
        self.train_bar = ttk.Progressbar(box, maximum=100)
        self.train_bar.pack(fill="x")
        ttk.Checkbutton(box, text="Track individual keys (per-key usage & keybinds)",
                        variable=self.track_keys).pack(anchor="w", pady=(8, 0))

        afk = ttk.LabelFrame(t, text="AFK handling (training data cleanup)", padding=10)
        afk.pack(fill="x", pady=(10, 0))
        self.afk_status = tk.Label(afk, text="Not recording", fg="#888", font=("TkDefaultFont", 10, "bold"))
        self.afk_status.pack(anchor="w")
        row = ttk.Frame(afk)
        row.pack(fill="x", pady=(6, 0))
        self.afk_btn = ttk.Button(row, text="Mark me AFK", command=self.toggle_afk_manual, state="disabled")
        self.afk_btn.pack(side="left")
        ttk.Label(row, text="   Key:").pack(side="left")
        kc = ttk.Combobox(row, textvariable=self.afk_key_var, values=AFK_KEYS, state="readonly", width=12)
        kc.pack(side="left", padx=4)
        kc.bind("<<ComboboxSelected>>", lambda e: self._set_afk_key())
        row2 = ttk.Frame(afk)
        row2.pack(fill="x", pady=(6, 0))
        ttk.Checkbutton(row2, text="Auto-detect idle after", variable=self.afk_auto).pack(side="left")
        ttk.Spinbox(row2, from_=3, to=15, width=3, textvariable=self.afk_secs).pack(side="left", padx=4)
        ttk.Label(row2, text="seconds").pack(side="left")
        ttk.Label(afk, text="Windows containing AFK time are left out of the fingerprint. Training only - "
                            "the AFK key is never recorded. Game-state AFK rules (Integrations tab) count too.",
                  foreground="#666", wraplength=560, justify="left").pack(anchor="w", pady=(6, 0))
        ttk.Label(t, text="Play normally for 10+ minutes (more sessions = a sturdier fingerprint).\n"
                          "Re-select the same fingerprint later to keep refining it.",
                  foreground="#666", justify="left").pack(anchor="w", pady=10)

    def _train_sel_changed(self):
        new = self.train_sel.get() == NEW_LABEL
        self.name_entry.configure(state="normal" if new else "disabled")
        if not new:
            try:
                p = Profile.load(profile_path(self.train_sel.get()))
                tl = tags_line(p)
                self.train_info.config(text=f"K+M {p.classify()['code']} · {p.classify().get('playstyle', 'Not enough data')}  |  {len(p.windows)} windows, "
                                            f"{p.seconds/60:.1f} min recorded, "
                                            f"{os.path.getsize(profile_path(p.name))/1024:.0f} KB  |  "
                                            f"{stability_text(p)}\nADS: {p.classify()['ads_desc']}"
                                            + (f"\n{tl}" if tl else ""))
                self._train_emb = p.embedding()
                self.show_train_id()
            except Exception as e:
                self.train_info.config(text=f"Could not read: {e}")
        else:
            self.train_info.config(text="")
            self._train_emb = None
            self.show_train_id()

    # ---- detect tab --------------------------------------------------------
    def _build_detect(self):
        t = self.detect_tab
        head = ttk.Frame(t)
        head.pack(fill="x")
        ttk.Label(head, text="Fingerprints to compare against:").pack(side="left")
        ttk.Button(head, text="Import...", command=self.import_profile).pack(side="right")
        ttk.Button(head, text="Refresh", command=self.refresh_profiles).pack(side="right", padx=6)

        self.det_list = ttk.Frame(t)
        self.det_list.pack(fill="x", pady=(6, 8))

        self.detect_btn = ttk.Button(t, text=f"Start detecting ({HOTKEY.upper()})", command=self.toggle_detect)
        self.detect_btn.pack(fill="x", pady=6, ipady=6)

        srow = ttk.Frame(t)
        srow.pack(fill="x", pady=(2, 8))
        ttk.Label(srow, text="Strictness").pack(side="left")
        ttk.Label(srow, text="strict").pack(side="left", padx=(10, 2))
        self.strict = tk.DoubleVar(value=1.8)
        ttk.Scale(srow, from_=1.2, to=3.0, variable=self.strict, orient="horizontal").pack(
            side="left", fill="x", expand=True)
        ttk.Label(srow, text="lenient").pack(side="left", padx=(2, 0))
        ttk.Checkbutton(t, text=f"Compare only against windows with the current context tag "
                                f"(needs {TAG_MIN_WINDOWS}+ such windows, else whole fingerprint)",
                        variable=self.use_tags).pack(anchor="w", pady=(0, 6))

        self.verdict = tk.Label(t, text="Not running", font=("TkDefaultFont", 20, "bold"), fg="#888",
                                wraplength=480, pady=12)
        self.verdict.pack(fill="x")
        self.live_class = tk.Label(t, text="", fg="#335", wraplength=480, justify="center")
        self.live_class.pack(fill="x", pady=(0, 4))
        self.live_keys = tk.Label(t, text="", fg="#335", wraplength=480, justify="center")
        self.live_keys.pack(fill="x", pady=(0, 4))
        self.flag_label = tk.Label(t, text="", fg="#c00", wraplength=480, justify="center")
        self.flag_label.pack(fill="x", pady=(0, 4))
        lrow = ttk.Frame(t)
        lrow.pack(fill="x", pady=(0, 8))
        ttk.Label(lrow, text="Live ID:").pack(side="left")
        self.live_id_var = tk.StringVar()
        ttk.Entry(lrow, textvariable=self.live_id_var, state="readonly",
                  font=("TkFixedFont", 9)).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(lrow, text="Copy", width=6,
                   command=lambda: self.copy_text(self.live_id_var.get())).pack(side="left")
        self.results = ttk.Frame(t)
        self.results.pack(fill="x")
        self.det_status = ttk.Label(t, text="", foreground="#666", wraplength=900, justify="left")
        self.det_status.pack(anchor="w", pady=(10, 0))

    # ---- integrations tab --------------------------------------------------
    def _build_integrations(self):
        t = self.integ_tab
        ttk.Label(t, text="In-game integrations", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(t, text=INTEG_NOTICE, foreground="#7a4b00", wraplength=1000, justify="left").pack(
            anchor="w", pady=(4, 8))
        bar = ttk.Frame(t)
        bar.pack(fill="x")
        ttk.Button(bar, text="Start selected", command=self.integ_start_sel).pack(side="left")
        ttk.Button(bar, text="Stop selected", command=self.integ_stop_sel).pack(side="left", padx=6)
        ttk.Button(bar, text="Stop all", command=self.integ_stop_all).pack(side="left")
        self.integ_example_btn = ttk.Button(bar, text="Write example config", command=self.integ_write_example)
        self.integ_example_btn.pack(side="right")
        self.integ_reload_btn = ttk.Button(bar, text="Reload config", command=self.integ_reload)
        self.integ_reload_btn.pack(side="right", padx=6)

        cols = ("kind", "status", "age", "msgs", "error")
        self.integ_tree = ttk.Treeview(t, columns=cols, height=6, selectmode="browse")
        for col, title, w, stretch in (("#0", "Integration", 150, False), ("kind", "Source", 230, False),
                                       ("status", "Status", 80, False), ("age", "Last update", 90, False),
                                       ("msgs", "Messages", 110, False), ("error", "Last error", 330, True)):
            self.integ_tree.heading(col, text=title)
            self.integ_tree.column(col, width=w, stretch=stretch)
        self.integ_tree.tag_configure("running", foreground="#1a8a3a")
        self.integ_tree.tag_configure("error", foreground="#c00")
        self.integ_tree.tag_configure("off", foreground="#888")
        self.integ_tree.pack(fill="x", pady=(8, 0))
        self.integ_tree.bind("<<TreeviewSelect>>", lambda _e: self._refresh_integ())

        self.integ_ctx = ttk.Label(t, text="", font=("Segoe UI", 10, "bold"), wraplength=1000, justify="left")
        self.integ_ctx.pack(anchor="w", pady=(10, 2))
        ttk.Label(t, text="Live values of the selected integration (memory only - never saved or uploaded):",
                  foreground="#52647d").pack(anchor="w")
        self.integ_vals = tk.Text(t, height=9, state="disabled", wrap="none", font=("Consolas", 9),
                                  background="#ffffff", relief="flat", borderwidth=1)
        self.integ_vals.pack(fill="both", expand=True, pady=(2, 6))
        self.integ_msg = ttk.Label(t, text="", foreground="#555", wraplength=1000, justify="left")
        self.integ_msg.pack(anchor="w")

    def _integ_selected(self):
        sel = self.integ_tree.selection()
        return sel[0] if sel else None

    def integ_start_sel(self):
        n = self._integ_selected()
        if not n:
            messagebox.showinfo("Select an integration", "Select an integration in the list first.")
            return
        if not self.integ.start(n):
            messagebox.showwarning("Could not start", f"{n}: {self.integ.sources[n].last_error or 'failed'}")
        self._refresh_integ()

    def integ_stop_sel(self):
        n = self._integ_selected()
        if n:
            self.integ.stop(n)
        self._refresh_integ()

    def integ_stop_all(self):
        self.integ.stop_all()
        self._refresh_integ()

    def integ_reload(self):
        ok, msg = self.integ.load()
        if not ok:
            messagebox.showinfo("Reload blocked", msg)
        self.integ_tree.delete(*self.integ_tree.get_children())
        self._refresh_integ()

    def integ_write_example(self):
        if os.path.exists(self.integ.path):
            messagebox.showinfo("Already exists", f"{self.integ.path}\nalready exists - edit it directly.")
            return
        try:
            os.makedirs(APP_DATA_DIR, exist_ok=True)
            with open(self.integ.path, "w", encoding="utf-8") as fh:
                fh.write(example_config_text() + "\n")
            try:
                os.chmod(self.integ.path, 0o600)          # may hold a shared token
            except OSError:
                pass
            messagebox.showinfo("Example written", f"{self.integ.path}\n\nEverything in it is disabled and "
                                                   "uses placeholders. Edit it, set \"enabled\": true on what "
                                                   "you want, then press Reload config.")
        except Exception as e:
            messagebox.showerror("Could not write", str(e))

    def _refresh_integ(self):
        """Refresh the integrations tab. Never raises."""
        try:
            self.integ_reload_btn.config(state="disabled" if self.mode else "normal")
            rows = self.integ.rows()
            names = {r["name"] for r in rows}
            have = set(self.integ_tree.get_children())
            for iid in have - names:
                self.integ_tree.delete(iid)
            for r in rows:
                age = "never" if r["age"] is None else f"{r['age']:.1f}s ago"
                status = r["status"] if r["enabled"] else "disabled"
                err = ""
                if r["error"]:
                    err = r["error"] + ("" if r["error_age"] is None else f"  ({r['error_age']:.0f}s ago)")
                vals = (f"{r['kind']}  {r['detail']}".strip(), status, age,
                        f"{r['count']} ok / {r['bad']} bad", err)
                tag = ("off",) if not r["enabled"] else (("running",) if r["status"] == "running"
                                                         else (("error",) if r["status"] == "error" else ()))
                if r["name"] in have:
                    self.integ_tree.item(r["name"], values=vals, tags=tag)
                else:
                    self.integ_tree.insert("", "end", iid=r["name"], text=r["name"], values=vals, tags=tag)
            applies, rule = self.integ.afk_check()
            tag = self.integ.current_tag()
            self.integ_ctx.config(text=f"Game-state context now:  tag = {tag or '(none)'}   |   AFK rule: "
                                       f"{('APPLIES - ' + rule) if applies else 'none applies'}")
            sel = self._integ_selected()
            row = next((r for r in rows if r["name"] == sel), None)
            if row is None:
                text = "(select an integration above)"
            elif not row["values"]:
                text = "(no fresh values - not started, nothing received yet, or values went stale)"
            else:
                text = "\n".join(f"{k} = {v!r}" for k, v in sorted(row["values"].items())[:INTEG_MAX_KEYS])
            if text != self._integ_vals_shown:
                self._integ_vals_shown = text
                self.integ_vals.config(state="normal")
                self.integ_vals.delete("1.0", "end")
                self.integ_vals.insert("end", text)
                self.integ_vals.config(state="disabled")
            note = f"Config: {self.integ.path}" + ("" if os.path.exists(self.integ.path)
                                                   else "  (not found - nothing is configured)")
            if self.integ.warnings:
                note += "\nWarnings: " + "; ".join(self.integ.warnings[:5])
            if self.mode:
                note += "\nA session is running: config reload is blocked until it stops."
            self.integ_msg.config(text=note)
        except Exception as e:
            print("integrations ui error:", e)

    def update_integrations_ui(self):
        try:
            self._refresh_integ()
        finally:
            if self.winfo_exists():
                self.after(1000, self.update_integrations_ui)

# ---- screen (anti-cheat) tab -------------------------------------------
    def _build_screen(self):
        t = self.screen_tab
        self.screen_report = []
        ttk.Label(t, text="Ban list & ban-evasion screening",
                  font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        ttk.Label(t, text="Flags are leads for human review - never auto-ban on a fingerprint match alone.",
                  foreground="#a60").pack(anchor="w", pady=(2, 8))

        self.ban_tree = ttk.Treeview(t, columns=("code", "status", "reason"), height=6,
                                     selectmode="extended")
        for col, text, w in (("#0", "Account", 150), ("code", "Class", 70),
                             ("status", "Status", 70), ("reason", "Ban reason", 230)):
            self.ban_tree.heading(col, text=text)
            self.ban_tree.column(col, width=w, stretch=(col == "reason"))
        self.ban_tree.tag_configure("banned", foreground="#c00")
        self.ban_tree.pack(fill="x")

        brow = ttk.Frame(t)
        brow.pack(fill="x", pady=6)
        ttk.Button(brow, text="Ban selected...", command=self.ban_selected).pack(side="left")
        ttk.Button(brow, text="Unban selected", command=self.unban_selected).pack(side="left", padx=6)

        srow = ttk.Frame(t)
        srow.pack(fill="x", pady=(4, 6))
        ttk.Label(srow, text="Strictness").pack(side="left")
        ttk.Label(srow, text="strict").pack(side="left", padx=(10, 2))
        ttk.Scale(srow, from_=1.2, to=3.0, variable=self.strict, orient="horizontal").pack(
            side="left", fill="x", expand=True)
        ttk.Label(srow, text="lenient").pack(side="left", padx=(2, 0))

        arow = ttk.Frame(t)
        arow.pack(fill="x", pady=4)
        ttk.Button(arow, text="Screen all clean accounts", command=self.run_screen).pack(side="left")
        ttk.Button(arow, text="Screen selected",
                   command=lambda: self.run_screen(only_selected=True)).pack(side="left", padx=6)
        ttk.Button(arow, text="Export report...", command=self.export_report).pack(side="right")

        self.screen_tree = ttk.Treeview(t, columns=("banned", "sim", "pop", "kb", "flag"), height=8)
        for col, text, w in (("#0", "Account", 130), ("banned", "Closest banned", 130),
                             ("sim", "Similarity", 80), ("pop", "More alike than", 100),
                             ("kb", "Keybinds", 70), ("flag", "Flag", 60)):
            self.screen_tree.heading(col, text=text)
            self.screen_tree.column(col, width=w, stretch=False)
        self.screen_tree.tag_configure("high", foreground="#c00")
        self.screen_tree.tag_configure("review", foreground="#d77a00")
        self.screen_tree.pack(fill="both", expand=True, pady=(6, 0))
        self.screen_status = ttk.Label(t, text="", foreground="#666", wraplength=600, justify="left")
        self.screen_status.pack(anchor="w", pady=(8, 0))

    def refresh_ban_tree(self):
        ban = load_banlist()
        self.ban_tree.delete(*self.ban_tree.get_children())
        for n in list_profiles():
            code, _ = read_summary(profile_path(n))
            b = ban.get(n)
            self.ban_tree.insert("", "end", iid=n, text=n,
                                 values=(code, "BANNED" if b else "clean", (b or {}).get("reason", "")),
                                 tags=("banned",) if b else ())

    def ban_selected(self):
        sel = self.ban_tree.selection()
        if not sel:
            messagebox.showinfo("Select accounts", "Select one or more accounts in the list first.")
            return
        reason = simpledialog.askstring("Ban reason", "Reason / ticket reference:", parent=self)
        if reason is None:
            return
        ban = load_banlist()
        for n in sel:
            ban[n] = {"reason": reason, "date": datetime.now().isoformat(timespec="seconds")}
        save_banlist(ban)
        if self.srv_cfg["url"] and self.srv_cfg["key"]:
            for n in sel:
                self._srv_bg(lambda n=n, e=ban[n]: self._ensure_awake() and server_call(
                    self.srv_cfg, "PUT", "/api/banlist/" + _q(n), json.dumps(e).encode(),
                    content_type="application/json"))
        self.refresh_profiles()

    def unban_selected(self):
        sel = self.ban_tree.selection()
        if not sel:
            return
        ban = load_banlist()
        for n in sel:
            ban.pop(n, None)
        save_banlist(ban)
        if self.srv_cfg["url"] and self.srv_cfg["key"]:
            for n in sel:
                self._srv_bg(lambda n=n: self._ensure_awake() and server_call(
                    self.srv_cfg, "DELETE", "/api/banlist/" + _q(n)))
        self.refresh_profiles()

    def run_screen(self, only_selected=False):
        ban = load_banlist()
        profiles = {}
        for n in list_profiles():
            try:
                p = Profile.load(profile_path(n))
                p.name = n
                if len(p.windows) >= 5:
                    profiles[n] = p
            except Exception:
                pass
        banned = {n: p for n, p in profiles.items() if n in ban}
        clean = {n: p for n, p in profiles.items() if n not in ban}
        if not banned:
            messagebox.showinfo("No banned fingerprints",
                                "Ban at least one account (with 5+ windows of data) first.")
            return
        sel = set(self.ban_tree.selection())
        cands = [p for n, p in clean.items() if not only_selected or n in sel]
        if not cands:
            messagebox.showinfo("Nothing to screen", "No clean accounts to screen"
                                + (" - select some first." if only_selected else "."))
            return
        thr = float(self.strict.get())
        w = feature_weights(list(profiles.values()))
        idf = key_idf([p.keys for p in profiles.values() if p.keys])
        self.screen_tree.delete(*self.screen_tree.get_children())
        self.screen_report = []
        high = review = 0
        for c in cands:
            rows = screen_player(c, banned, [p for n, p in clean.items() if n != c.name], thr, w, idf)
            if not rows:
                continue
            top = rows[0]
            high += top["flag"] == "HIGH"
            review += top["flag"] == "REVIEW"
            pop = "n/a" if top["population_rank"] is None else f"{top['population_rank']:.0f}% of players"
            kbs = "n/a" if top["key_sim"] is None else f"{top['key_sim'] * 100:.0f}%"
            self.screen_tree.insert("", "end", text=c.name,
                                    values=(top["banned"], f"{top['similarity']:.0f}%", pop, kbs, top["flag"]),
                                    tags=(top["flag"].lower(),))
            self.screen_report.append({"candidate": c.name, "flag": top["flag"], "matches": rows})
        pop_note = ""
        if len(clean) < 6:
            pop_note = "  Fewer than 5 other clean players - scores are uncalibrated, treat with extra caution."
        self.screen_status.config(
            text=f"Screened {len(self.screen_report)} account(s): {high} HIGH, {review} REVIEW.{pop_note}"
                 + ("  Feature weighting on." if w is not None
                    else "  Feature weighting off (needs 3+ fingerprints)."))

    def export_report(self):
        if not self.screen_report:
            messagebox.showinfo("No report", "Run a screen first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".json", initialfile="screening_report.json",
                                            filetypes=[("JSON", "*.json")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"generated": datetime.now().isoformat(timespec="seconds"),
                       "strictness": float(self.strict.get()),
                       "note": "Fingerprint matches are leads for human review, not proof of identity.",
                       "results": self.screen_report}, fh, indent=1)
        messagebox.showinfo("Exported", path)

    # ---- fingerprint ID helpers -------------------------------------------
    def _idlen(self):
        try:
            v = int(self.id_len.get())
        except (tk.TclError, ValueError):
            v = ID_DEFAULT_LEN
        return max(16, min(ID_MAX_LEN, v))

    def _id_text(self, emb):
        if emb is None:
            return "(not enough data yet)"
        return format_id(fingerprint_id(emb), self._idlen())

    def show_train_id(self):
        self.train_id_var.set(self._id_text(self._train_emb))

    def refresh_ids(self, *_):
        self.id_len.set(self._idlen())
        self.show_train_id()
        if self._live_emb is not None:
            self.live_id_var.set(self._id_text(self._live_emb))
        if self.mode is None:
            self.refresh_profiles()

    def copy_text(self, text):
        if text and not text.startswith("("):
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update()

    # ---- class guide -------------------------------------------------------
    def show_guide(self):
        """Open the full, scrollable reference for every compact class-code segment."""
        w = tk.Toplevel(self)
        w.title("Play Fingerprint — Classification Guide")
        w.geometry("920x760")
        w.minsize(720, 560)
        w.transient(self)

        outer = ttk.Frame(w, padding=0)
        outer.pack(fill="both", expand=True)
        header = tk.Frame(outer, bg="#17365e", padx=22, pady=18)
        header.pack(fill="x")
        tk.Label(header, text="CLASSIFICATION GUIDE", bg="#17365e", fg="#ffffff",
                 font=("Segoe UI", 17, "bold")).pack(anchor="w")
        tk.Label(header, text="Understand every segment of a Play Fingerprint class code",
                 bg="#17365e", fg="#dbeafe", font=("Segoe UI", 10)).pack(anchor="w", pady=(4, 0))

        area = ttk.Frame(outer)
        area.pack(fill="both", expand=True)
        canvas = tk.Canvas(area, highlightthickness=0, bg="#eef2f7")
        scrollbar = ttk.Scrollbar(area, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        content = ttk.Frame(canvas, padding=(18, 16, 18, 22))
        win_id = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win_id, width=e.width))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        w.bind("<Destroy>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        def section(title, subtitle, rows, footnote=None):
            """Render each classification as a visual chart rather than a text-heavy table."""
            card = ttk.LabelFrame(content, text=title, padding=(13, 10, 13, 12))
            card.pack(fill="x", pady=(0, 12))
            ttk.Label(card, text=subtitle, wraplength=790, justify="left").pack(anchor="w", pady=(0, 9))

            # k/m use a matrix: columns are pace levels; rows are style levels.
            if len(rows) == 20 and rows[0][0][:1] in ("k", "m"):
                prefix = rows[0][0][0]
                pace_names = KB_PACE_NAMES if prefix == "k" else MS_PACE_NAMES
                style_names = KB_STYLE_NAMES if prefix == "k" else MS_STYLE_NAMES
                grid = tk.Frame(card, bg="#cbd8e8", padx=1, pady=1)
                grid.pack(fill="x", expand=True)
                tk.Label(grid, text="STYLE ↓  /  PACE →", bg="#17365e", fg="white",
                         font=("Segoe UI", 9, "bold"), padx=8, pady=9).grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
                for pi, pace_name in enumerate(pace_names, start=1):
                    tk.Label(grid, text=pace_name, bg="#17365e", fg="white",
                             font=("Segoe UI", 9, "bold"), padx=7, pady=9, wraplength=115,
                             justify="center").grid(row=0, column=pi, sticky="nsew", padx=1, pady=1)
                # Code numbering is pace-major: code = pace_index * number_of_styles + style_index + 1.
                for si, style_name in enumerate(style_names):
                    tk.Label(grid, text=style_name, bg="#dbeafe", fg="#12345a",
                             font=("Segoe UI", 9, "bold"), padx=8, pady=9,
                             wraplength=130, justify="left").grid(row=si+1, column=0, sticky="nsew", padx=1, pady=1)
                    for pi, _pace_name in enumerate(pace_names):
                        number = pi * len(style_names) + si + 1
                        code = f"{prefix}{number:02d}"
                        bg = "#ffffff" if (pi + si) % 2 == 0 else "#f1f5f9"
                        tk.Label(grid, text=code, bg=bg, fg="#12345a",
                                 font=("Consolas", 11, "bold"), padx=10, pady=12,
                                 relief="flat", anchor="center").grid(row=si+1, column=pi+1,
                                                                       sticky="nsew", padx=1, pady=1)
                for col in range(len(pace_names) + 1):
                    grid.columnconfigure(col, weight=1, uniform=f"{prefix}_col")
                for row in range(len(style_names) + 1):
                    grid.rowconfigure(row, weight=1)
            else:
                # Compact chart: each class is a colored band with its code, label, and range.
                chart = tk.Frame(card, bg="#dbe4f0", padx=1, pady=1)
                chart.pack(fill="x", expand=True)
                chart.columnconfigure(0, weight=0)
                chart.columnconfigure(1, weight=2)
                chart.columnconfigure(2, weight=3)
                for i, (code, meaning, ranges) in enumerate(rows):
                    bg = "#ffffff" if i % 2 == 0 else "#f1f5f9"
                    code_bg = "#dbeafe" if not code.endswith("??") else "#e5e7eb"
                    tk.Label(chart, text=code, bg=code_bg, fg="#12345a", font=("Consolas", 10, "bold"),
                             padx=10, pady=7, width=9, anchor="center").grid(row=i, column=0, sticky="nsew", padx=1, pady=1)
                    tk.Label(chart, text=meaning, bg=bg, fg="#243247", font=("Segoe UI", 9, "bold"),
                             padx=10, pady=7, anchor="w").grid(row=i, column=1, sticky="nsew", padx=1, pady=1)
                    tk.Label(chart, text=ranges, bg=bg, fg="#526176", padx=10, pady=7, anchor="w",
                             wraplength=420, justify="left").grid(row=i, column=2, sticky="nsew", padx=1, pady=1)
            if footnote:
                ttk.Label(card, text=footnote, foreground="#596579", wraplength=790,
                          justify="left").pack(anchor="w", pady=(8, 0))

        ttk.Label(content, text="A class code is a compact summary of measured input habits. It is not the full fingerprint ID and it does not prove who is playing.",
                  wraplength=800, justify="left").pack(anchor="w", pady=(0, 10))
        code_card = ttk.LabelFrame(content, text="Example code", padding=13)
        code_card.pack(fill="x", pady=(0, 12))
        tk.Label(code_card, text="k08m14a03cr04p05s03ads02ko04cv01ch03", bg="#dbeafe", fg="#12345a",
                 font=("Consolas", 14, "bold"), padx=12, pady=10).pack(anchor="w", fill="x")
        ttk.Label(code_card, text="Read from left to right: k, m, a, cr, p, s, ads, ko, cv, ch. Each segment has its own scale. A ?? segment means that feature could not be classified from the available data.",
                  wraplength=800, justify="left").pack(anchor="w", pady=(8, 0))

        # Keyboard and mouse codes use a 4-by-5 grid: pace level * 5 + style level + 1.
        def grid_rows(prefix, pace_names, style_names):
            rows = []
            for pi, pace_name in enumerate(pace_names):
                for si, style_name in enumerate(style_names):
                    number = pi * len(style_names) + si + 1
                    rows.append((f"{prefix}{number:02d}", f"{pace_name} pace + {style_name.lower()} style",
                                 f"Pace: {pace_name}; style: {style_name}"))
            return rows

        section("1. Keyboard activity — k01 to k20",
                "Combines the rate of key presses with the typical duration a key is held. The code number is a grid cell, not a rank.",
                grid_rows("k", KB_PACE_NAMES, KB_STYLE_NAMES),
                "Pace thresholds (key presses/second): " + _range_text(KB_PACE_CUTS) + ". Median key-hold thresholds (seconds): " + _range_text(KB_STYLE_CUTS) + ".")
        section("2. Mouse movement — m01 to m20",
                "Combines average pointer speed while moving with mouse-stroke straightness. Straightness ranges from 0 to 1.",
                grid_rows("m", MS_PACE_NAMES, MS_STYLE_NAMES),
                "Speed thresholds (pixels/second): " + _range_text(MS_PACE_CUTS) + ". Straightness thresholds: " + _range_text(MS_STYLE_CUTS) + ". Results can vary with display scaling, sensitivity, and the game.")
        section("3. Aim-path proxy — a01 to a05",
                "Measures how straight recorded mouse paths tend to be. This is only a movement-shape proxy; it does not measure aim accuracy or skill.",
                [("a01", "Very winding paths", "straightness <= 0.25"),
                 ("a02", "Winding paths", "0.25 < straightness <= 0.45"),
                 ("a03", "Mixed straightness", "0.45 < straightness <= 0.65"),
                 ("a04", "Mostly direct paths", "0.65 < straightness <= 0.82"),
                 ("a05", "Very direct paths", "straightness > 0.82")])
        section("4. Click rate — cr01 to cr05",
                "Classifies the rate of recorded mouse clicks, in clicks per second.",
                [("cr01", "Very low", "<= 0.5 clicks/s"), ("cr02", "Low to moderate", "> 0.5 to 1.5 clicks/s"),
                 ("cr03", "Moderate", "> 1.5 to 3.0 clicks/s"), ("cr04", "High", "> 3.0 to 5.0 clicks/s"),
                 ("cr05", "Very high", "> 5.0 clicks/s")],
                "The rate is calculated over the recorded window, so idle time and the type of game activity can affect it.")
        section("5. Overall playstyle — p01 to p09",
                "A single best-match label selected by heuristic rules from the recorded keyboard and mouse features. It is not an independent hardware type.",
                [("p01", "ADS-focused", "Frequent right-click / aiming-time behavior"),
                 ("p02", "Click-intensive", "High mouse-click rate"),
                 ("p03", "Keyboard-led", "Behavior dominated by keyboard activity"),
                 ("p04", "Mouse-led", "More mouse-oriented behavior"),
                 ("p05", "Balanced keyboard + mouse", "Regular use of both inputs"),
                 ("p06", "High-input hybrid", "High keyboard and mouse activity"),
                 ("p07", "Precision / direct-aim", "Frequent, relatively straight mouse movements"),
                 ("p08", "Scroll-intensive", "Frequent scrolling"),
                 ("p09", "Low-intensity / mixed", "Lower-intensity or less distinctive behavior"),
                 ("p??", "Not enough data", "Not enough available features to assign a label")],
                "The app applies priority rules to choose one label. A different game or task can change the label for the same person.")
        section("6. Scrolling — s01 to s05",
                "Counts scroll events per second.",
                [("s01", "Little or no scrolling", "<= 0.05 events/s"), ("s02", "Occasional scrolling", "> 0.05 to 0.25 events/s"),
                 ("s03", "Regular scrolling", "> 0.25 to 0.75 events/s"), ("s04", "Frequent scrolling", "> 0.75 to 1.5 events/s"),
                 ("s05", "Very frequent scrolling", "> 1.5 events/s")])
        section("7. ADS / right-click — ads01 to ads05",
                "Measures the fraction of recorded time that the right mouse button is held. It does not know whether a particular game uses right-click to aim.",
                [("ads01", "Almost never held", "<= 1% of recorded time"), ("ads02", "Low hold share", "> 1% to 8%"),
                 ("ads03", "Moderate hold share", "> 8% to 20%"), ("ads04", "High hold share", "> 20% to 40%"),
                 ("ads05", "Very high hold share", "> 40%")])
        section("8. Keyboard overlap — ko01 to ko05",
                "Measures how often key presses overlap with other key presses, as a ratio.",
                [("ko01", "Very little overlap", "<= 0.02"), ("ko02", "Low overlap", "> 0.02 to 0.10"),
                 ("ko03", "Moderate overlap", "> 0.10 to 0.25"), ("ko04", "High overlap", "> 0.25 to 0.50"),
                 ("ko05", "Very high overlap", "> 0.50")],
                "Overlapping keys are common in games that use simultaneous movement and action inputs.")
        section("9. Keyboard timing variation — cv01 to cv05",
                "Measures variation in the intervals between keyboard events using a coefficient of variation. Larger values mean less consistent intervals.",
                [("cv01", "Very consistent", "<= 0.25"), ("cv02", "Fairly consistent", "> 0.25 to 0.50"),
                 ("cv03", "Moderately variable", "> 0.50 to 1.0"), ("cv04", "Highly variable", "> 1.0 to 2.0"),
                 ("cv05", "Very highly variable", "> 2.0")])
        section("10. Mouse-button hold duration — ch01 to ch05",
                "Uses the median duration of recorded mouse-button holds, in seconds.",
                [("ch01", "Very short holds", "<= 0.05 s"), ("ch02", "Short holds", "> 0.05 to 0.12 s"),
                 ("ch03", "Medium holds", "> 0.12 to 0.25 s"), ("ch04", "Long holds", "> 0.25 to 0.50 s"),
                 ("ch05", "Very long holds", "> 0.50 s")])

        caveats = ttk.LabelFrame(content, text="Important notes", padding=13)
        caveats.pack(fill="x", pady=(0, 12))
        notes = [
            "Class thresholds are hand-set heuristics, not population-calibrated identity boundaries.",
            "The code summarizes behavior; the full fingerprint uses many continuous features beyond these ten categories.",
            "Per-key usage and keybind comparisons are separate from this compact class code.",
            "This version records keyboard and mouse input. It does not currently classify gamepad-only or touchscreen input.",
            "A matching class code is not proof that two accounts belong to the same person. Use matches as a lead for human review, not as an automatic ban decision.",
        ]
        for note in notes:
            ttk.Label(caveats, text="•  " + note, wraplength=790, justify="left").pack(anchor="w", pady=2)
        ttk.Button(content, text="Close guide", command=w.destroy).pack(anchor="e", pady=(0, 4))

    # ---- profile lists -----------------------------------------------------
    def refresh_profiles(self):
        names = list_profiles()
        ban = load_banlist()
        self.train_combo["values"] = [NEW_LABEL] + names
        if self.train_sel.get() not in [NEW_LABEL] + names:
            self.train_sel.set(NEW_LABEL)
        self._train_sel_changed()
        old = {n: v.get() for n, v in self.det_vars.items()}
        for w in self.det_list.winfo_children():
            w.destroy()
        self.det_vars = {}
        if not names:
            ttk.Label(self.det_list, text="No fingerprints yet - train one first.",
                      foreground="#a60").pack(anchor="w")
        for n in names:
            v = tk.BooleanVar(value=old.get(n, True))
            self.det_vars[n] = v
            code, emb = read_summary(profile_path(n))
            short = format_id(fingerprint_id(emb), min(self._idlen(), 32)) if emb is not None else "no ID yet"
            tag = "   (BANNED)" if n in ban else ""
            ttk.Checkbutton(self.det_list, text=f"{n}   [{code}]   {short}{tag}",
                            variable=v).pack(anchor="w")
        self.refresh_ban_tree()
        self.refresh_keys_tab()
        self.refresh_compare_tab()

    def import_profile(self):
        path = filedialog.askopenfilename(title="Import fingerprint",
                                          filetypes=[("Fingerprint", f"*{EXT}"), ("All", "*.*")])
        if not path:
            return
        try:
            p = Profile.load(path)
            os.makedirs(PROFILE_DIR, exist_ok=True)
            shutil.copy(path, profile_path(sanitize(p.name) or "imported"))
            self.refresh_profiles()
        except Exception as e:
            messagebox.showerror("Import failed", str(e))

# ---- start / stop ------------------------------------------------------
    def toggle_current(self):
        idx = self.nb.index(self.nb.select())
        if self.mode == "train" or (self.mode is None and idx == 0):
            self.toggle_train()
        elif self.mode == "detect" or (self.mode is None and idx == 1):
            self.toggle_detect()

    def _lock_tabs(self, active_index):
        for i in range(5):
            self.nb.tab(i, state="normal" if active_index is None or i == active_index else "disabled")

    def toggle_train(self):
        if self.mode == "detect":
            return
        if self.mode == "train":
            return self.stop_train()
        sel = self.train_sel.get()
        try:
            if sel == NEW_LABEL:
                name = sanitize(self.new_name.get())
                if not name:
                    messagebox.showinfo("Name needed", "Type a name for the new fingerprint.")
                    return
                if os.path.exists(profile_path(name)):
                    if not messagebox.askyesno("Exists", f"'{name}' already exists. Add to it?"):
                        return
                    self.profile = Profile.load(profile_path(name))
                else:
                    self.profile = Profile(name)
            else:
                self.profile = Profile.load(profile_path(sel))
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return
        self.new_windows = self.skipped = 0
        self.afk_manual = False
        self.afk_samples.clear()
        self._last_afk_sample = 0.0
        self._afk_auto_now = False
        self.afk_skipped = 0
        self.ctx_samples.clear()
        self._last_ctx_sample = 0.0
        self._afk_rule_now, self._afk_rule_name, self._cur_tag = False, None, ""
        self.tv["tag"].set("-")
        self.rec.afk_pressed = False
        self.afk_btn.config(state="normal", text="Mark me AFK")
        self.mode = "train"
        self.t0 = time.perf_counter()
        self._key_t = self.t0
        self.next_eval = self.t0 + TRAIN_WARMUP
        self.rec.start()
        self._last_counts = (0, 0, self.t0)
        self.train_btn.config(text=f"Stop & save ({HOTKEY.upper()})")
        self.rec_label.config(text="● RECORDING")
        self._lock_tabs(0)

    def stop_train(self):
        self.rec.stop()
        self.afk_manual = False
        self.afk_btn.config(state="disabled", text="Mark me AFK")
        self.afk_status.config(text="Not recording", fg="#888")
        self.mode = None
        self.rec_label.config(text="")
        self.train_btn.config(text=f"Start training ({HOTKEY.upper()})")
        self._lock_tabs(None)
        p = self.profile
        if p and self.new_windows > 0:
            p.seconds += time.perf_counter() - self.t0
            try:
                size = p.save(profile_path(p.name))
                if self.srv_cfg.get("auto_upload") and self.srv_cfg["url"] and self.srv_cfg["key"]:
                    self._srv_bg(lambda n=p.name: self._upload_one(n))
                c = p.classify()
                tl = tags_line(p)
                messagebox.showinfo("Saved", f"'{p.name}' saved\n{len(p.windows)} windows - {size/1024:.0f} KB\n\n"
                                             f"Class: {c['code']}\n"
                                             f"  Keyboard: {c['k_desc']}\n"
                                             f"  Mouse: {c['m_desc']}\n"
                                             f"  ADS: {c['ads_desc']}\n"
                                             f"  {stability_text(p)}\n"
                                             f"  {keys_line(p)}\n"
                                             + (f"  {tl}\n" if tl else "")
                                             + f"\nID: {self._id_text(p.embedding())}\n\n"
                                               f"{profile_path(p.name)}")
            except Exception as e:
                messagebox.showerror("Save failed", str(e))
        else:
            messagebox.showinfo("Nothing saved", "No full windows were captured. "
                                                 "Train for at least ~30 seconds of real play.")
        self.new_name.set("")
        self.refresh_profiles()
        if p:
            self.train_sel.set(p.name)
            self._train_sel_changed()

    def toggle_detect(self):
        if self.mode == "train":
            return
        if self.mode == "detect":
            return self.stop_detect()
        chosen = [n for n, v in self.det_vars.items() if v.get()]
        if not chosen:
            messagebox.showinfo("Pick fingerprints", "Tick at least one fingerprint.")
            return
        self.det_profiles = []
        for n in chosen:
            try:
                p = Profile.load(profile_path(n))
                p.name = n
                if len(p.windows) < 5:
                    messagebox.showwarning("Too little data", f"'{n}' has under 5 windows - skipped.")
                    continue
                self.det_profiles.append(p)
            except Exception as e:
                messagebox.showerror("Error", f"{n}: {e}")
        if not self.det_profiles:
            return
        pool = {p.name: p for p in self.det_profiles}
        for n, p in load_all_profiles().items():       # whole player population -> feature weights
            pool.setdefault(n, p)
        self.weights = feature_weights(list(pool.values()))
        for p in self.det_profiles:
            p.ref_w = p.self_ref(self.weights)
        self.mismatch = 0
        self.was_match = False
        self.flag_label.config(text="")
        self.live_keys.config(text="")
        self.key_idf = key_idf([p.keys for p in pool.values() if p.keys])
        self.live_slices.clear()
        self._kb_scores = {}
        self.ema = {}
        self.ctx_samples.clear()
        self._last_ctx_sample = 0.0
        self._afk_rule_now, self._afk_rule_name, self._cur_tag = False, None, ""
        self.banned_names = set(load_banlist())
        self.recent.clear()
        self.live_class.config(text="")
        self._live_emb = None
        self.live_id_var.set("")
        for w in self.results.winfo_children():
            w.destroy()
        self.det_rows = {}
        for p in self.det_profiles:
            row = ttk.Frame(self.results)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=p.name, width=16).pack(side="left")
            bar = ttk.Progressbar(row, maximum=100)
            bar.pack(side="left", fill="x", expand=True, padx=6)
            pct = ttk.Label(row, text="--", width=6)
            pct.pack(side="left")
            kb = ttk.Label(row, text="KB --", width=7, foreground="#555")
            kb.pack(side="left")
            self.det_rows[p.name] = (bar, pct, kb)
        self.mode = "detect"
        self.t0 = time.perf_counter()
        self._det_key_t = self.t0
        self.next_eval = self.t0 + DETECT_WARMUP
        self.rec.start()
        self._last_counts = (0, 0, self.t0)
        self.detect_btn.config(text=f"Stop detecting ({HOTKEY.upper()})")
        self.verdict.config(text="Collecting...", fg="#888")
        self.rec_label.config(text="● RECORDING")
        self._lock_tabs(1)

    def stop_detect(self):
        self.rec.stop()
        self.mode = None
        self.rec_label.config(text="")
        self.detect_btn.config(text=f"Start detecting ({HOTKEY.upper()})")
        self.verdict.config(text="Stopped", fg="#888")
        self.flag_label.config(text="")
        self.det_status.config(text="")
        self._lock_tabs(None)

    # ---- AFK handling (training only) --------------------------------------
    def _afk_secs(self):
        try:
            return max(3, min(15, int(self.afk_secs.get())))
        except (tk.TclError, ValueError):
            return AFK_IDLE_SECS

    def _set_afk_key(self):
        self.rec.afk_key = self.afk_key_var.get()
        self.rec.afk_pressed = False

    def toggle_afk_manual(self):
        if self.mode != "train":
            return
        self.afk_manual = not self.afk_manual
        self.afk_btn.config(text="I'm back" if self.afk_manual else "Mark me AFK")
        self._last_afk_sample = 0.0          # sample immediately
        self.update_afk(time.perf_counter())

    def update_afk(self, now):
        """Sample AFK state once a second and refresh the status label.

        AFK = manual key/button, auto-detected idle, or a game-state AFK rule (Integrations tab)."""
        if now - self._last_afk_sample >= 1.0:
            self._last_afk_sample = now
            secs = self._afk_secs()
            self._afk_auto_now = bool(
                self.afk_auto.get() and now - self.t0 >= secs and self.rec.is_idle(secs))
            self.afk_samples.append(
                (now, self.afk_manual or self._afk_auto_now or self._afk_rule_now))
        if self.afk_manual:
            self.afk_status.config(text="AFK (manual) - not being recorded into the fingerprint", fg="#d11")
        elif self._afk_rule_now:
            self.afk_status.config(text=f"AFK (game-state rule '{self._afk_rule_name}') - not being recorded "
                                        "into the fingerprint", fg="#d77a00")
        elif self._afk_auto_now:
            self.afk_status.config(text="AFK (idle detected) - not being recorded into the fingerprint", fg="#d77a00")
        else:
            self.afk_status.config(text="Active - counted", fg="#1a8a3a")

    def update_context(self, now):
        """Once a second while a session runs: sample the game-state AFK rules and the context tag.

        Any trouble in the integration layer falls back to 'not AFK, untagged' - it can never
        interrupt input recording or the UI loop."""
        if now - self._last_ctx_sample < 1.0:
            return
        self._last_ctx_sample = now
        try:
            self._afk_rule_now, self._afk_rule_name = self.integ.afk_check()
            self._cur_tag = self.integ.current_tag()
        except Exception as e:
            print("integration error:", e)
            self._afk_rule_now, self._afk_rule_name, self._cur_tag = False, None, ""
        self.ctx_samples.append((now, self._cur_tag, self._afk_rule_now))

    def _detect_paused(self, now):
        """(paused, why): a game-state AFK rule applies now, or covered > AFK_MAX_FRAC of the window."""
        if self._afk_rule_now:
            return True, f"game-state rule '{self._afk_rule_name}' applies"
        recent = [s[2] for s in self.ctx_samples if s[0] >= now - WINDOW_SEC]
        if recent and sum(recent) / len(recent) > AFK_MAX_FRAC:
            return True, "a game-state rule applied during the last window"
        return False, ""

    # ---- server sync -------------------------------------------------------------
    def _poll_server(self):
        self.srv_label.config(text=self.srv["text"], fg=self.srv["fg"])
        if self.srv.get("dirty") and self.mode is None:
            self.srv["dirty"] = False
            self.refresh_profiles()

    def _wake_on_start(self):
        """First load: ping the server so a sleeping Render instance starts waking right away."""
        if not self.srv_cfg["url"]:
            return
        if not self.srv_cfg["wake_on_start"]:
            self.srv.update(text="Server: not pinged (disabled)", fg="#888")
            return
        self.start_wake()

    def start_wake(self):
        if self._wake_thread and self._wake_thread.is_alive():
            return
        self._wake_thread = threading.Thread(target=wake_server, args=(dict(self.srv_cfg), self.srv),
                                             daemon=True)
        self._wake_thread.start()

    def _ensure_awake(self):
        if self.srv.get("ok"):
            return True
        if self._wake_thread and self._wake_thread.is_alive():
            self._wake_thread.join(WAKE_TOTAL + 20)
            return bool(self.srv.get("ok"))
        return wake_server(dict(self.srv_cfg), self.srv)

    def _srv_bg(self, fn):
        def work():
            try:
                fn()
            except Exception as e:
                print("server error:", e)
        threading.Thread(target=work, daemon=True).start()

    def _upload_one(self, name):
        """-> (ok, message)"""
        if not self._ensure_awake():
            return False, "server not reachable"
        with open(profile_path(name), "rb") as fh:
            raw = fh.read()
        code, body = server_call(self.srv_cfg, "PUT", "/api/fingerprints/" + _q(name), raw, timeout=60)
        if code in (200, 201):
            return True, "uploaded" if code == 201 else "updated"
        try:
            msg = json.loads(body).get("error", "")
        except Exception:
            msg = ""
        return False, f"HTTP {code} {msg}".strip()

    def open_server_dialog(self):
        if self._srv_win is not None and self._srv_win.winfo_exists():
            self._srv_win.lift()
            return
        w = self._srv_win = tk.Toplevel(self)
        w.title("Server sync")
        pad = ttk.Frame(w, padding=14)
        pad.pack(fill="both", expand=True)
        url_v = tk.StringVar(value=self.srv_cfg["url"])
        key_v = tk.StringVar(value=self.srv_cfg["key"])
        wake_v = tk.BooleanVar(value=self.srv_cfg["wake_on_start"])
        auto_v = tk.BooleanVar(value=self.srv_cfg["auto_upload"])
        ttk.Label(pad, text="Server URL  (e.g. https://play-fingerprint-server.onrender.com)").pack(anchor="w")
        ttk.Entry(pad, textvariable=url_v, width=64).pack(fill="x", pady=(2, 8))
        ttk.Label(pad, text="API key  (Render dashboard > Environment > API_KEY)").pack(anchor="w")
        ttk.Entry(pad, textvariable=key_v, show="*", width=64).pack(fill="x", pady=(2, 8))
        ttk.Checkbutton(pad, text="Ping the server when the app starts (wakes a sleeping Render instance)",
                        variable=wake_v).pack(anchor="w")
        ttk.Checkbutton(pad, text="Upload a fingerprint automatically after training",
                        variable=auto_v).pack(anchor="w")
        log = tk.Text(pad, height=11, width=64, state="disabled", wrap="word")
        q = queue.Queue()

        def say(msg):
            q.put(msg)

        def apply_cfg():
            self.srv_cfg = {"url": url_v.get().strip().rstrip("/"), "key": key_v.get().strip(),
                            "wake_on_start": wake_v.get(), "auto_upload": auto_v.get()}
            save_server_cfg(self.srv_cfg)

        def run(fn):
            apply_cfg()

            def work():
                try:
                    fn()
                except Exception as e:
                    say(f"Error: {e}")
            threading.Thread(target=work, daemon=True).start()

        def need_ready():
            say("Waking the server (a sleeping Render instance can take up to a minute)...")
            if not self._ensure_awake():
                say("Could not reach the server. Check the URL, or try again in a minute.")
                return False
            return True

        def do_test():
            if not need_ready():
                return
            code, body = server_call(self.srv_cfg, "GET", "/api/status")
            if code == 200:
                j = json.loads(body)
                say(f"Connected. Server holds {j['fingerprints']} fingerprint(s) and {j['bans']} ban(s).")
            elif code == 401:
                say("Server is up but rejected the API key (401).")
            else:
                say(f"Unexpected response: HTTP {code}")

        def do_upload():
            if not need_ready():
                return
            names = list_profiles()
            if not names:
                say("No local fingerprints to upload.")
                return
            ok = 0
            for n in names:
                good, msg = self._upload_one(n)
                ok += good
                say(f"  {n}: {msg}")
            say(f"Uploaded {ok} of {len(names)}.")

        def do_download():
            if not need_ready():
                return
            code, body = server_call(self.srv_cfg, "GET", "/api/fingerprints")
            if code != 200:
                say(f"List failed: HTTP {code}")
                return
            os.makedirs(PROFILE_DIR, exist_ok=True)
            got = skipped = 0
            for r in json.loads(body):
                name = sanitize(str(r.get("name", "")))
                if not name:
                    continue
                path = profile_path(name)
                if os.path.exists(path) and read_updated(path) >= str(r.get("updated", "")):
                    skipped += 1
                    continue
                c, raw = server_call(self.srv_cfg, "GET", "/api/fingerprints/" + _q(name), timeout=60)
                if c == 200 and 0 < len(raw) <= MAX_BYTES:
                    with open(path, "wb") as fh:
                        fh.write(raw)
                    got += 1
                    say(f"  downloaded {name}")
                else:
                    say(f"  {name}: HTTP {c}")
            say(f"Downloaded {got}, already up to date {skipped}.")
            self.srv["dirty"] = True

        def do_pull_bans():
            if not need_ready():
                return
            code, body = server_call(self.srv_cfg, "GET", "/api/banlist")
            if code != 200:
                say(f"Failed: HTTP {code}")
                return
            data = json.loads(body)
            save_banlist({sanitize(k): {"reason": str(v.get("reason", "")), "date": str(v.get("date", ""))}
                          for k, v in data.items() if isinstance(v, dict) and sanitize(k)})
            say(f"Ban list replaced with the server's ({len(data)} entries).")
            self.srv["dirty"] = True

        def do_push_bans():
            if not need_ready():
                return
            ban = load_banlist()
            for n, e in ban.items():
                c, _ = server_call(self.srv_cfg, "PUT", "/api/banlist/" + _q(n),
                                   json.dumps(e).encode(), content_type="application/json")
                say(f"  {n}: {'ok' if c == 200 else 'HTTP ' + str(c)}")
            say(f"Pushed {len(ban)} ban(s). (Bans/unbans you make in the app are also sent automatically.)")

        def do_server_analysis():
            if not need_ready():
                return
            code, raw = server_call(self.srv_cfg, "POST", "/api/analysis", b"{}",
                                    content_type="application/json", timeout=90)
            if code != 200:
                say(f"Server analysis failed: HTTP {code} {raw[:300].decode('utf-8', 'replace')}")
                return
            result = json.loads(raw)
            matches = result.get("results", [])
            flagged = [x for x in matches if x.get("flag") in ("REVIEW", "HIGH")]
            say(f"Server analysis checked {len(matches)} candidate-to-banned comparison(s); {len(flagged)} need human review.")
            for item in flagged[:100]:
                say(f"  {item.get('flag')}: {item.get('candidate')} vs banned {item.get('banned')} — distance {item.get('distance')} ({item.get('shared_features')} shared features)")
            if not flagged:
                say("No potential matches were flagged by the server's approximate analysis.")
            say("Reminder: heuristic matches are not proof of identity. Review manually; never auto-ban from this score.")

        row1 = ttk.Frame(pad)
        row1.pack(fill="x", pady=(10, 4))
        ttk.Button(row1, text="Save + test connection", command=lambda: run(do_test)).pack(side="left")
        ttk.Button(row1, text="Save only", command=apply_cfg).pack(side="left", padx=6)
        row2 = ttk.Frame(pad)
        row2.pack(fill="x", pady=2)
        ttk.Button(row2, text="Upload my fingerprints", command=lambda: run(do_upload)).pack(side="left")
        ttk.Button(row2, text="Download from server", command=lambda: run(do_download)).pack(side="left", padx=6)
        row3 = ttk.Frame(pad)
        row3.pack(fill="x", pady=2)
        ttk.Button(row3, text="Pull ban list (replaces local)", command=lambda: run(do_pull_bans)).pack(side="left")
        ttk.Button(row3, text="Push my ban list", command=lambda: run(do_push_bans)).pack(side="left", padx=6)
        ttk.Button(row3, text="Run server ban analysis", command=lambda: run(do_server_analysis)).pack(side="left", padx=6)
        log.pack(fill="both", expand=True, pady=(10, 0))

        def poll():
            try:
                while True:
                    msg = q.get_nowait()
                    log.config(state="normal")
                    log.insert("end", msg + "\n")
                    log.see("end")
                    log.config(state="disabled")
            except queue.Empty:
                pass
            if w.winfo_exists():
                w.after(200, poll)
        poll()

    # ---- keybinds ------------------------------------------------------------
    def _update_live_keys(self, agg):
        total = sum(e[0] for e in agg.values())
        if total < 20:
            self.live_keys.config(text=f"Keys: gathering data ({total} presses so far)")
            return
        rows = key_rows(agg)[:6]
        usage = "  ".join(f"{display_key(r['key'])} {r['share'] * 100:.0f}%" for r in rows)
        k = keybind_summary(agg)
        extra = f"  |  unusual binds: {', '.join(k['unusual'])}" if k["unusual"] else ""
        self.live_keys.config(text=f"Live keys: {usage}\nMovement: {k['movement_text']} ({k['scheme']})  |  "
                                   f"Keybinds: {k['text']}{extra}")

    def _build_keys(self):
        t = self.keys_tab
        ttk.Label(t, text="Per-key usage & keybinds", font=("TkDefaultFont",  12, "bold")).pack(anchor="w")
        ttk.Label(t, text="Compare each recorded key for two accounts side by side, including press count, usage share, and average hold time. Train with 'Track individual keys' enabled.",
                  foreground="#666", wraplength=850, justify="left").pack(anchor="w", pady=(2, 8))
        row = ttk.Frame(t)
        row.pack(fill="x")
        ttk.Label(row, text="Account A:").pack(side="left")
        self.keys_a = tk.StringVar()
        self.keys_ca = ttk.Combobox(row, textvariable=self.keys_a, state="readonly", width=20)
        self.keys_ca.pack(side="left", padx=6)
        ttk.Label(row, text="Account B:").pack(side="left", padx=(10, 0))
        self.keys_b = tk.StringVar(value=NONE_LABEL)
        self.keys_cb = ttk.Combobox(row, textvariable=self.keys_b, state="readonly", width=20)
        self.keys_cb.pack(side="left", padx=6)
        for c in (self.keys_ca, self.keys_cb):
            c.bind("<<ComboboxSelected>>", lambda e: self.show_keys())

        cols = ("a_share", "a_cls", "a_n", "a_hold", "b_share", "b_cls", "b_n", "b_hold", "delta")
        self.keys_tree = ttk.Treeview(t, columns=cols, show="tree headings", height=16)
        self.keys_tree.heading("#0", text="KEY")
        self.keys_tree.column("#0", width=75, minwidth=60, stretch=False, anchor="center")
        definitions = [
            ("a_share", "A Share", 75), ("a_cls", "A Class", 85), ("a_n", "A Presses", 85), ("a_hold", "A Hold (ms)", 100),
            ("b_share", "B Share", 75), ("b_cls", "B Class", 85), ("b_n", "B Presses", 85), ("b_hold", "B Hold (ms)", 100),
            ("delta", "Share Δ", 80),
        ]
        for col, title, width in definitions:
            self.keys_tree.heading(col, text=title)
            self.keys_tree.column(col, width=width, minwidth=65, stretch=True, anchor="center")
        yscroll = ttk.Scrollbar(t, orient="vertical", command=self.keys_tree.yview)
        xscroll = ttk.Scrollbar(t, orient="horizontal", command=self.keys_tree.xview)
        self.keys_tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.keys_tree.pack(fill="both", expand=True, pady=(10, 0))
        xscroll.pack(fill="x")
        yscroll.place(relx=1.0, rely=0.22, relheight=0.68, anchor="ne")
        self.keys_tree.tag_configure("rare", foreground="#a60")
        self.keys_tree.tag_configure("move", foreground="#1a6fd1")
        self.keys_tree.tag_configure("distinct", foreground="#c00")
        self.keys_tree.tag_configure("odd", background="#f3f6fa")
        self.keys_sum = ttk.Label(t, text="", wraplength=900, justify="left")
        self.keys_sum.pack(anchor="w", pady=(6, 0))

    def refresh_keys_tab(self):
        names = list_profiles()
        self.keys_ca["values"] = names
        self.keys_cb["values"] = [NONE_LABEL] + names
        if self.keys_a.get() not in names:
            self.keys_a.set(names[0] if names else "")
        if self.keys_b.get() not in [NONE_LABEL] + names:
            self.keys_b.set(names[1] if len(names) > 1 else NONE_LABEL)
        elif self.keys_b.get() == NONE_LABEL and len(names) > 1 and names[0] == self.keys_a.get():
            self.keys_b.set(names[1])
        self.show_keys()

    def show_keys(self):
        self.keys_tree.delete(*self.keys_tree.get_children())
        names = list_profiles()
        pop = {n: read_keys(profile_path(n)) for n in names}
        name_a, name_b = self.keys_a.get(), self.keys_b.get()
        comparing = bool(name_b and name_b != NONE_LABEL and name_b != name_a)
        # When Account B isn't selected, show only Account A's columns so the
        # single-account view doesn't waste space on empty comparison fields.
        self.keys_tree.configure(displaycolumns=(
            ("a_share", "a_cls", "a_n", "a_hold", "b_share", "b_cls", "b_n", "b_hold", "delta")
            if comparing else ("a_share", "a_cls", "a_n", "a_hold")
        ))
        self.keys_tree.heading("a_share", text=(f"{name_a} Share" if name_a else "Share"))
        self.keys_tree.heading("a_cls", text="Class")
        self.keys_tree.heading("a_n", text="Presses")
        self.keys_tree.heading("a_hold", text="Hold (ms)")
        self.keys_tree.heading("b_share", text=f"{name_b} Share" if comparing else "B Share")
        self.keys_tree.heading("b_cls", text="Class")
        self.keys_tree.heading("b_n", text="Presses")
        self.keys_tree.heading("b_hold", text="Hold (ms)")
        self.keys_tree.heading("delta", text="Share Δ")
        ka = pop.get(name_a) or {}
        kb = pop.get(name_b) or {} if comparing else {}
        if not name_a:
            self.keys_sum.config(text="No fingerprints yet - train one first.")
            return
        if not ka and not kb:
            self.keys_sum.config(text="Neither selected account has per-key data. Retrain with 'Track individual keys' enabled.")
            return

        def index_keys(agg):
            return {r["key"]: r for r in key_rows(agg)} if agg else {}
        ra, rb = index_keys(ka), index_keys(kb)
        all_keys = set(ra) | set(rb)
        ordered = sorted(all_keys, key=lambda key: max(ra.get(key, {}).get("n", 0), rb.get(key, {}).get("n", 0)), reverse=True)
        total_a = sum(max(0, int(v[0])) for v in ka.values()) if ka else 0
        total_b = sum(max(0, int(v[0])) for v in kb.values()) if kb else 0
        for i, key in enumerate(ordered):
            a, b = ra.get(key), rb.get(key)
            def fields(r, total):
                if not r:
                    return ("—", "—", "0", "—")
                hold = "—" if r.get("hold") is None else f"{r['hold'] * 1000:.0f} ± {r.get('hold_sd', 0) * 1000:.0f}"
                return (f"{r['share'] * 100:.1f}%", r["cls"], str(r["n"]), hold)
            av, bv = fields(a, total_a), fields(b, total_b)
            delta = ((a["share"] if a else 0) - (b["share"] if b else 0)) * 100
            tags = ["odd"] if i % 2 else []
            if (a and a["cls"] == "Rare") or (b and b["cls"] == "Rare"):
                tags.append("rare")
            elif (a and a["cls"] == "Movement") or (b and b["cls"] == "Movement"):
                tags.append("move")
            self.keys_tree.insert("", "end", text=display_key(key), values=(*av, *bv, f"{delta:+.1f} pp"), tags=tuple(tags))

        lines = []
        for label, account, agg in (("A", name_a, ka), ("B", name_b, kb)):
            if agg:
                summary = keybind_summary(agg)
                lines.append(f"{label} ({account}) movement keys: {summary['movement_text']} | {summary['scheme']} | keybinds: {summary['text']}")
            elif account and account != NONE_LABEL:
                lines.append(f"{label} ({account}): no per-key data recorded; retrain with individual-key tracking enabled.")
        if ka and kb:
            sim = key_similarity(ka, kb, key_idf(list(pop.values())))
            if sim is not None:
                lines.append(f"Keybind similarity: {sim * 100:.0f}%  (A-only and B-only keys are visible in the table above.)")
        elif not comparing:
            lines.append("Choose Account B to compare both accounts' keys in adjacent columns.")
        self.keys_sum.config(text="\n".join(lines))

    # ---- account comparison -------------------------------------------------
    def _build_compare(self):
        t = self.compare_tab
        ttk.Label(t, text="Account comparison", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(t, text="Choose two saved fingerprints to compare their recorded behavior side by side.",
                  foreground="#58677d").pack(anchor="w", pady=(2, 12))
        pick = ttk.LabelFrame(t, text="Compare accounts", padding=12)
        pick.pack(fill="x", pady=(0, 12))
        ttk.Label(pick, text="Account A").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.compare_a = tk.StringVar()
        self.compare_ca = ttk.Combobox(pick, textvariable=self.compare_a, state="readonly", width=28)
        self.compare_ca.grid(row=0, column=1, sticky="ew", padx=(0, 18))
        ttk.Label(pick, text="Account B").grid(row=0, column=2, sticky="w", padx=(0, 8))
        self.compare_b = tk.StringVar()
        self.compare_cb = ttk.Combobox(pick, textvariable=self.compare_b, state="readonly", width=28)
        self.compare_cb.grid(row=0, column=3, sticky="ew", padx=(0, 12))
        ttk.Button(pick, text="Compare", command=self.show_compare).grid(row=0, column=4)
        pick.columnconfigure(1, weight=1)
        pick.columnconfigure(3, weight=1)
        self.compare_summary = ttk.Label(t, text="Select two accounts above.", font=("Segoe UI", 12, "bold"),
                                         padding=(12, 10), background="#ffffff", anchor="w")
        self.compare_summary.pack(fill="x", pady=(0, 10))
        tablebox = ttk.Frame(t)
        tablebox.pack(fill="both", expand=True)
        cols = ("metric", "a", "b", "delta")
        self.compare_tree = ttk.Treeview(tablebox, columns=cols, show="headings")
        for col, title, width, anchor in [
            ("metric", "STATISTIC", 260, "w"), ("a", "ACCOUNT A", 240, "center"),
            ("b", "ACCOUNT B", 240, "center"), ("delta", "DIFFERENCE", 180, "center")]:
            self.compare_tree.heading(col, text=title)
            self.compare_tree.column(col, width=width, anchor=anchor, stretch=(col == "metric"))
        scroll = ttk.Scrollbar(tablebox, orient="vertical", command=self.compare_tree.yview)
        self.compare_tree.configure(yscrollcommand=scroll.set)
        self.compare_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.compare_tree.tag_configure("section", background="#e7eef8", font=("Segoe UI", 9, "bold"))
        self.compare_tree.tag_configure("odd", background="#f7f9fc")
        ttk.Label(t, text="Similarity scores are estimates from input patterns, not proof of shared account ownership.",
                  foreground="#65748a").pack(anchor="w", pady=(8, 0))
        for cb in (self.compare_ca, self.compare_cb):
            cb.bind("<<ComboboxSelected>>", lambda _e: self.show_compare())

    def refresh_compare_tab(self):
        names = list_profiles()
        for cb, var in ((self.compare_ca, self.compare_a), (self.compare_cb, self.compare_b)):
            cb["values"] = names
            if var.get() not in names:
                var.set(names[0] if names else "")
        if len(names) > 1 and self.compare_a.get() == self.compare_b.get():
            self.compare_b.set(names[1])
        self.show_compare()

    def show_compare(self):
        if not hasattr(self, "compare_tree"):
            return
        self.compare_tree.delete(*self.compare_tree.get_children())
        a_name, b_name = self.compare_a.get(), self.compare_b.get()
        if not a_name or not b_name:
            self.compare_summary.config(text="Create or import at least two fingerprints to compare accounts.")
            return
        if a_name == b_name:
            self.compare_summary.config(text="Choose two different accounts to see a comparison.")
            return
        try:
            a, b = Profile.load(profile_path(a_name)), Profile.load(profile_path(b_name))
            va = typical_vector(a.windows) if a.windows else np.full(len(FEATURE_NAMES), NAN)
            vb = typical_vector(b.windows) if b.windows else np.full(len(FEATURE_NAMES), NAN)
            score = pair_score(a, b, feature_weights(list(load_all_profiles(min_windows=5).values())))
            sim = None if score is None else 100.0 / (1.0 + (score / 1.8) ** 4)
            key_sim = key_similarity(
                a.keys, b.keys,
                key_idf([read_keys(profile_path(n)) for n in list_profiles()]),
            )
            behavioral_text = (
                f"Behavioral similarity: {sim:.1f}%" if sim is not None
                else "Behavioral similarity: not enough matching data"
            )
            key_text = (
                f"Keybind similarity: {key_sim * 100:.1f}%" if key_sim is not None
                else "Keybind similarity: not enough key data"
            )
            self.compare_summary.config(text=f"{behavioral_text}     •     {key_text}")
            rows = [
                ("OVERVIEW", None, None, None, True),
                ("Class code", a.classify().get("code", "—"), b.classify().get("code", "—"), None, False),
                ("Playstyle class", a.classify().get("playstyle", "—"), b.classify().get("playstyle", "—"), None, False),
                ("Recorded time", f"{a.seconds/60:.1f} min", f"{b.seconds/60:.1f} min", None, False),
                ("Training windows", str(len(a.windows)), str(len(b.windows)), None, False),
                ("Training stability", stability_text(a).replace("Stability: ", ""),
                 stability_text(b).replace("Stability: ", ""), None, False),
                ("Keyboard style", a.classify().get("k_desc", "—"), b.classify().get("k_desc", "—"), None, False),
                ("Mouse style", a.classify().get("m_desc", "—"), b.classify().get("m_desc", "—"), None, False),
                ("ADS / right-click", a.classify().get("ads_desc", "—"), b.classify().get("ads_desc", "—"), None, False),
                ("MOUSE BEHAVIOR", None, None, None, True),
            ]
            metrics = [
                ("Mean pointer speed", "m_speed_mean", "px/s", 1),
                ("Pointer speed variation", "m_speed_cv", "ratio", 3),
                ("Mouse acceleration", "m_acc_mean", "px/s²", 1),
                ("Mouse path straightness", "m_straight", "%", 2),
                ("Average stroke length", "m_stroke_len", "px", 1),
                ("Stroke rate", "m_stroke_rate", "/s", 2),
                ("Click rate", "c_rate", "/s", 2),
                ("Typical click hold", "c_hold_med", "s", 3),
                ("Right-click / ADS time", "r_time_ratio", "%", 2),
                ("Shots while ADS", "r_fire_ratio", "%", 2),
                ("Key presses per second", "k_rate", "/s", 2),
                ("Typical key hold", "k_hold_med", "s", 3),
                ("Key timing variation", "k_flight_cv", "ratio", 3),
                ("Key overlap ratio", "k_overlap", "ratio", 3),
                ("Unique keys per window", "k_unique", "keys", 1),
                ("Scroll rate", "s_rate", "/s", 2),
            ]
            # Keep section headers separate from metric tuples: each metric has
            # exactly four fields (label, feature, unit, precision).
            keyboard_start = next(i for i, item in enumerate(metrics) if item[1] == "k_rate")
            for label, field, unit, decimals in metrics[:keyboard_start]:
                rows.append((label, self._compare_value(va, field, unit, decimals),
                             self._compare_value(vb, field, unit, decimals), field, False))
            rows.append(("KEYBOARD BEHAVIOR", None, None, None, True))
            for label, field, unit, decimals in metrics[keyboard_start:]:
                rows.append((label, self._compare_value(va, field, unit, decimals),
                             self._compare_value(vb, field, unit, decimals), field, False))
            for idx, (label, av, bv, field, section) in enumerate(rows):
                if section:
                    self.compare_tree.insert("", "end", values=(label, "", "", ""), tags=("section",))
                    continue
                delta = ""
                if field:
                    xa, xb = float(va[IDX[field]]), float(vb[IDX[field]])
                    if not np.isnan(xa) and not np.isnan(xb):
                        d = xa - xb
                        if unit == "%":
                            delta = f"{d * 100:+.2f} pp"
                        elif unit in ("s", "ratio", "/s"):
                            delta = f"{d:+.3f} {unit}"
                        elif unit == "keys":
                            delta = f"{d:+.1f}"
                        else:
                            delta = f"{d:+.1f} {unit}"
                self.compare_tree.insert("", "end", values=(label, av, bv, delta),
                                         tags=("odd",) if idx % 2 else ())

            # Compare the actual recorded keys side by side as well as the
            # aggregate keyboard metrics above. This is the per-key detail the
            # account comparison needs (press count, share, and typical hold).
            self.compare_tree.insert("", "end", values=("KEY-BY-KEY USAGE", "", "", ""),
                                     tags=("section",))
            keys_a, keys_b = a.keys or {}, b.keys or {}
            total_a = sum(max(0, int(e[0])) for e in keys_a.values())
            total_b = sum(max(0, int(e[0])) for e in keys_b.values())
            all_keys = set(keys_a) | set(keys_b)
            ordered_keys = sorted(
                all_keys,
                key=lambda key: max(keys_a.get(key, [0])[0], keys_b.get(key, [0])[0]),
                reverse=True,
            )
            if not all_keys or (total_a == 0 and total_b == 0):
                self.compare_tree.insert("", "end", values=(
                    "No individual-key data", "Not recorded", "Not recorded", ""
                ))
            else:
                self.compare_tree.insert("", "end", values=(
                    "Format", "presses | share | avg hold", "presses | share | avg hold", "share difference"
                ), tags=("section",))
                for key_index, key in enumerate(ordered_keys):
                    ea = keys_a.get(key)
                    eb = keys_b.get(key)

                    def key_details(entry, total):
                        if not entry or entry[0] <= 0:
                            return "Not used"
                        presses = int(entry[0])
                        share = presses / total * 100 if total else 0.0
                        hold = (f"{entry[2] / entry[1]:.2f}s" if len(entry) > 1 and entry[1] >= 3 else "n/a")
                        return f"{presses} | {share:.1f}% | {hold}"

                    share_a = (ea[0] / total_a) if ea and total_a else 0.0
                    share_b = (eb[0] / total_b) if eb and total_b else 0.0
                    share_delta = (share_a - share_b) * 100
                    self.compare_tree.insert(
                        "", "end",
                        values=(display_key(key), key_details(ea, total_a), key_details(eb, total_b),
                                f"{share_delta:+.1f} pp" if ea or eb else "—"),
                        tags=("odd",) if key_index % 2 else (),
                    )
        except Exception as e:
            self.compare_summary.config(text=f"Could not compare these fingerprints: {e}")

    @staticmethod
    def _compare_value(vec, field, unit, decimals):
        value = float(vec[IDX[field]])
        if np.isnan(value):
            return "No data"
        if unit == "%":
            return f"{value * 100:.{decimals}f}%"
        if unit in ("s", "ratio", "/s"):
            return f"{value:.{decimals}f} {unit}"
        if unit == "keys":
            return f"{value:.{decimals}f}"
        return f"{value:.{decimals}f} {unit}"

    # ---- periodic work -----------------------------------------------------
    def tick(self):
        try:
            if self.rec.hotkey_pressed:
                self.rec.hotkey_pressed = False
                self.toggle_current()
            if self.rec.afk_pressed:
                self.rec.afk_pressed = False
                if self.mode == "train":          # ignored outside training
                    self.toggle_afk_manual()
            self._poll_server()
            self.refresh_stats()
            now = time.perf_counter()
            if self.mode:
                self.update_context(now)          # game-state AFK rules + context tag (1 Hz)
            if self.mode == "train":
                self.update_afk(now)
            if self.mode and now >= self.next_eval:
                if self.mode == "train":
                    self.train_eval(now)
                else:
                    self.detect_eval(now)
        except Exception as e:  # never kill the UI loop
            print("tick error:", e)
        finally:
            self.after(250, self.tick)

    def refresh_stats(self):
        if not self.mode:
            return
        now = time.perf_counter()
        m, k, t = self._last_counts
        dt = now - t
        if dt >= 1.0:
            mr, kr = (self.rec.n_moves - m) / dt, (self.rec.n_keys - k) / dt
            self._last_counts = (self.rec.n_moves, self.rec.n_keys, now)
            if self.mode == "train":
                self.tv["mouse"].set(f"{mr:.0f}")
                self.tv["keys"].set(f"{kr:.1f}")
        if self.mode == "train":
            el = now - self.t0
            self.tv["time"].set(f"{int(el//60)}:{int(el%60):02d}")
            total = len(self.profile.windows)
            self.tv["wins"].set(f"{self.new_windows} new  ({total} total, "
                                f"{self.skipped} low-activity, {self.afk_skipped} AFK skipped)")
            self.train_bar["value"] = min(100, total / TARGET_WINDOWS * 100)

    def train_eval(self, now):
        self.next_eval = now + TRAIN_STEP
        key_lo = max(self._key_t, now - 1.5 * TRAIN_STEP)      # non-overlapping slice for per-key stats
        self._key_t = now
        recent = [a for t, a in self.afk_samples if t >= now - WINDOW_SEC]
        if recent and sum(recent) / len(recent) > AFK_MAX_FRAC:
            self.afk_skipped += 1
            return
        vec = window_vector(self.rec.snapshot(WINDOW_SEC), WINDOW_SEC)
        if vec is None:
            self.skipped += 1
        else:
            tag = dominant_tag(self.ctx_samples, now, WINDOW_SEC)    # '' unless one tag held for the window
            try:
                nums = self.integ.current_nums()
            except Exception:
                nums = None
            self.profile.add_window(vec, tag, nums)
            self.new_windows += 1
            self.tv["tag"].set(tag or "(none)")
            if self.track_keys.get():
                self.profile.add_key_slice(slice_key_stats(self.rec.snapshot(KEEP)[3], key_lo, now))
            cls = self.profile.classify()
            self.tv["class"].set(f"{cls['code']} · {cls.get('playstyle', 'Not enough data')}")
            self.tv["ads"].set(classify_ads(typical_vector(self.profile.windows))["short"])
            self._train_emb = self.profile.embedding()
            self.show_train_id()

    def detect_eval(self, now):
        self.next_eval = now + DETECT_STEP
        paused, why = self._detect_paused(now)
        if paused:                                   # game-state AFK rule: pause scoring
            self.verdict.config(text="Paused (game state)", fg="#888")
            self.det_status.config(text=f"Scoring paused - {why}. Last check {datetime.now().strftime('%H:%M:%S')}")
            return
        dur = min(WINDOW_SEC, now - self.t0)
        lo = max(self._det_key_t, now - 2 * DETECT_STEP)
        self._det_key_t = now
        self.live_slices.append(slice_key_stats(self.rec.snapshot(KEEP)[3], lo, now))
        live_agg = aggregate_slices(self.live_slices)
        self._update_live_keys(live_agg)
        self._kb_scores = {}
        for p in self.det_profiles:
            kb = key_similarity(live_agg, p.keys, self.key_idf)
            self._kb_scores[p.name] = kb
            self.det_rows[p.name][2].config(text="KB --" if kb is None else f"KB {kb * 100:.0f}%")
        vec = window_vector(self.rec.snapshot(dur), dur)
        if vec is None:
            self.det_status.config(text="Not enough activity in the last few seconds - waiting...")
            return
        self.recent.append(vec)
        c = classify_vector(typical_vector(list(self.recent)))
        self.live_class.config(text=f"Current style: {c['code']}\n"
                                    f"Keyboard: {c['k_desc']}  |  Mouse: {c['m_desc']}\n"
                                    f"ADS: {c['ads_desc']}")
        self._live_emb = embed(typical_vector(list(self.recent)))
        self.live_id_var.set(self._id_text(self._live_emb))
        cur_tag = dominant_tag(self.ctx_samples, now, WINDOW_SEC) if self.use_tags.get() else ""
        tag_used = 0
        for p in self.det_profiles:
            mu, sd, ref, used = p.match_params(cur_tag, self.weights)   # tag subset if 10+ windows, else whole
            d = distance(vec, mu, sd, self.weights)
            if d is None:
                continue
            tag_used += used
            s = d / ref
            prev = self.ema.get(p.name)
            self.ema[p.name] = s if prev is None else 0.6 * prev + 0.4 * s
        if not self.ema:
            return
        thr = float(self.strict.get())
        for name, s in self.ema.items():
            bar, pct, _kb = self.det_rows[name]
            sim = 100.0 / (1.0 + (s / thr) ** 4)
            bar["value"] = sim
            pct.config(text=f"{sim:.0f}%")
        best = min(self.ema, key=self.ema.get)
        if self.ema[best] <= thr and best in self.banned_names:
            self.verdict.config(text=f"[FLAG] Matches banned account '{best}'", fg="#d11")
        elif self.ema[best] <= thr:
            txt = f"Playing: {best}" if len(self.det_profiles) > 1 else f"Match: {best}"
            self.verdict.config(text=txt, fg="#1a8a3a")
        else:
            self.verdict.config(text="Unknown player", fg="#d77a00")
        matched = self.ema[best] <= thr
        if matched:
            self.mismatch, self.was_match = 0, True
        else:
            self.mismatch += 1
        notes = automation_flags(typical_vector(list(self.recent))) if len(self.recent) >= 3 else []
        for n_, v_ in self._kb_scores.items():
            if v_ is not None and v_ >= 0.9 and n_ in self.banned_names and not (matched and best == n_):
                notes.append(f"Keybind pattern closely matches banned account '{n_}'")
        if matched and self._kb_scores.get(best) is not None and self._kb_scores[best] < 0.5:
            notes.append(f"Keybinds differ from '{best}' even though movement matches")
        if self.was_match and self.mismatch >= 3:
            notes.append("Player change suspected: input no longer matches the earlier match")
        self.flag_label.config(text="\n".join(notes))
        ctx = ""
        if self.use_tags.get():
            if not cur_tag:
                ctx = "  |  context tag: none right now - whole fingerprint"
            elif tag_used:
                ctx = f"  |  context tag '{cur_tag}': matched against same-tag windows ({tag_used}/{len(self.det_profiles)} fingerprint(s))"
            else:
                ctx = f"  |  context tag '{cur_tag}': under {TAG_MIN_WINDOWS} matching windows - whole fingerprint"
        self.det_status.config(text=f"Last check {datetime.now().strftime('%H:%M:%S')} "
                                    f"(last {dur:.0f}s, {len(self.recent)} recent windows)" + ctx)

    def on_close(self):
        if self.mode == "train":
            if not messagebox.askyesno("Quit", "Training is running. Stop, save and quit?"):
                return
            self.stop_train()
        self.integ.stop_all()
        self.rec.shutdown()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
