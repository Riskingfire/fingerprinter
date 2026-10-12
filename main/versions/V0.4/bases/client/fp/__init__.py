"""
Play Fingerprint
================
Learns HOW you play (mouse + keyboard habits) and later recognises you by it.

  pip install pynput numpy
  python fingerprinter.py

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

PACKAGE LAYOUT (V0.4 - same features, same files, split into a base and optional modules)
-----------------------------------------------------------------------------------------
  fp/            the BASE: recorder, feature extraction, classes, IDs, profiles, keybinds, screening
                 maths, a headless Engine (train / detect), the module loader and a CLI. It never
                 imports tkinter or any module, and runs on its own:
                     python fingerprinter.py --no-modules train MyName
  modules/       everything optional. Drop a .py file in here and it is loaded automatically:
                     ui_tk (window + Train tab)   ui_detect  ui_class (class display + guide)
                     ui_screen  ui_keys  ui_compare  ui_resources  ui_server  ui_integrations
                     integrations  server_sync
                 Choose with --only a,b / --disable a,b / --no-modules, or modules.json.
                 A new module is just a new file here: no base file lists, imports or mentions it.
                 See fp/plugins.py for how to write a module.
"""

from .settings import *
from .recorder import *
from .features import *
from .classify import *
from .embedding import *
from .profile import *
from .store import *
from .keybinds import *
from .analysis import *
from .context import *
from .hooks import Hooks
from .engine import Engine
from .plugins import Context, ModuleUnavailable

__version__ = "0.4"
