"""Connection settings: the same server_config.json the client uses, so connecting once works for both.

Lookup order: --config path, FP_SERVER_CONFIG, ./server_config.json (next to the launcher),
then %USERPROFILE%/AppData/fingerprint/server_config.json (the client's file). Unknown keys are preserved on save.
"""
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT_FILE = os.path.join(os.path.expanduser("~"), "AppData", "fingerprint", "server_config.json")


def find_path(explicit=None):
    for p in (explicit, os.environ.get("FP_SERVER_CONFIG"), os.path.join(APP_DIR, "server_config.json"), CLIENT_FILE):
        if p and os.path.exists(p):
            return p
    return explicit or os.environ.get("FP_SERVER_CONFIG") or os.path.join(APP_DIR, "server_config.json")


def load(path=None):
    path = find_path(path)
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception:
        raw = {}
    cfg = dict(raw)
    cfg["url"] = str(raw.get("url", "")).strip().rstrip("/")
    cfg["key"] = str(raw.get("key", "")).strip()
    cfg["_path"] = path
    return cfg


def save(cfg):
    path = cfg.get("_path") or find_path()
    data = {k: v for k, v in cfg.items() if not k.startswith("_")}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    try:
        os.chmod(path, 0o600)                     # holds the API key
    except OSError:
        pass
    return path
