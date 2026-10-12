"""Fingerprint profile: windows, statistics, matching parameters, gzip-JSON file format."""
import os
import gzip
import json
from collections import Counter
from datetime import datetime
import math
import warnings

from .settings import FEATURE_NAMES, KEY_MAX, MAX_BYTES, MAX_WINDOWS, NAN, NUM_MAX, TAG_MAX_LEN, TAG_MIN_WINDOWS, WINDOW_SEC
from .keybinds import _parse_keys
from .classify import classify_vector, typical_vector
from .features import distance
from .embedding import embed, fingerprint_id

import numpy as np



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
