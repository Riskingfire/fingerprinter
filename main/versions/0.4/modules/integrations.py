"""Simplified/advanced example Integrations module for the installer path
main/versions/0.4/modules/integrations.py

This file is an example that expands the integrations surface with ready-to-use
connection helpers and plugin registration examples. It is intentionally self
contained and safe: everything binds to loopback only and raw payloads are
kept in-memory.

NOTE: This is an example module (placed under main/versions/0.4/modules as
requested). The repository's canonical module lives under
main/versions/V0.4/modules/integrations.py; keep that file for the official
implementation. This file demonstrates how to "put something in your code" —
advanced decoders, a callback example, and helper classes you can copy into
your real integrations module as needed.
"""

from threading import Thread, Event
import socket
import http.server
import json
import time
import re
import struct
import os
from collections import deque

LOOPBACK = ("127.0.0.1", "localhost", "::1")

# --- simple in-memory game state (bounded, thread-safe-ish for example) ---
class SimpleGameState:
    def __init__(self, max_keys=128, stale_after=15.0):
        self._data = {}        # source -> {key: (value, ts)}
        self._max_keys = max_keys
        self._stale = stale_after

    def update(self, source, values):
        now = time.monotonic()
        cur = self._data.get(source, {})
        for k, v in values.items():
            if len(cur) >= self._max_keys and k not in cur:
                continue
            cur[k] = (v, now)
        self._data[source] = cur

    def snapshot(self):
        now = time.monotonic()
        out = {}
        for src, d in self._data.items():
            for k, (v, t) in d.items():
                if now - t <= self._stale:
                    out[f"{src}.{k}"] = v
        return out

    def clear(self, source=None):
        if source is None:
            self._data.clear()
        else:
            self._data.pop(source, None)


# --- decoders / callbacks registry (example) ---
DECODERS = {}
CALLBACKS = {}
MAPPERS = {}


def register_decoder(name, fn):
    DECODERS[str(name)] = fn


def register_callback(name, fn):
    CALLBACKS[str(name)] = fn


def register_mapper(name, fn):
    MAPPERS[str(name)] = fn


# built-in decoders (json, text_kv, struct)

def _decode_json(data, cfg):
    try:
        obj = json.loads(data.decode("utf-8"))
    except Exception:
        return {}
    out = {}
    def _flatten(o, prefix=""):
        if isinstance(o, dict):
            for kk, vv in o.items():
                _flatten(vv, prefix + ("." if prefix else "") + str(kk))
        elif isinstance(o, (list, tuple)):
            for i, vv in enumerate(o):
                _flatten(vv, prefix + ("." if prefix else "") + str(i))
        else:
            out[prefix] = vv if prefix else None
    # simpler: keep top-level scalars + simple nested
    def _collect(o, p=""):
        if isinstance(o, dict):
            for kk, vv in o.items():
                _collect(vv, p + ("." if p else "") + str(kk))
        elif isinstance(o, (list, tuple)):
            for i, vv in enumerate(o):
                _collect(vv, p + ("." if p else "") + str(i))
        else:
            out[p] = o
    _collect(obj)
    return out


def _decode_text_kv(data, cfg):
    s = data.decode("utf-8", "replace")
    out = {}
    for line in s.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()
            if not k:
                continue
            # coerce numbers/bools in a small way
            if re.fullmatch(r"-?\d+", v):
                out[k] = int(v)
            else:
                try:
                    fv = float(v)
                    out[k] = fv
                except Exception:
                    out[k] = v
    return out


def _decode_struct(data, cfg):
    # cfg should provide fields: [{"key": "x", "fmt": "<f", "offset": 0}, ...]
    out = {}
    for f in cfg.get("fields", []):
        try:
            key = f.get("key")
            fmt = f.get("fmt")
            off = int(f.get("offset", 0))
            val = struct.unpack_from(fmt, data, off)[0]
            out[key] = val
        except Exception:
            continue
    return out

# register built-ins
register_decoder("json", _decode_json)
register_decoder("text_kv", _decode_text_kv)
register_decoder("struct", _decode_struct)


# --- example sources ---
class HttpPushSource:
    """A tiny local HTTP endpoint accepting JSON POSTs on loopback.
    Requires a port and optional token in cfg: {'port': 3000, 'token': 'tok'}
    """
    def __init__(self, name, cfg, state: SimpleGameState):
        self.name = name
        self.cfg = cfg or {}
        self.state = state
        self._srv = None
        self._thread = None

    class _Handler(http.server.BaseHTTPRequestHandler):
        server_version = "SimpleInteg/0.1"
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            # token check left to outer server via attribute
            # decode JSON
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception:
                self.send_response(400)
                self.end_headers()
                return
            # simplistic: emit flattened scalars
            out = {}
            def collect(o, p=""):
                if isinstance(o, dict):
                    for kk, vv in o.items():
                        collect(vv, p + ("." if p else "") + str(kk))
                elif isinstance(o, (list, tuple)):
                    for i, vv in enumerate(o):
                        collect(vv, p + ("." if p else "") + str(i))
                else:
                    out[p] = vv
            collect(data)
            # attach to server's source_name
            src = getattr(self.server, "source_name", "push")
            state = getattr(self.server, "stateobj", None)
            if state is not None:
                state.update(src, out)
            self.send_response(200)
            self.end_headers()

        def log_message(self, format, *args):
            # silence
            return

    def open(self):
        port = int(self.cfg.get("port", 3000))
        host = self.cfg.get("host", "127.0.0.1")
        if host not in LOOPBACK:
            raise ValueError("only loopback hosts allowed")
        self._srv = http.server.HTTPServer((host, port), self._Handler)
        # attach helpers
        self._srv.source_name = self.name
        self._srv.stateobj = self.state
        self._thread = Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()

    def close(self):
        if self._srv:
            try:
                self._srv.shutdown()
            except Exception:
                pass
            self._srv = None

    def start(self):
        self.open()

    def stop(self):
        self.close()


class UdpSource:
    """Simple UDP listener that uses registered decoders.
    cfg: {'port': 40000, 'decoder': 'json'}
    """
    def __init__(self, name, cfg, state: SimpleGameState):
        self.name = name
        self.cfg = cfg or {}
        self.state = state
        self._sock = None
        self._thread = None
        self._stop = Event()

    def _loop(self):
        decoder = DECODERS.get(self.cfg.get("decoder", "json"))
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(65535)
                # only accept loopback
                if addr[0] not in LOOPBACK:
                    continue
                if decoder:
                    vals = decoder(data, self.cfg) or {}
                    self.state.update(self.name, vals)
            except Exception:
                time.sleep(0.1)

    def start(self):
        port = int(self.cfg.get("port", 40000))
        host = self.cfg.get("host", "127.0.0.1")
        if host not in LOOPBACK:
            raise ValueError("only loopback hosts allowed")
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((host, port))
        self._thread = Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass


class CallbackSource:
    """Run a registered callback function that emits values via emit(dict).

    The callback is resolved from CALLBACKS by name in cfg: {'callback': 'my_cb'}.
    The callback signature is fn(emit, stop_event) and should return when stop_event is set.
    """
    def __init__(self, name, cfg, state: SimpleGameState):
        self.name = name
        self.cfg = cfg or {}
        self.state = state
        self._stop = Event()
        self._thread = None

    def _emit(self, vals):
        if not isinstance(vals, dict):
            return
        self.state.update(self.name, vals)

    def _run(self):
        cbname = self.cfg.get("callback")
        cb = CALLBACKS.get(cbname)
        if not cb:
            return
        try:
            cb(self._emit, self._stop)
        except Exception:
            return

    def start(self):
        self._stop.clear()
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        t = self._thread
        if t:
            t.join(1.0)


# --- helper manager to demonstrate wiring up sources ---
class IntegrationManager:
    def __init__(self, cfg, state=None):
        self.cfg = cfg or {}
        self.state = state or SimpleGameState()
        self._sources = {}

    def start_all(self):
        for name, spec in (self.cfg.get("integrations", {}) or {}).items():
            if not spec.get("enabled"):
                continue
            try:
                self.start_source(name, spec)
            except Exception:
                pass

    def start_source(self, name, spec):
        kind = spec.get("kind")
        if kind == "http_push":
            src = HttpPushSource(name, spec, self.state)
            src.start()
            self._sources[name] = src
        elif kind == "udp":
            src = UdpSource(name, spec, self.state)
            src.start()
            self._sources[name] = src
        elif kind == "callback":
            src = CallbackSource(name, spec, self.state)
            src.start()
            self._sources[name] = src
        else:
            raise ValueError("unsupported kind")

    def stop_all(self):
        for s in list(self._sources.values()):
            try:
                s.stop()
            except Exception:
                pass
        self._sources.clear()

    def snapshot(self):
        return self.state.snapshot()


# --- example callback you can register in your "game code" ---

def example_game_counter(emit, stop_event):
    """An example callback simulating a game's periodic telemetry values."""
    i = 0
    while not stop_event.is_set() and i < 1000:
        emit({"ticks": i, "player.connected": True if i % 5 != 0 else False})
        time.sleep(0.5)
        i += 1

# register the example callback so games can reference it by name in cfg
register_callback("example_counter", example_game_counter)


# --- example usage helper (writes example config and shows how to start) ---

def example_config():
    return {
        "version": 1,
        "integrations": {
            "game_push": {"kind": "http_push", "enabled": False, "port": 30123},
            "game_udp": {"kind": "udp", "enabled": False, "port": 40123, "decoder": "json"},
            "game_cb": {"kind": "callback", "enabled": False, "callback": "example_counter"}
        }
    }


if __name__ == "__main__":
    # quick demo when run standalone
    cfg = example_config()
    cfg["integrations"]["game_cb"]["enabled"] = True
    mgr = IntegrationManager(cfg)
    mgr.start_all()
    print("started demo callback; snapshot in 2s ->")
    time.sleep(2.5)
    print(mgr.snapshot())
    mgr.stop_all()
