"""Tunable settings, paths and feature definitions (base layer - no UI, no optional modules)."""
import os

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
# Game base: its own folder (override with the FP_GAME_DATA environment variable or the "data_dir" setting)
APP_DATA_DIR = os.environ.get("FP_GAME_DATA") or os.path.join(os.path.expanduser("~"), "AppData", "fingerprint_game")
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
