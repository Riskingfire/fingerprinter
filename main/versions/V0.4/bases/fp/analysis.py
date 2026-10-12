"""Feature weighting, stability, automation heuristics and ban-evasion screening maths."""
import warnings

from .features import IDX, distance
from .profile import Profile
from .keybinds import key_similarity
from .store import list_profiles, profile_path

import numpy as np



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
