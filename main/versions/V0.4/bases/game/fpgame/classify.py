"""Human-readable style classes (compact class code) from a typical feature vector."""
import numpy as np
import warnings

from .settings import KB_PACE_CUTS, KB_PACE_NAMES, KB_STYLE_CUTS, KB_STYLE_NAMES, MS_PACE_CUTS, MS_PACE_NAMES, MS_STYLE_CUTS, MS_STYLE_NAMES

from .features import IDX


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


def _range_text(cuts, unit=""):
    parts = [f"<{cuts[0]}"] + [f"{a}-{b}" for a, b in zip(cuts, cuts[1:])] + [f">{cuts[-1]}"]
    return ", ".join(parts) + unit
