"""Per-key statistics, keybind signature and keybind similarity."""
import gzip
import json

from .settings import COMMON_BINDS, CORE_SHARE, KEY_CLASSES, MOVE_SHARE, RARE_SHARE, SCHEMES




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
