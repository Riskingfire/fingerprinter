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

# Settings: try client (fp) then game (fpgame) then sensible defaults
try:
    from fp.settings import APP_DATA_DIR, TAG_MAX_LEN, NUM_MAX
except Exception:
    try:
        from fpgame.settings import APP_DATA_DIR as APP_DATA_DIR, TAG_MAX_LEN as TAG_MAX_LEN, NUM_MAX as NUM_MAX
    except Exception:
        APP_DATA_DIR = os.path.join(os.path.expanduser("~"), "AppData", "fingerprint")
        TAG_MAX_LEN = 64
        NUM_MAX = 8

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

...
