"""Send the fingerprint file to the server (standard library only).

The same endpoints the client uses:  GET /health (wakes a sleeping Render instance)  and
PUT /api/fingerprints/<name>  with the raw .fpr file.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

WAKE_TOTAL = 120.0
WAKE_GAP = 3.0


def call(cfg, method, path, body=None, timeout=30, auth=True):
    """-> (http status, bytes). Only https (or localhost) so the key is never sent in clear text."""
    url = (cfg.get("url") or "").rstrip("/")
    if not url:
        raise ValueError("no server URL set")
    if not url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        raise ValueError("server URL must start with https://")
    req = urllib.request.Request(url + path, data=body, method=method)
    if auth and cfg.get("key"):
        req.add_header("Authorization", "Bearer " + cfg["key"])
    if body is not None:
        req.add_header("Content-Type", "application/octet-stream")
    req.add_header("User-Agent", "play-fingerprint-game/0.4")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def wake(cfg, total=WAKE_TOTAL):
    t0 = time.time()
    while time.time() - t0 < total:
        try:
            if call(cfg, "GET", "/health", timeout=15, auth=False)[0] == 200:
                return True
        except ValueError:
            return False
        except Exception:
            pass
        time.sleep(WAKE_GAP)
    return False


def configured(cfg):
    return bool(cfg and cfg.get("url") and cfg.get("key"))


def upload_file(cfg, name, path):
    """-> (ok, message)"""
    if not wake(cfg):
        return False, "server not reachable"
    with open(path, "rb") as fh:
        raw = fh.read()
    code, body = call(cfg, "PUT", "/api/fingerprints/" + urllib.parse.quote(name, safe=""), raw, timeout=60)
    if code in (200, 201):
        return True, "uploaded" if code == 201 else "updated"
    try:
        msg = json.loads(body).get("error", "")
    except Exception:
        msg = ""
    return False, f"HTTP {code} {msg}".strip()
