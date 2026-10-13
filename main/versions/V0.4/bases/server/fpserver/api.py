"""Thin client for the fingerprint server's REST API (standard library only)."""
import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request

WAKE_TOTAL = 120.0
WAKE_GAP = 3.0


class ApiError(Exception):
    def __init__(self, code, message=""):
        super().__init__(f"HTTP {code} {message}".strip())
        self.code = code


def _q(name):
    return urllib.parse.quote(name, safe="")


class ServerApi:
    def __init__(self, cfg):
        self.cfg = cfg                            # {"url": ..., "key": ...}

    def call(self, method, path, body=None, timeout=30, auth=True, ctype="application/json"):
        """-> (status, bytes). Only https (or localhost) so the key never travels in clear text."""
        url = (self.cfg.get("url") or "").rstrip("/")
        if not url:
            raise ValueError("no server URL set")
        if not url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise ValueError("server URL must start with https://")
        req = urllib.request.Request(url + path, data=body, method=method)
        if auth and self.cfg.get("key"):
            req.add_header("Authorization", "Bearer " + self.cfg["key"])
        if body is not None:
            req.add_header("Content-Type", ctype)
        req.add_header("User-Agent", "play-fingerprint-viewer/0.4")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def _json(self, method, path, body=None, timeout=30, ok=(200, 201)):
        code, raw = self.call(method, path, body, timeout)
        if code not in ok:
            msg = ""
            try:
                msg = json.loads(raw).get("error", "")
            except Exception:
                pass
            raise ApiError(code, "bad API key" if code == 401 and not msg else msg)
        return json.loads(raw) if raw else {}

    def wake(self, progress=None, total=WAKE_TOTAL):
        """Ping /health until the server answers (a sleeping Render instance needs 30-60 s). -> bool"""
        t0 = time.time()
        n = 0
        while time.time() - t0 < total:
            n += 1
            try:
                if self.call("GET", "/health", timeout=15, auth=False)[0] == 200:
                    return True
            except ValueError:
                raise
            except Exception:
                pass
            if progress:
                progress(f"waking the server... {time.time() - t0:.0f}s (try {n})")
            time.sleep(WAKE_GAP)
        return False

    def status(self):
        return self._json("GET", "/api/status")

    def fingerprints(self):
        d = self._json("GET", "/api/fingerprints")
        return d if isinstance(d, list) else d.get("fingerprints", [])

    def fingerprint_raw(self, name):
        code, raw = self.call("GET", "/api/fingerprints/" + _q(name), timeout=60)
        if code != 200:
            raise ApiError(code)
        return raw

    def banlist(self):
        d = self._json("GET", "/api/banlist")
        return d if isinstance(d, dict) else {}

    def ban(self, name, reason="", date=""):
        entry = {"reason": reason, "date": date or time.strftime("%Y-%m-%d")}
        self._json("PUT", "/api/banlist/" + _q(name), json.dumps(entry).encode())
        return entry

    def unban(self, name):
        self._json("DELETE", "/api/banlist/" + _q(name))

    def analysis(self):
        """The server's approximate ban-evasion analysis -> list of result dicts."""
        return self._json("POST", "/api/analysis", b"{}", timeout=120).get("results", [])


def summarize(raw):
    """Read the interesting header fields of a downloaded .fpr (gzip JSON) without needing numpy."""
    d = json.loads(gzip.decompress(raw).decode("utf-8"))
    return {"name": d.get("name"), "class": (d.get("class") or {}).get("code"), "id": d.get("id"),
            "updated": d.get("updated"), "created": d.get("created"), "minutes": round(float(d.get("seconds_recorded") or 0) / 60, 1),
            "windows": len(d.get("windows") or []), "stability": d.get("stability"),
            "playstyle": (d.get("class") or {}).get("playstyle"), "keys_tracked": bool(d.get("keys"))}


LEVELS = {"all": ("NONE", "REVIEW", "HIGH"), "review": ("REVIEW", "HIGH"), "high": ("HIGH",)}


def filter_results(results, level="review"):
    """Keep results at/above a flag level, most suspicious first (HIGH, then closest)."""
    order = {"HIGH": 0, "REVIEW": 1, "NONE": 2}
    keep = LEVELS.get(level, LEVELS["review"])
    rows = [r for r in results if r.get("flag", "NONE") in keep]
    return sorted(rows, key=lambda r: (order.get(r.get("flag"), 3), float(r.get("distance") or 9e9)))
