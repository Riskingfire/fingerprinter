"""IN-GAME INTEGRATIONS (optional module; the "Integrations" tab comes from ui_integrations)
---------------------------------------------------
The tool can also read GAME STATE (which mode / situation the player is in) and use it two ways:
  1. AFK RULES - "while state X is active, count me as AFK unless condition Y holds". Windows
     that overlap such time are skipped in Train (same AFK_MAX_FRAC logic as the manual key and
     idle auto-detect) and scoring is paused in Detect while a rule applies.
  2. CONTEXT TAGS - chosen game values become a short tag (e.g. "mode=a") saved per training
     window. In Detect you may tick "Compare only against windows with the current context tag";
     it is used only once 10+ windows share the tag, otherwise the whole fingerprint is used.

SANCTIONED SOURCES ONLY. Integrations may only consume data a game deliberately provides: its
official game-state / telemetry feature, official APIs or SDKs, documented log files, or data
your own game config exports. This tool never reads process memory, never injects into or hooks
a game process, never scrapes the screen and never touches anti-cheat-protected components - and
it must not be extended to do so.

Source types (each runs on its own daemon thread with bounded memory; a crash, disconnect or
malformed payload only shows up as an error in the tab - input recording and the Tk loop are
never affected):
  http_push  local-only listener on 127.0.0.1 that accepts JSON POSTed by a game's state feature.
             Needs a shared token (header "Authorization: Bearer <t>", "X-Integration-Token", or
             "auth.token" in the JSON) and caps the body size.
  http_poll  polls a local HTTP endpoint (127.0.0.1 / localhost only, no redirects) for JSON.
  log_tail   follows a log/telemetry file and extracts values with configurable regexes.
  udp        local-only UDP listener (127.0.0.1) with a pluggable packet decoder
             ("json", "text_kv", "struct", or one registered by a plugin).
  callback   runs a Python function you register: fn(emit, stop_event).
A value that stops updating goes stale (stale_after seconds) and falls back to its default:
rules do not apply and tags are omitted (a rule can opt into on_unknown="afk" instead).

PRIVACY. Raw game payloads are held only in memory (bounded, flattened, scalars only) and are
never written to disk or uploaded. Only the derived context tag - and optionally a few custom
numeric context values - is stored in a .fpr file (optional "window_tags" / "window_num" fields;
files without them load exactly as before, and files without tags are written exactly as before).

OPT-IN. Nothing listens until you press Start on an integration. With no config file, nothing
exists. Entries need "enabled": true. Listeners bind to 127.0.0.1 only. Reloading the config is
blocked while a Train/Detect session is running.

Config: integrations.json in the app-data folder next to server_config.json. The
"Write example config" button writes the template below (everything disabled; placeholders only).
Optional plugins live in the integration_plugins folder next to it: *.json files are declarative
field mappings {"name", "rename", "drop", "keep_only"}; *.py files must define register(api)
and are loaded only if "plugins_enabled" is true (they run as your own code - only put your own
files there).

integrations.json example:
    {
      "version": 1,
      "plugins_enabled": false,
      "stale_after": 15,
      "integrations": {
        "example_push": {"kind": "http_push", "enabled": false, "port": 3000,
                         "token": "REPLACE_WITH_A_LONG_RANDOM_STRING", "max_body": 65536},
        "example_poll": {"kind": "http_poll", "enabled": false,
                         "url": "http://127.0.0.1:8080/state.json", "interval": 1.0},
        "example_log": {"kind": "log_tail", "enabled": false, "path": "C:/path/to/game_log.txt",
                        "patterns": [{"key": "mode", "regex": "mode=([a-z_]+)"}]},
        "example_udp": {"kind": "udp", "enabled": false, "port": 40000, "decoder": "json"},
        "example_cb": {"kind": "callback", "enabled": false, "callback": "my_callback_name"}
      },
      "afk_rules": [
        {"name": "menu_unless_moving",
         "when": {"ref": "example_push.game.screen", "op": "in", "value": ["menu", "lobby"]},
         "unless": {"ref": "example_push.player.moving", "op": "==", "value": true},
         "on_unknown": "ignore"}
      ],
      "tags": [
        {"name": "mode", "ref": "example_push.game.mode",
         "map": {"mode_a": "a", "mode_b": "b"}, "other": "other"}
      ],
      "numeric": []
    }
    --- end example ---
Refs are "<integration name>.<flattened.json.path>". Condition ops: == != > >= < <= in not_in
contains regex exists missing, combinable with {"all": [...]}, {"any": [...]}, {"not": {...}}.

As a module this provides the service "integrations" (an IntegrationManager) and answers the engine's
context.sample / context.nums hooks, so AFK rules and context tags work with or without a UI.
"""
NAME = "integrations"
DESCRIPTION = "In-game state sources (HTTP push/poll, log tail, UDP, callbacks): AFK rules + context tags"
REQUIRES = []
ORDER = 10

import hmac
import math
import http.server
import importlib.util
import json
import os
import re
import socket
import struct
import threading
import time
import types
import urllib.error
import urllib.parse
import urllib.request
import queue
from collections import deque

from fp.settings import APP_DATA_DIR, TAG_MAX_LEN, NUM_MAX

INTEG_CONFIG_FILE = os.path.join(APP_DATA_DIR, "integrations.json")
INTEG_PLUGIN_DIR = os.path.join(APP_DATA_DIR, "integration_plugins")
INTEG_MAX_CFG_BYTES = 256 * 1024
INTEG_MAX_SOURCES = 16
INTEG_STALE_DEFAULT = 15.0   # seconds without an update before a value falls back to its default
INTEG_BODY_DEFAULT = 64 * 1024
INTEG_BODY_HARD_MAX = 1024 * 1024
INTEG_MAX_KEYS = 64          # flattened values kept per integration
INTEG_MAX_KEY_LEN = 64
INTEG_MAX_STR = 128
INTEG_MAX_DEPTH = 6
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

INTEG_NOTICE = ("Sanctioned data sources only: a game's official state/telemetry feature, official APIs or "
                "SDKs, documented log files, or data your own game config exports. This tool never reads game "
                "process memory, injects into or hooks game processes, scrapes the screen, or touches "
                "anti-cheat-protected components. Everything here is off until you press Start, listeners "
                "bind to 127.0.0.1 only, and only derived context tags are saved - never raw game data.")


def _clamp(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return default if v != v else max(lo, min(hi, v))


def _ident(s, n=32):
    """Integration / tag names: letters, digits, _ and - only (no dots: refs are 'name.key')."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(s)).strip("_")[:n]


def _slug(s, n=TAG_MAX_LEN):
    return re.sub(r"[^A-Za-z0-9_.+-]+", "_", str(s)).strip("_")[:n]


def _auto_value(s):
    """'12' -> 12, '3.5' -> 3.5, 'true' -> True, anything else stays a (bounded) string."""
    s = str(s).strip()
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(s)
    except ValueError:
        pass
    try:
        f = float(s)
        if math.isfinite(f):
            return f
    except ValueError:
        pass
    return s[:INTEG_MAX_STR]


def _clean_scalar(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v if abs(v) < 2 ** 53 else None
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, str):
        return v[:INTEG_MAX_STR]
    return None                       # None, lists, dicts, bytes ... are never kept


def flatten_payload(obj, prefix="", out=None, depth=0):
    """Nested JSON -> {'a.b.0': scalar}. Bounded in depth, key count, key length and string length."""
    if out is None:
        out = {}
    if len(out) >= INTEG_MAX_KEYS or depth > INTEG_MAX_DEPTH:
        return out
    if isinstance(obj, dict):
        items = obj.items()
    elif isinstance(obj, (list, tuple)):
        items = enumerate(obj)
    else:
        v = _clean_scalar(obj)
        if v is not None and prefix:
            out[prefix[:INTEG_MAX_KEY_LEN]] = v
        return out
    for k, v in items:
        if len(out) >= INTEG_MAX_KEYS:
            break
        k = str(k)[:INTEG_MAX_KEY_LEN]
        flatten_payload(v, f"{prefix}.{k}" if prefix else k, out, depth + 1)
    return out


class GameState:
    """Thread-safe, bounded, in-memory store of the latest scalar game values per source.

    Values go stale: snapshot() only returns values updated within the source's stale_after
    seconds, so a source that stops updating simply falls back to 'no data' (the defaults)."""

    def __init__(self, clock=time.monotonic, stale_after=INTEG_STALE_DEFAULT):
        self._lock = threading.Lock()
        self._data = {}            # source -> {key: (value, time)}
        self._stale = {}
        self._clock = clock
        self.default_stale = stale_after

    def set_stale(self, source, secs):
        self._stale[source] = _clamp(secs, 1.0, 3600.0, self.default_stale)

    def _limit(self, source):
        return self._stale.get(source, self.default_stale)

    def update(self, source, values, replace=False):
        """Store sanitised scalars. replace=True drops the source's earlier keys first."""
        if not isinstance(values, dict):
            return 0
        clean = {}
        for k, v in list(values.items())[:INTEG_MAX_KEYS * 4]:
            k = str(k)[:INTEG_MAX_KEY_LEN]
            v = _clean_scalar(v)
            if k and v is not None and len(clean) < INTEG_MAX_KEYS:
                clean[k] = v
        now = self._clock()
        with self._lock:
            lim = self._limit(source)
            cur = {} if replace else {k: e for k, e in self._data.get(source, {}).items()
                                      if now - e[1] <= lim}
            for k, v in clean.items():
                if k in cur or len(cur) < INTEG_MAX_KEYS:
                    cur[k] = (v, now)
            self._data[source] = cur
        return len(clean)

    def snapshot(self, now=None):
        """{'source.key': value} for every non-stale value."""
        now = self._clock() if now is None else now
        out = {}
        with self._lock:
            for src, d in self._data.items():
                lim = self._limit(src)
                for k, (v, t) in d.items():
                    if now - t <= lim:
                        out[f"{src}.{k}"] = v
        return out

    def clear(self, source=None):
        with self._lock:
            if source is None:
                self._data.clear()
            else:
                self._data.pop(source, None)


# ---- conditions, AFK rules, context tags (pure functions) ---------------------------------
_COND_OPS = {"==", "!=", ">", ">=", "<", "<=", "in", "not_in", "contains", "regex", "exists", "missing"}


def _norm_eq(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return str(a).lower() == str(b).lower()
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def valid_cond(c, depth=0):
    """Is this a well-formed condition (leaf, all / any / not)?"""
    if not isinstance(c, dict) or depth > 4:
        return False
    for k in ("all", "any"):
        if k in c:
            return isinstance(c[k], list) and 0 < len(c[k]) <= 8 and all(valid_cond(x, depth + 1) for x in c[k])
    if "not" in c:
        return valid_cond(c["not"], depth + 1)
    ref, op = c.get("ref"), c.get("op", "==")
    if not isinstance(ref, str) or "." not in ref or op not in _COND_OPS:
        return False
    if op in ("in", "not_in") and not isinstance(c.get("value"), list):
        return False
    if op == "regex":
        try:
            re.compile(str(c.get("value", "")))
        except re.error:
            return False
    return True


def eval_cond(cond, values):
    """True / False, or None when the data the condition needs is missing or stale."""
    if not isinstance(cond, dict):
        return None
    if "all" in cond or "any" in cond:
        is_all = "all" in cond
        rs = [eval_cond(c, values) for c in cond["all" if is_all else "any"]]
        decisive = False if is_all else True
        if any(r is decisive for r in rs):
            return decisive
        return None if any(r is None for r in rs) else is_all
    if "not" in cond:
        r = eval_cond(cond["not"], values)
        return None if r is None else (not r)
    ref, op, want = cond.get("ref"), cond.get("op", "=="), cond.get("value")
    present = ref in values
    if op == "exists":
        return present
    if op == "missing":
        return not present
    if not present:
        return None
    have = values[ref]
    try:
        if op in ("==", "eq"):
            return _norm_eq(have, want)
        if op == "!=":
            return not _norm_eq(have, want)
        if op == ">":
            return float(have) > float(want)
        if op == ">=":
            return float(have) >= float(want)
        if op == "<":
            return float(have) < float(want)
        if op == "<=":
            return float(have) <= float(want)
        if op == "in":
            return any(_norm_eq(have, w) for w in want)
        if op == "not_in":
            return not any(_norm_eq(have, w) for w in want)
        if op == "contains":
            return str(want).lower() in str(have).lower()
        if op == "regex":
            return re.search(str(want), str(have)) is not None
    except (TypeError, ValueError, re.error):
        return None
    return None


def afk_rule_check(rules, values):
    """-> (applies, rule_name): 'while <when> is true, count me as AFK unless <unless> is true'.

    If the data a rule needs is missing/stale the rule does NOT apply, unless it sets
    on_unknown="afk"."""
    for r in rules:
        w = eval_cond(r["when"], values)
        if w is False:
            continue
        u = eval_cond(r["unless"], values) if r.get("unless") else False
        if u is True:
            continue
        if w is True and u is False:
            return True, r["name"]
        if r.get("on_unknown") == "afk":
            return True, r["name"] + " (no data)"
    return False, None


def tag_value(d, values):
    """One tag definition -> short tag string or None. Numbers need map/bins (keeps tags coarse)."""
    v = values.get(d["ref"])
    if v is None:
        return d.get("default") or None
    mp, bins = d.get("map"), d.get("bins")
    if mp:
        key = str(v).lower() if isinstance(v, bool) else str(v)
        hit = mp.get(key, mp.get(key.lower()))
        return str(hit) if hit is not None else d.get("other", "other")
    if bins:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return None
        i = sum(x >= c for c in bins)
        if i == 0:
            return f"lt{bins[0]:g}"
        return f"ge{bins[-1]:g}" if i == len(bins) else f"{bins[i - 1]:g}to{bins[i]:g}"
    if isinstance(v, float) and not v.is_integer():
        return None
    return str(int(v)) if isinstance(v, float) else str(v)


def compute_tag(defs, values):
    """Combine all tag definitions into one derived context tag ('' = untagged)."""
    parts = []
    for d in defs:
        t = tag_value(d, values)
        if t:
            parts.append(f"{_slug(d['name'], 16)}={_slug(t, 24)}")
    return "|".join(parts)[:TAG_MAX_LEN]


def compute_nums(defs, values):
    out = {}
    for d in defs[:NUM_MAX]:
        v = values.get(d["ref"])
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
            out[d["name"]] = round(float(v), 4)
    return out or None


# ---- decoders / callbacks / mappers (plugin extension points) ------------------------------
DECODERS, CALLBACKS, MAPPERS = {}, {}, {}


def register_decoder(name, fn):
    """fn(data: bytes, cfg: dict) -> {key: scalar}. Used by the udp source (cfg 'decoder')."""
    DECODERS[str(name)] = fn


def register_callback(name, fn):
    """fn(emit, stop_event): produce values with emit({...}); return when stop_event is set."""
    CALLBACKS[str(name)] = fn


def register_mapper(name, fn):
    """fn({key: scalar}) -> {key: scalar}. Per-game field mapping (cfg 'mapper' on any integration)."""
    MAPPERS[str(name)] = fn


def make_json_mapper(spec):
    """Declarative per-game mapping: {"rename": {old: new}, "drop": [prefix...], "keep_only": bool}."""
    rename = {str(k): str(v) for k, v in dict(spec.get("rename", {})).items()}
    drop = tuple(str(x) for x in spec.get("drop", []))
    keep_only = bool(spec.get("keep_only", False))

    def fn(values):
        out = {}
        for k, v in values.items():
            if drop and k.startswith(drop):
                continue
            if k in rename:
                out[rename[k]] = v
            elif not keep_only:
                out[k] = v
        return out
    return fn


def _decode_json(data, cfg):
    return flatten_payload(json.loads(data.decode("utf-8")))


def _decode_text_kv(data, cfg):
    out = {}
    for line in data.decode("utf-8", "replace").splitlines()[:INTEG_MAX_KEYS]:
        if "=" in line:
            k, v = line.split("=", 1)
            if k.strip():
                out[k.strip()[:INTEG_MAX_KEY_LEN]] = _auto_value(v)
    return out


_STRUCT_FMT = re.compile(r"^[<>=!@]?[bBhHiIlLqQfd?]$")


def _decode_struct(data, cfg):
    """Fixed binary layout from config: fields = [{"key", "offset", "fmt": "<f", "scale"?}]."""
    out = {}
    for f in list(cfg.get("fields", []))[:INTEG_MAX_KEYS]:
        fmt, off = str(f.get("fmt", "")), int(f.get("offset", 0))
        if not _STRUCT_FMT.match(fmt) or off < 0 or off + struct.calcsize(fmt) > len(data):
            continue
        v = struct.unpack_from(fmt, data, off)[0]
        if isinstance(v, (int, float)) and not isinstance(v, bool) and f.get("scale"):
            v = v * float(f["scale"])
        out[str(f.get("key", f"f{off}"))[:INTEG_MAX_KEY_LEN]] = v
    return out


DECODERS.update({"json": _decode_json, "text_kv": _decode_text_kv, "struct": _decode_struct})


# ---- sources ------------------------------------------------------------------------------
class IntegrationSource:
    """Base class: one daemon thread, bounded memory, never raises into the caller.

    Subclasses implement open() (bind/validate - runs in start(), so errors show up at once),
    run() (loop until self._stop is set) and close() (release resources)."""
    kind = "base"

    def __init__(self, name, cfg, state, clock=time.monotonic, mapper=None):
        self.name, self.cfg, self.state, self._clock, self.mapper = name, dict(cfg or {}), state, clock, mapper
        self.status = "stopped"          # stopped | running | error | finished
        self.last_update = None
        self.last_error, self.last_error_t = "", None
        self.count = self.bad = 0
        self._stop = threading.Event()
        self._thread = None
        state.set_stale(name, self.cfg.get("stale_after", state.default_stale))

    # -- to override
    def open(self):
        pass

    def run(self):
        raise NotImplementedError

    def close(self):
        pass

    def describe(self):
        return ""

    # -- shared plumbing
    def fail(self, e, where=""):
        text = e if isinstance(e, str) else f"{type(e).__name__}: {e}"
        self.last_error = (f"{where}: " if where else "") + text[:200]
        self.last_error_t = self._clock()

    def malformed(self, why):
        self.bad += 1
        self.fail(why)

    def push(self, values, replace=False):
        """Hand a decoded payload to the shared state (mapper applied, everything sanitised)."""
        if self._stop.is_set():
            return 0
        if self.mapper:
            try:
                values = self.mapper(values) or {}
            except Exception as e:
                self.fail(e, "mapper")
                return 0
        n = self.state.update(self.name, values, replace)
        self.last_update = self._clock()
        self.count += 1
        return n

    def active(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.active():
            self.fail("previous run is still shutting down")
            return False
        self._stop.clear()
        self.last_error, self.last_error_t = "", None
        try:
            self.open()
        except Exception as e:
            self.fail(e)
            self.status = "error"
            try:
                self.close()
            except Exception:
                pass
            return False
        self.status = "running"
        self._thread = threading.Thread(target=self._guard, name=f"integration-{self.name}", daemon=True)
        self._thread.start()
        return True

    def _guard(self):
        fails = 0
        while not self._stop.is_set():
            try:
                self.status = "running"
                self.run()
                if not self._stop.is_set():
                    self.status = "finished"
                return
            except Exception as e:                       # a crash must never propagate
                fails += 1
                self.fail(e, "crashed")
                self.status = "error"
                if self._stop.wait(min(30.0, 2.0 ** min(fails, 5))):
                    return
                try:                                     # reopen sockets / files, then run again
                    self.close()
                    self.open()
                except Exception as e2:
                    self.fail(e2, "reopen")

    def stop(self):
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(2.0)
        try:
            self.close()
        except Exception:
            pass
        self.state.clear(self.name)                      # stopped = no data = defaults apply
        self.status = "stopped"


class _LoopbackServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = False      # never share a port (matters on Windows)
    daemon_threads = True


class HttpPushSource(IntegrationSource):
    """127.0.0.1-only listener for JSON pushed by a game's official state-integration feature."""
    kind = "http_push"

    def open(self):
        token = str(self.cfg.get("token", ""))
        if len(token) < 8:
            raise ValueError("http_push needs a shared 'token' of at least 8 characters")
        try:
            port = int(self.cfg.get("port", 0))
        except (TypeError, ValueError):
            raise ValueError("invalid port")
        if not 0 <= port <= 65535:
            raise ValueError("invalid port")
        self._token = token.encode("utf-8")
        self.max_body = int(_clamp(self.cfg.get("max_body"), 256, INTEG_BODY_HARD_MAX, INTEG_BODY_DEFAULT))
        src, sem = self, threading.BoundedSemaphore(8)

        class Handler(http.server.BaseHTTPRequestHandler):
            timeout = 5

            def log_message(self, *a):
                pass

            def _reply(self, code):
                try:
                    self.send_response(code)
                    self.send_header("Content-Length", "0")
                    self.send_header("Connection", "close")
                    self.end_headers()
                except Exception:
                    pass

            def do_POST(self):
                if not sem.acquire(blocking=False):
                    return self._reply(503)
                try:
                    code = src._handle(self)
                except Exception as e:
                    src.malformed(f"request failed: {type(e).__name__}")
                    code = 400
                finally:
                    sem.release()
                self._reply(code)

            do_PUT = do_POST

        self._srv = _LoopbackServer(("127.0.0.1", port), Handler)   # localhost only, always
        self._srv.timeout = 0.25
        self.port = self._srv.server_address[1]

    def _token_ok(self, supplied):
        return isinstance(supplied, str) and hmac.compare_digest(supplied.encode("utf-8"), self._token)

    def _handle(self, h):
        try:
            n = int(h.headers.get("Content-Length", ""))
        except ValueError:
            return 411
        if n < 0:
            return 400
        if n > self.max_body:
            self.malformed(f"body of {n} bytes exceeds max_body ({self.max_body})")
            return 413
        raw = h.rfile.read(n)
        if len(raw) != n:
            return 400
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError):
            self.malformed("body is not valid JSON")
            return 400
        auth = h.headers.get("Authorization", "")
        supplied = auth[7:] if auth.lower().startswith("bearer ") else h.headers.get("X-Integration-Token", "")
        if not self._token_ok(supplied) and not (
                isinstance(obj, dict) and isinstance(obj.get("auth"), dict) and self._token_ok(obj["auth"].get("token"))):
            self.malformed("rejected a request with a missing or wrong token")
            return 401
        if not isinstance(obj, dict):
            self.malformed("JSON body must be an object")
            return 400
        self.push(flatten_payload({k: v for k, v in obj.items() if k != "auth"}), replace=True)   # token never stored
        return 200

    def run(self):
        while not self._stop.is_set():
            self._srv.handle_request()          # returns after <= 0.25 s so stop() is prompt

    def close(self):
        srv, self._srv = getattr(self, "_srv", None), None
        if srv is not None:
            srv.server_close()

    def describe(self):
        return f"127.0.0.1:{getattr(self, 'port', '?')}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class HttpPollSource(IntegrationSource):
    """Polls a LOCAL http endpoint (127.0.0.1 / localhost only, no proxies, no redirects) for JSON."""
    kind = "http_poll"

    def open(self):
        self.url = str(self.cfg.get("url", ""))
        u = urllib.parse.urlsplit(self.url)
        if u.scheme not in ("http", "https") or (u.hostname or "").lower() not in LOOPBACK_HOSTS:
            raise ValueError("http_poll url must point at 127.0.0.1 / localhost")
        self.interval = _clamp(self.cfg.get("interval"), 0.25, 3600.0, 1.0)
        self.timeout = _clamp(self.cfg.get("timeout"), 0.5, 10.0, 3.0)
        self.max_bytes = int(_clamp(self.cfg.get("max_bytes"), 1024, INTEG_BODY_HARD_MAX, 256 * 1024))
        self.headers = {str(k)[:64]: str(v)[:256] for k, v in list(dict(self.cfg.get("headers", {})).items())[:8]}
        self.headers.setdefault("User-Agent", "play-fingerprint/2")
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
        self._desc = f"{u.hostname}:{u.port or (443 if u.scheme == 'https' else 80)}{u.path}"

    def run(self):
        while not self._stop.is_set():
            try:
                with self._opener.open(urllib.request.Request(self.url, headers=self.headers),
                                       timeout=self.timeout) as r:
                    raw = r.read(self.max_bytes + 1)
                if len(raw) > self.max_bytes:
                    self.malformed(f"response larger than max_bytes ({self.max_bytes})")
                else:
                    self.push(flatten_payload(json.loads(raw.decode("utf-8"))), replace=True)
            except Exception as e:                      # endpoint down / bad JSON: keep trying, values go stale
                self.fail(e, "poll")
            self._stop.wait(self.interval)

    def describe(self):
        return getattr(self, "_desc", "")


class LogTailSource(IntegrationSource):
    """Follows a documented log / telemetry file and extracts values with configurable regexes."""
    kind = "log_tail"

    def open(self):
        self.path = os.path.expanduser(str(self.cfg.get("path", "")))
        if not self.path:
            raise ValueError("log_tail needs a 'path'")
        self.pats = []
        for p in list(self.cfg.get("patterns", []))[:16]:
            grp = p.get("group", 1)
            self.pats.append((str(p["key"])[:INTEG_MAX_KEY_LEN], re.compile(str(p["regex"])[:512]),
                              grp, p.get("map") or {}))
        if not self.pats:
            raise ValueError("log_tail needs at least one pattern {key, regex}")
        self.interval = _clamp(self.cfg.get("interval"), 0.1, 60.0, 0.5)
        self._pos = self._ino = None
        self._buf = b""

    def _poll_once(self):
        st = os.stat(self.path)
        if self._pos is None:                           # start at the end: never replay old history
            self._pos = 0 if self.cfg.get("from_start") else st.st_size
            self._ino = st.st_ino
        if st.st_size < self._pos or st.st_ino != self._ino:       # truncated or rotated
            self._pos, self._ino, self._buf = 0, st.st_ino, b""
        if st.st_size <= self._pos:
            return
        with open(self.path, "rb") as fh:
            fh.seek(self._pos)
            chunk = fh.read(min(st.st_size - self._pos, 256 * 1024))
        self._pos += len(chunk)
        lines = (self._buf + chunk).split(b"\n")
        self._buf = lines.pop()[-4096:]                 # incomplete last line, bounded
        out = {}
        for line in lines[-500:]:
            text = line[:2000].decode("utf-8", "replace")
            for key, rx, grp, mp in self.pats:
                m = rx.search(text)
                if m:
                    v = m.group(grp)
                    if v is not None:
                        out[key] = mp.get(v, _auto_value(v)) if mp else _auto_value(v)
        if out:
            self.push(out)

    def run(self):
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as e:                      # missing file, bad group, permissions ...
                self.fail(e, "tail")
            self._stop.wait(self.interval)

    def describe(self):
        return os.path.basename(getattr(self, "path", "")) or ""


class UdpSource(IntegrationSource):
    """127.0.0.1-only UDP listener; packets are turned into values by a pluggable decoder."""
    kind = "udp"

    def open(self):
        try:
            port = int(self.cfg.get("port", 0))
        except (TypeError, ValueError):
            raise ValueError("invalid port")
        name = str(self.cfg.get("decoder", "json"))
        self._fn = DECODERS.get(name)
        if self._fn is None:
            raise ValueError(f"unknown decoder {name!r} (known: {', '.join(sorted(DECODERS))})")
        self.max_packet = int(_clamp(self.cfg.get("max_packet"), 16, 65507, 2048))
        self.max_rate = _clamp(self.cfg.get("max_rate"), 1, 5000, 500)      # packets/second kept
        self._win_t, self._win_n, self.dropped = 0.0, 0, 0
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(0.5)
            s.bind(("127.0.0.1", port))                 # localhost only, always
        except Exception:
            s.close()
            raise
        self._sock, self.port = s, s.getsockname()[1]

    def run(self):
        while not self._stop.is_set():
            try:
                data, _ = self._sock.recvfrom(self.max_packet + 1)
            except socket.timeout:
                continue
            except ConnectionResetError:                # Windows reports ICMP errors on UDP sockets
                continue
            now = time.monotonic()
            if now - self._win_t >= 1.0:
                self._win_t, self._win_n = now, 0
            self._win_n += 1
            if self._win_n > self.max_rate:
                self.dropped += 1
                continue
            if len(data) > self.max_packet:
                self.malformed("oversize packet dropped")
                continue
            try:
                self.push(self._fn(data, self.cfg))
            except Exception as e:
                self.malformed(f"decode failed: {type(e).__name__}: {e}")

    def close(self):
        s, self._sock = getattr(self, "_sock", None), None
        if s is not None:
            s.close()

    def describe(self):
        return f"udp 127.0.0.1:{getattr(self, 'port', '?')} ({self.cfg.get('decoder', 'json')})"


class CallbackSource(IntegrationSource):
    """Runs a registered Python function fn(emit, stop_event) on its own daemon thread."""
    kind = "callback"

    def open(self):
        name = str(self.cfg.get("callback", ""))
        self._fn = CALLBACKS.get(name)
        if self._fn is None:
            raise ValueError(f"no callback registered as {name!r} (see register_callback / plugins)")

    def run(self):
        self._fn(lambda values, replace=False: self.push(values, replace), self._stop)

    def describe(self):
        return f"callback {self.cfg.get('callback', '')}"


SOURCE_KINDS = {c.kind: c for c in (HttpPushSource, HttpPollSource, LogTailSource, UdpSource, CallbackSource)}


# ---- configuration --------------------------------------------------------------------------
def example_config_text():
    """The placeholder-only example from the module docstring (everything disabled)."""
    m = re.search(r"integrations\.json example:\n(.*?)\n\s*--- end example ---", __doc__ or "", re.S)
    return m.group(1) if m else json.dumps({"version": 1, "plugins_enabled": False, "integrations": {}}, indent=2)


def clean_integ_cfg(raw):
    """Validate a raw config dict -> (clean config, warnings). Bad entries are dropped, never fatal."""
    warns = []
    raw = raw if isinstance(raw, dict) else {}
    stale = _clamp(raw.get("stale_after"), 1.0, 3600.0, INTEG_STALE_DEFAULT)
    cfg = {"plugins_enabled": raw.get("plugins_enabled") is True, "stale_after": stale,
           "integrations": {}, "afk_rules": [], "tags": [], "numeric": []}
    for name, sc in list(dict(raw.get("integrations") or {}).items())[:INTEG_MAX_SOURCES]:
        n = _ident(name)
        if not n or not isinstance(sc, dict):
            warns.append(f"integration {name!r}: skipped (bad name or entry)")
            continue
        if sc.get("kind") not in SOURCE_KINDS:
            warns.append(f"integration {n!r}: unknown kind {sc.get('kind')!r}")
            continue
        sc = dict(sc)
        sc["enabled"] = sc.get("enabled") is True
        sc["stale_after"] = _clamp(sc.get("stale_after"), 1.0, 3600.0, stale)
        cfg["integrations"][n] = sc
    for i, r in enumerate(list(raw.get("afk_rules") or [])[:32]):
        if not isinstance(r, dict) or r.get("enabled", True) is False:
            continue
        name = _ident(r.get("name") or f"rule{i + 1}")
        if not valid_cond(r.get("when")) or (r.get("unless") is not None and not valid_cond(r.get("unless"))):
            warns.append(f"afk rule {name!r}: invalid 'when'/'unless' condition - skipped")
            continue
        cfg["afk_rules"].append({"name": name, "when": r["when"], "unless": r.get("unless"),
                                 "on_unknown": "afk" if r.get("on_unknown") == "afk" else "ignore"})
    for i, t in enumerate(list(raw.get("tags") or [])[:8]):
        if not isinstance(t, dict) or not isinstance(t.get("ref"), str) or "." not in t["ref"]:
            warns.append(f"tag #{i + 1}: needs a 'ref' like 'integration.key' - skipped")
            continue
        d = {"name": _ident(t.get("name") or f"tag{i + 1}", 16), "ref": t["ref"]}
        if isinstance(t.get("map"), dict):
            d["map"] = {str(k): str(v) for k, v in list(t["map"].items())[:32]}
        if isinstance(t.get("bins"), list):
            try:
                d["bins"] = sorted(float(b) for b in t["bins"][:8])
            except (TypeError, ValueError):
                warns.append(f"tag {d['name']!r}: bad 'bins' ignored")
        for k in ("other", "default"):
            if isinstance(t.get(k), str):
                d[k] = t[k]
        cfg["tags"].append(d)
    for i, t in enumerate(list(raw.get("numeric") or [])[:NUM_MAX]):
        if isinstance(t, dict) and isinstance(t.get("ref"), str) and "." in t["ref"]:
            cfg["numeric"].append({"name": _ident(t.get("name") or f"num{i + 1}", 16), "ref": t["ref"]})
    return cfg, warns


def load_plugins(plugin_dir, allow_py):
    """Load declarative *.json mappings always, and *.py plugins only if allow_py. -> warnings."""
    warns = []
    if not os.path.isdir(plugin_dir):
        return warns
    for fn in sorted(os.listdir(plugin_dir))[:32]:
        path = os.path.join(plugin_dir, fn)
        try:
            if os.path.getsize(path) > 64 * 1024:
                warns.append(f"plugin {fn}: skipped (over 64 KB)")
            elif fn.endswith(".json"):
                with open(path, encoding="utf-8") as fh:
                    spec = json.load(fh)
                register_mapper(_ident(spec.get("name") or fn[:-5]), make_json_mapper(spec))
            elif fn.endswith(".py"):
                if not allow_py:
                    warns.append(f"plugin {fn}: skipped (set \"plugins_enabled\": true to allow .py plugins)")
                    continue
                ms = importlib.util.spec_from_file_location("pf_plugin_" + _ident(fn[:-3]), path)
                mod = importlib.util.module_from_spec(ms)
                ms.loader.exec_module(mod)
                if not callable(getattr(mod, "register", None)):
                    warns.append(f"plugin {fn}: no register(api) function")
                    continue
                mod.register(types.SimpleNamespace(register_decoder=register_decoder,
                                                   register_callback=register_callback,
                                                   register_mapper=register_mapper))
        except Exception as e:
            warns.append(f"plugin {fn}: {type(e).__name__}: {e}")
    return warns


class IntegrationManager:
    """Owns the shared GameState, the configured sources and the AFK-rule / tag definitions.

    Nothing starts by itself: a source only runs after start(name) (the user pressing Start)."""

    def __init__(self, path=INTEG_CONFIG_FILE, plugin_dir=INTEG_PLUGIN_DIR, clock=time.monotonic,
                 session_running=None):
        self.path, self.plugin_dir, self.clock = path, plugin_dir, clock
        self.session_running = session_running or (lambda: False)
        self.state = GameState(clock)
        self.sources = {}
        self.cfg, self.warnings = clean_integ_cfg({})
        self.load()

    def load(self):
        """(Re)read the config. Blocked while a Train/Detect session runs. -> (ok, message)"""
        if self.session_running():
            return False, "Reloading is blocked while a Train/Detect session is running."
        self.stop_all()
        raw, warns = {}, []
        try:
            if os.path.exists(self.path):
                if os.path.getsize(self.path) > INTEG_MAX_CFG_BYTES:
                    raise ValueError("config file is larger than 256 KB")
                with open(self.path, encoding="utf-8") as fh:
                    raw = json.load(fh)
        except Exception as e:
            warns.append(f"config unreadable: {type(e).__name__}: {e}")
        cfg, w2 = clean_integ_cfg(raw)
        warns += w2
        warns += load_plugins(self.plugin_dir, cfg["plugins_enabled"])
        self.cfg = cfg
        self.state.clear()
        self.state.default_stale = cfg["stale_after"]
        self.sources = {}
        for name, sc in cfg["integrations"].items():
            mapper = None
            if sc.get("mapper"):
                mapper = MAPPERS.get(str(sc["mapper"]))
                if mapper is None:
                    warns.append(f"integration {name!r}: unknown mapper {sc['mapper']!r}")
            self.sources[name] = SOURCE_KINDS[sc["kind"]](name, sc, self.state, self.clock, mapper)
        self.warnings = warns
        return True, f"{len(self.sources)} integration(s), {len(cfg['afk_rules'])} AFK rule(s), {len(cfg['tags'])} tag(s)"

    def add_source(self, src):
        """Programmatic hook (tests / embedding): register an already-built source object."""
        self.sources[src.name] = src
        src.cfg.setdefault("enabled", True)

    def start(self, name):
        src = self.sources.get(name)
        if src is None:
            return False
        if src.cfg.get("enabled") is not True:
            src.fail('disabled in config - set "enabled": true to allow it')
            return False
        return src.start()

    def stop(self, name):
        src = self.sources.get(name)
        if src is not None:
            src.stop()

    def stop_all(self):
        for src in list(self.sources.values()):
            src.stop()

    def any_active(self):
        return any(s.active() for s in self.sources.values())

    def values(self):
        return self.state.snapshot()

    def afk_check(self):
        """-> (applies, rule_name). Only evaluated while at least one integration is running."""
        if not self.cfg["afk_rules"] or not self.any_active():
            return False, None
        return afk_rule_check(self.cfg["afk_rules"], self.state.snapshot())

    def current_tag(self):
        if not self.cfg["tags"] or not self.any_active():
            return ""
        return compute_tag(self.cfg["tags"], self.state.snapshot())

    def current_nums(self):
        if not self.cfg["numeric"] or not self.any_active():
            return None
        return compute_nums(self.cfg["numeric"], self.state.snapshot())

    def rows(self):
        now, snap, out = self.clock(), self.state.snapshot(), []
        for n, s in self.sources.items():
            pre = n + "."
            out.append({"name": n, "kind": s.kind, "enabled": s.cfg.get("enabled") is True,
                        "status": s.status, "detail": s.describe(),
                        "age": None if s.last_update is None else now - s.last_update,
                        "count": s.count, "bad": s.bad, "error": s.last_error,
                        "error_age": None if s.last_error_t is None else now - s.last_error_t,
                        "values": {k[len(pre):]: v for k, v in snap.items() if k.startswith(pre)}})
        return out


def register(ctx):
    eng = ctx.engine
    mgr = ctx.provide("integrations", IntegrationManager(session_running=lambda: eng.mode is not None))
    ctx.provide("integration_api", {"register_decoder": register_decoder, "register_callback": register_callback,
                                    "register_mapper": register_mapper, "notice": INTEG_NOTICE, "INTEG_MAX_KEYS": INTEG_MAX_KEYS,
                                    "example_config_text": example_config_text})

    def sample(engine, now):
        applies, rule = mgr.afk_check()
        return {"afk": applies, "rule": rule, "tag": mgr.current_tag()}

    ctx.hooks.on("context.sample", sample)
    ctx.hooks.on("context.nums", lambda engine, now: mgr.current_nums())
    ctx.hooks.on("shutdown", mgr.stop_all)
