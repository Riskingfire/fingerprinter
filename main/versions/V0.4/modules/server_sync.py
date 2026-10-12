"""Server sync (optional): upload/download fingerprints and the ban list with the Render server in
./fingerprint-server. Headless - the settings dialog lives in the separate ui_server module.

Provides the service "server" (a ServerClient). Listens to:
    app.started        ping /health so a sleeping Render instance is awake by the time you need it
    profile.saved      auto-upload after training when "auto_upload" is on
    banlist.changed    (added: {name: entry}, removed: [names]) -> mirrored to the server
Settings live in server_config.json (it holds your API key, so don't share it).
"""
NAME = "server_sync"
DESCRIPTION = "Sync fingerprints and the ban list with the Render server (no UI)"
REQUIRES = []
ORDER = 20

import gzip
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from fp.settings import APP_DATA_DIR, MAX_BYTES
from fp.store import profile_path


CONFIG_FILE = os.path.join(APP_DATA_DIR, "server_config.json")
WAKE_TOTAL = 120.0       # keep pinging this long - a sleeping Render free instance takes ~30-60 s
WAKE_GAP = 3.0


def load_server_cfg():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:
        d = {}
    return {"url": str(d.get("url", "")).strip().rstrip("/"), "key": str(d.get("key", "")).strip(),
            "wake_on_start": bool(d.get("wake_on_start", True)),
            "auto_upload": bool(d.get("auto_upload", False))}


def save_server_cfg(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=1)
    try:
        os.chmod(CONFIG_FILE, 0o600)          # holds the API key
    except OSError:
        pass


def server_call(cfg, method, path, body=None, timeout=30, content_type="application/octet-stream",
                auth=True):
    """-> (http_status, bytes). Raises on network errors. Only https (or localhost) is allowed
    so the API key is never sent in clear text."""
    url = cfg.get("url", "")
    if not url:
        raise ValueError("no server URL set")
    if not url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        raise ValueError("server URL must start with https://")
    req = urllib.request.Request(url + path, data=body, method=method)
    if auth and cfg.get("key"):
        req.add_header("Authorization", "Bearer " + cfg["key"])
    if body is not None:
        req.add_header("Content-Type", content_type)
    req.add_header("User-Agent", "play-fingerprint/2")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def wake_server(cfg, state):
    """Ping /health until the server answers (wakes a sleeping Render instance).

    Runs in a background thread; `state` is a dict the UI polls (text / fg / ok)."""
    t0 = time.time()
    n = 0
    state.update(text="Server: waking up...", fg="#d77a00", ok=None)
    while time.time() - t0 < WAKE_TOTAL:
        n += 1
        try:
            code, _ = server_call(cfg, "GET", "/health", timeout=15, auth=False)
            if code == 200:
                state.update(text=f"Server: online ({time.time() - t0:.0f}s)", fg="#1a8a3a", ok=True)
                return True
            state.update(text=f"Server: HTTP {code}, retrying (try {n})...", fg="#d77a00")
        except ValueError as e:                      # bad / missing URL - retrying won't help
            state.update(text=f"Server: {e}", fg="#d11", ok=False)
            return False
        except Exception:                            # sleeping instances time out / reset while booting
            state.update(text=f"Server: waking up... ({time.time() - t0:.0f}s, try {n})", fg="#d77a00")
        time.sleep(WAKE_GAP)
    state.update(text="Server: unreachable", fg="#d11", ok=False)
    return False


def read_updated(path):
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return str(json.load(fh).get("updated", ""))
    except Exception:
        return ""


def _q(name):
    return urllib.parse.quote(name, safe="")


class ServerClient:
    """Server settings and helpers (these used to be App.srv_cfg / srv / _ensure_awake / _upload_one)."""

    def __init__(self):
        self.cfg = load_server_cfg()
        self.state = {"text": "Server: not set up (optional)", "fg": "#888", "ok": None, "dirty": False}
        self._wake_thread = None

    def configured(self):
        return bool(self.cfg["url"] and self.cfg["key"])

    def wake_on_start(self):
        """First load: ping the server so a sleeping Render instance starts waking right away."""
        if not self.cfg["url"]:
            return
        if not self.cfg["wake_on_start"]:
            self.state.update(text="Server: not pinged (disabled)", fg="#888")
            return
        self.start_wake()

    def start_wake(self):
        if self._wake_thread and self._wake_thread.is_alive():
            return
        self._wake_thread = threading.Thread(target=wake_server, args=(dict(self.cfg), self.state), daemon=True)
        self._wake_thread.start()

    def ensure_awake(self):
        if self.state.get("ok"):
            return True
        if self._wake_thread and self._wake_thread.is_alive():
            self._wake_thread.join(WAKE_TOTAL + 20)
            return bool(self.state.get("ok"))
        return wake_server(dict(self.cfg), self.state)

    def bg(self, fn):
        def work():
            try:
                fn()
            except Exception as e:
                print("server error:", e)
        threading.Thread(target=work, daemon=True).start()

    def upload_one(self, name):
        """-> (ok, message)"""
        if not self.ensure_awake():
            return False, "server not reachable"
        with open(profile_path(name), "rb") as fh:
            raw = fh.read()
        code, body = server_call(self.cfg, "PUT", "/api/fingerprints/" + _q(name), raw, timeout=60)
        if code in (200, 201):
            return True, "uploaded" if code == 201 else "updated"
        try:
            msg = json.loads(body).get("error", "")
        except Exception:
            msg = ""
        return False, f"HTTP {code} {msg}".strip()

    def push_ban(self, name, entry):
        self.bg(lambda: self.ensure_awake() and server_call(
            self.cfg, "PUT", "/api/banlist/" + _q(name), json.dumps(entry).encode(),
            content_type="application/json"))

    def delete_ban(self, name):
        self.bg(lambda: self.ensure_awake() and server_call(self.cfg, "DELETE", "/api/banlist/" + _q(name)))


def register(ctx):
    client = ctx.provide("server", ServerClient())
    ctx.provide("server_api", {"server_call": server_call, "wake_server": wake_server, "read_updated": read_updated,
                               "quote": _q, "save_cfg": save_server_cfg, "WAKE_TOTAL": WAKE_TOTAL,
                               "MAX_BYTES": MAX_BYTES})
    ctx.hooks.on("app.started", lambda *a, **k: client.wake_on_start())

    def on_saved(profile, size):
        if client.cfg.get("auto_upload") and client.configured():
            client.bg(lambda n=profile.name: client.upload_one(n))

    def on_ban_changed(added=None, removed=None):
        if not client.configured():
            return
        for n, entry in (added or {}).items():
            client.push_ban(n, entry)
        for n in (removed or []):
            client.delete_ban(n)

    ctx.hooks.on("profile.saved", on_saved)
    ctx.hooks.on("banlist.changed", on_ban_changed)
