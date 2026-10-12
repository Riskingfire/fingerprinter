"""Headless Train / Detect engine - everything the old App did that was not drawing widgets.

No UI is imported here. A front-end (the Tk window, the CLI, your own script) drives it:

    eng = Engine(hooks)
    eng.start_train(Profile("me"))      # or eng.start_detect([profiles])
    while eng.mode:                     # call tick() a few times a second
        eng.tick()
    eng.stop_train()                    # -> {"profile":..., "saved":True, "size":...}

and listens to events on ``hooks``:
    session.start (mode)                      session.stop (mode, result)
    train.window  (info)                      detect.update (info)
    profile.saved (profile, size_bytes)       engine.tick (now)
    shutdown ()
and asks optional modules for context through *collect* hooks (any module may answer, none has to):
    context.sample (engine, now) -> {"afk": bool, "rule": str|None, "tag": str}
    context.nums   (engine, now) -> {name: number, ...}
"""
import os
import time
from collections import deque
from datetime import datetime


from .analysis import automation_flags, feature_weights, load_all_profiles
from .classify import classify_ads, classify_vector, typical_vector
from .context import dominant_tag
from .embedding import embed
from .features import distance, window_vector
from .hooks import Hooks
from .keybinds import aggregate_slices, key_idf, key_similarity, slice_key_stats
from .profile import Profile
from .recorder import InputRecorder
from .settings import (AFK_IDLE_SECS, AFK_MAX_FRAC, DETECT_STEP, DETECT_WARMUP, KEEP, PROFILE_DIR,
                       TAG_MIN_WINDOWS, TRAIN_STEP, TRAIN_WARMUP, WINDOW_SEC)
from .store import list_profiles, load_banlist, profile_path


class Engine:
    def __init__(self, hooks=None, recorder=None):
        self.hooks = hooks or Hooks()
        self._rec = recorder                # created on first use: nothing listens to input before that
        # ---- settings (front-ends set these; they are plain values, no UI toolkit involved) ----
        self.afk_auto = True              # auto-detect idle time while training
        self.afk_secs = AFK_IDLE_SECS     # ...after this many idle seconds (3-15)
        self.track_keys = True            # store per-key statistics
        self.strict = 1.8                 # detect strictness (1.2 strict .. 3.0 lenient)
        self.use_tags = False             # detect: compare only against same-context-tag windows
        # ---- session state ----
        self.mode = None                  # None | 'train' | 'detect'
        self.t0 = 0.0
        self.next_eval = 0.0
        self.profile = None
        self.new_windows = 0
        self.skipped = 0
        self.afk_skipped = 0
        self.afk_manual = False
        self._afk_auto_now = False
        self.afk_samples = deque(maxlen=40)       # (time, was_afk) once per second
        self._last_afk_sample = 0.0
        self.ctx_samples = deque(maxlen=60)       # (time, tag, rule_applies) once per second
        self._last_ctx_sample = 0.0
        self._afk_rule_now = False
        self._afk_rule_name = None
        self._cur_tag = ""
        self._key_t = 0.0
        self._det_key_t = 0.0
        # ---- detect state ----
        self.det_profiles = []
        self.weights = None
        self.key_idf = None
        self.banned_names = set()
        self.live_slices = deque(maxlen=12)
        self._kb_scores = {}
        self.mismatch = 0
        self.was_match = False
        self.ema = {}
        self.recent = deque(maxlen=8)
        self.last_detect = None

    @property
    def rec(self):
        """The global input recorder (starts its idle hotkey listener the first time it is used)."""
        if self._rec is None:
            self._rec = InputRecorder()
        return self._rec

    # ---- small helpers --------------------------------------------------------------------------
    def poll_hotkey(self):
        """True once per hotkey press (the front-end decides what the hotkey does)."""
        if self.rec.hotkey_pressed:
            self.rec.hotkey_pressed = False
            return True
        return False

    def _afk_secs(self):
        try:
            return max(3, min(15, int(self.afk_secs)))
        except (TypeError, ValueError):
            return AFK_IDLE_SECS

    def set_afk_key(self, name):
        self.rec.afk_key = name
        self.rec.afk_pressed = False

    def _reset_context(self):
        self.ctx_samples.clear()
        self._last_ctx_sample = 0.0
        self._afk_rule_now, self._afk_rule_name, self._cur_tag = False, None, ""

    # ---- session control ------------------------------------------------------------------------
    def start_train(self, profile):
        if self.mode:
            raise RuntimeError("a session is already running")
        self.profile = profile
        self.new_windows = self.skipped = 0
        self.afk_manual = False
        self.afk_samples.clear()
        self._last_afk_sample = 0.0
        self._afk_auto_now = False
        self.afk_skipped = 0
        self._reset_context()
        self.rec.afk_pressed = False
        self.mode = "train"
        self.t0 = time.perf_counter()
        self._key_t = self.t0
        self.next_eval = self.t0 + TRAIN_WARMUP
        self.rec.start()
        self.hooks.emit("session.start", "train")

    def stop_train(self):
        """Stop recording and save the profile. -> dict(profile, new_windows, saved, size, error)"""
        self.rec.stop()
        self.afk_manual = False
        self.mode = None
        p = self.profile
        result = {"profile": p, "new_windows": self.new_windows, "saved": False, "size": None, "error": None}
        if p and self.new_windows > 0:
            p.seconds += time.perf_counter() - self.t0
            try:
                os.makedirs(PROFILE_DIR, exist_ok=True)      # the old UI created this as a side effect of listing
                result["size"] = p.save(profile_path(p.name))
                result["saved"] = True
                self.hooks.emit("profile.saved", p, result["size"])
            except Exception as e:
                result["error"] = e
        self.hooks.emit("session.stop", "train", result)
        return result

    def load_detect_profiles(self, names):
        """Load fingerprints by name for detection. -> (profiles, problems) where problems are
        ("short" | "error", name, message) tuples for ones that were skipped."""
        profiles, problems = [], []
        for n in names:
            try:
                p = Profile.load(profile_path(n))
                p.name = n
                if len(p.windows) < 5:
                    problems.append(("short", n, f"'{n}' has under 5 windows - skipped."))
                    continue
                profiles.append(p)
            except Exception as e:
                problems.append(("error", n, f"{n}: {e}"))
        return profiles, problems

    def start_detect(self, profiles):
        if self.mode:
            raise RuntimeError("a session is already running")
        if not profiles:
            raise ValueError("no fingerprints to compare against")
        self.det_profiles = list(profiles)
        pool = {p.name: p for p in self.det_profiles}
        for n, p in load_all_profiles().items():       # whole player population -> feature weights
            pool.setdefault(n, p)
        self.weights = feature_weights(list(pool.values()))
        for p in self.det_profiles:
            p.ref_w = p.self_ref(self.weights)
        self.mismatch = 0
        self.was_match = False
        self.key_idf = key_idf([p.keys for p in pool.values() if p.keys])
        self.live_slices.clear()
        self._kb_scores = {}
        self.ema = {}
        self._reset_context()
        self.banned_names = set(load_banlist())
        self.recent.clear()
        self.last_detect = None
        self.mode = "detect"
        self.t0 = time.perf_counter()
        self._det_key_t = self.t0
        self.next_eval = self.t0 + DETECT_WARMUP
        self.rec.start()
        self.hooks.emit("session.start", "detect")

    def stop_detect(self):
        self.rec.stop()
        self.mode = None
        self.hooks.emit("session.stop", "detect", None)

    def shutdown(self):
        if self.mode == "train":
            self.stop_train()
        elif self.mode == "detect":
            self.stop_detect()
        self.hooks.emit("shutdown")
        if self._rec is not None:
            self._rec.shutdown()

    # ---- AFK handling (training only) ------------------------------------------------------------
    def toggle_afk_manual(self):
        if self.mode != "train":
            return
        self.afk_manual = not self.afk_manual
        self._last_afk_sample = 0.0          # sample immediately
        self.update_afk(time.perf_counter())

    def update_afk(self, now):
        """Sample AFK state once a second: manual key/button, idle auto-detect, or a context AFK rule."""
        if now - self._last_afk_sample >= 1.0:
            self._last_afk_sample = now
            secs = self._afk_secs()
            self._afk_auto_now = bool(
                self.afk_auto and now - self.t0 >= secs and self.rec.is_idle(secs))
            self.afk_samples.append(
                (now, self.afk_manual or self._afk_auto_now or self._afk_rule_now))

    def afk_state(self):
        """('manual' | 'rule' | 'idle' | 'active', rule name or None) for status displays."""
        if self.afk_manual:
            return "manual", None
        if self._afk_rule_now:
            return "rule", self._afk_rule_name
        if self._afk_auto_now:
            return "idle", None
        return "active", None

    def update_context(self, now):
        """Once a second while a session runs: ask context providers (e.g. the integrations module)
        for AFK rules and the context tag. No provider / any trouble = 'not AFK, untagged'."""
        if now - self._last_ctx_sample < 1.0:
            return
        self._last_ctx_sample = now
        afk, rule, tag = False, None, ""
        for r in self.hooks.collect("context.sample", self, now):
            try:
                if r.get("afk") and not afk:
                    afk, rule = True, r.get("rule")
                if r.get("tag") and not tag:
                    tag = str(r["tag"])
            except Exception as e:
                print("context provider error:", e)
        self._afk_rule_now, self._afk_rule_name, self._cur_tag = afk, rule, tag
        self.ctx_samples.append((now, self._cur_tag, self._afk_rule_now))

    def _context_nums(self, now):
        for r in self.hooks.collect("context.nums", self, now):
            if r:
                return r
        return None

    def _detect_paused(self, now):
        """(paused, why): a context AFK rule applies now, or covered > AFK_MAX_FRAC of the window."""
        if self._afk_rule_now:
            return True, f"game-state rule '{self._afk_rule_name}' applies"
        recent = [s[2] for s in self.ctx_samples if s[0] >= now - WINDOW_SEC]
        if recent and sum(recent) / len(recent) > AFK_MAX_FRAC:
            return True, "a game-state rule applied during the last window"
        return False, ""

    # ---- main loop step ---------------------------------------------------------------------------
    def tick(self):
        """Call ~4x per second. Handles the AFK key, context sampling, AFK sampling and the
        periodic Train / Detect evaluation. Front-ends should wrap this in try/except."""
        if self.rec.afk_pressed:
            self.rec.afk_pressed = False
            if self.mode == "train":          # ignored outside training
                self.toggle_afk_manual()
        now = time.perf_counter()
        if self.mode:
            self.update_context(now)          # AFK rules + context tag (1 Hz)
        if self.mode == "train":
            self.update_afk(now)
        if self.mode and now >= self.next_eval:
            if self.mode == "train":
                self.train_eval(now)
            else:
                self.detect_eval(now)
        self.hooks.emit("engine.tick", now)

    # ---- evaluation -------------------------------------------------------------------------------
    def train_eval(self, now):
        self.next_eval = now + TRAIN_STEP
        key_lo = max(self._key_t, now - 1.5 * TRAIN_STEP)      # non-overlapping slice for per-key stats
        self._key_t = now
        recent = [a for t, a in self.afk_samples if t >= now - WINDOW_SEC]
        if recent and sum(recent) / len(recent) > AFK_MAX_FRAC:
            self.afk_skipped += 1
            info = {"state": "afk_skipped"}
            self.hooks.emit("train.window", info)
            return info
        vec = window_vector(self.rec.snapshot(WINDOW_SEC), WINDOW_SEC)
        if vec is None:
            self.skipped += 1
            info = {"state": "low_activity"}
            self.hooks.emit("train.window", info)
            return info
        tag = dominant_tag(self.ctx_samples, now, WINDOW_SEC)    # '' unless one tag held for the window
        nums = self._context_nums(now)
        self.profile.add_window(vec, tag, nums)
        self.new_windows += 1
        if self.track_keys:
            self.profile.add_key_slice(slice_key_stats(self.rec.snapshot(KEEP)[3], key_lo, now))
        cls = self.profile.classify()
        info = {"state": "window", "tag": tag, "class": cls,
                "class_text": f"{cls['code']} · {cls.get('playstyle', 'Not enough data')}",
                "ads_short": classify_ads(typical_vector(self.profile.windows))["short"],
                "embedding": self.profile.embedding()}
        self.hooks.emit("train.window", info)
        return info

    def detect_eval(self, now):
        self.next_eval = now + DETECT_STEP
        info = {"state": "paused", "time": datetime.now().strftime("%H:%M:%S")}
        paused, why = self._detect_paused(now)
        if paused:                                   # context AFK rule: pause scoring
            info["why"] = why
            return self._emit_detect(info)
        dur = min(WINDOW_SEC, now - self.t0)
        lo = max(self._det_key_t, now - 2 * DETECT_STEP)
        self._det_key_t = now
        self.live_slices.append(slice_key_stats(self.rec.snapshot(KEEP)[3], lo, now))
        live_agg = aggregate_slices(self.live_slices)
        info["live_agg"] = live_agg
        self._kb_scores = {}
        for p in self.det_profiles:
            self._kb_scores[p.name] = key_similarity(live_agg, p.keys, self.key_idf)
        info["kb_scores"] = dict(self._kb_scores)
        vec = window_vector(self.rec.snapshot(dur), dur)
        if vec is None:
            info["state"] = "waiting"
            return self._emit_detect(info)
        self.recent.append(vec)
        typ = typical_vector(list(self.recent))
        info["live_class"] = classify_vector(typ)
        info["live_embedding"] = embed(typ)
        cur_tag = dominant_tag(self.ctx_samples, now, WINDOW_SEC) if self.use_tags else ""
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
        info["state"] = "partial"                    # live class known, nothing scored yet
        if not self.ema:
            return self._emit_detect(info)
        thr = float(self.strict)
        info["state"] = "scored"
        info["threshold"] = thr
        info["ema"] = dict(self.ema)
        info["similarity"] = {name: 100.0 / (1.0 + (s / thr) ** 4) for name, s in self.ema.items()}
        best = min(self.ema, key=self.ema.get)
        info["best"] = best
        if self.ema[best] <= thr and best in self.banned_names:
            info["verdict"] = "banned"
        elif self.ema[best] <= thr:
            info["verdict"] = "match"
        else:
            info["verdict"] = "unknown"
        info["multi"] = len(self.det_profiles) > 1
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
        info["notes"] = notes
        ctx = ""
        if self.use_tags:
            if not cur_tag:
                ctx = "  |  context tag: none right now - whole fingerprint"
            elif tag_used:
                ctx = f"  |  context tag '{cur_tag}': matched against same-tag windows ({tag_used}/{len(self.det_profiles)} fingerprint(s))"
            else:
                ctx = f"  |  context tag '{cur_tag}': under {TAG_MIN_WINDOWS} matching windows - whole fingerprint"
        info["status"] = (f"Last check {datetime.now().strftime('%H:%M:%S')} "
                          f"(last {dur:.0f}s, {len(self.recent)} recent windows)" + ctx)
        return self._emit_detect(info)

    def _emit_detect(self, info):
        self.last_detect = info
        self.hooks.emit("detect.update", info)
        return info

    # ---- convenience ----------------------------------------------------------------------------
    @staticmethod
    def profile_names():
        return list_profiles()
