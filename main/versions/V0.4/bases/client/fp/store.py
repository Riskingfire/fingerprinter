"""Fingerprint files on disk + the local ban list."""
import os
import gzip
import json

from .settings import BAN_FILE, EXT, PROFILE_DIR

import numpy as np



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


def tags_line(p):
    """One-line summary of a profile's context tags ('' if none)."""
    c = p.tag_counts()
    if not c:
        return ""
    return "Context tags: " + ", ".join(f"{t} x{n}" for t, n in c.most_common(6))


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
