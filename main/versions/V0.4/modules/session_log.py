"""Session log: records every Train / Detect session to history.json (no UI - ui_history shows it)."""
NAME = "session_log"
DESCRIPTION = "Records every Train / Detect session; adds the 'history' command"
REQUIRES = []
ORDER = 20

import json
import os
import time
from datetime import datetime

from fp.settings import APP_DATA_DIR

HISTORY_FILE = os.path.join(APP_DATA_DIR, "history.json")
MAX_ENTRIES = 500


class SessionLog:
    """Service \"session_log\": ctx.get(\"session_log\").entries() -> list of dicts, oldest first."""

    def __init__(self, path=HISTORY_FILE, limit=MAX_ENTRIES):
        self.path = path
        self.limit = limit
        self._open = None

    # ---- storage ---------------------------------------------------------------------------
    def entries(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _write(self, entries):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(entries[-self.limit:], fh, indent=1)
        os.replace(tmp, self.path)

    def clear(self):
        self._write([])

    # ---- recording -------------------------------------------------------------------------
    def start(self, mode, profiles=None):
        self._open = {"mode": mode, "t0": time.time(), "profiles": list(profiles or []),
                      "started": datetime.now().isoformat(timespec="seconds")}

    def stop(self, mode, result=None):
        """Close the running session and append it to the file. -> the new entry (or None)."""
        opened, self._open = self._open, None
        if not opened or opened["mode"] != mode:
            return None
        entry = {"mode": mode, "started": opened["started"], "seconds": round(time.time() - opened["t0"], 1),
                 "profiles": opened["profiles"], "new_windows": 0, "saved": None, "size": None, "error": None}
        if isinstance(result, dict):                       # Train hands over a result dict
            profile = result.get("profile")
            if profile is not None:
                entry["profiles"] = [getattr(profile, "name", str(profile))]
            entry["new_windows"] = int(result.get("new_windows") or 0)
            entry["saved"] = bool(result.get("saved"))
            entry["size"] = result.get("size")
            if result.get("error"):
                entry["error"] = str(result["error"])
        entries = self.entries()
        entries.append(entry)
        self._write(entries)
        return entry

    # ---- helpers shared with ui_history ------------------------------------------------------
    @staticmethod
    def fmt_duration(seconds):
        seconds = int(round(float(seconds or 0)))
        h, rest = divmod(seconds, 3600)
        m, s = divmod(rest, 60)
        if h:
            return f"{h}h {m:02d}m {s:02d}s"
        if m:
            return f"{m}m {s:02d}s"
        return f"{s}s"

    @staticmethod
    def totals(entries):
        """-> {mode: (session count, total seconds)}"""
        out = {}
        for e in entries:
            n, secs = out.get(e.get("mode", "?"), (0, 0.0))
            out[e.get("mode", "?")] = (n + 1, secs + float(e.get("seconds") or 0))
        return out


def _cmd_history(ctx, args):
    log = ctx.get("session_log")
    if args.clear:
        log.clear()
        print("History cleared.")
        return 0
    entries = log.entries()
    if not entries:
        print("No sessions recorded yet.")
        return 0
    for e in reversed(entries[-max(1, args.limit):]):
        who = ", ".join(e.get("profiles") or []) or "-"
        extra = f"  +{e.get('new_windows', 0)} windows" if e.get("mode") == "train" else ""
        print(f"{e.get('started', '?'):20s} {e.get('mode', '?'):7s} {log.fmt_duration(e.get('seconds')):>10s}  {who}{extra}")
    for mode, (n, secs) in sorted(log.totals(entries).items()):
        print(f"{mode}: {n} session(s), {log.fmt_duration(secs)} total")
    return 0


def register(ctx):
    log = ctx.provide("session_log", SessionLog())

    def on_start(mode):
        names = []
        if mode == "detect":
            names = [getattr(p, "name", str(p)) for p in (getattr(ctx.engine, "det_profiles", None) or [])]
        log.start(mode, names)

    def on_stop(mode, result=None):
        try:
            log.stop(mode, result)
        except OSError as e:
            print(f"session_log: could not write history: {e}")

    ctx.hooks.on("session.start", on_start, priority=10)     # run before UI subscribers
    ctx.hooks.on("session.stop", on_stop, priority=10)
    ctx.add_command("history", _cmd_history, help="show recorded Train / Detect sessions",
                    setup=lambda p: (p.add_argument("--limit", type=int, default=20),
                                     p.add_argument("--clear", action="store_true", help="delete the history")))
