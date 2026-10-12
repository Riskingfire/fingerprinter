"""Continuous fingerprint embedding + unbounded-resolution ID."""
import numpy as np

from .settings import FEATURE_NAMES_V1



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
