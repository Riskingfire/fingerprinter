"""Headless viewer commands.

    python fingerprinter_server.py status
    python fingerprinter_server.py players
    python fingerprinter_server.py show NAME                 header of one fingerprint
    python fingerprinter_server.py bans
    python fingerprinter_server.py ban NAME --reason "..."   /   unban NAME
    python fingerprinter_server.py analyze [--level review|high|all] [--json]
    python fingerprinter_server.py download NAME [-o FILE]
    python fingerprinter_server.py gui                       (default: the window)
"""
import csv
import json
import sys

from . import config
from .api import ApiError, ServerApi, filter_results, summarize

COMMANDS = ("status", "players", "show", "bans", "ban", "unban", "analyze", "download")


def add(sub):
    sub.add_parser("status", help="connection test and counts")
    sub.add_parser("players", help="fingerprints on the server")
    p = sub.add_parser("show", help="header of one fingerprint")
    p.add_argument("name")
    sub.add_parser("bans", help="the ban list")
    p = sub.add_parser("ban", help="add a ban")
    p.add_argument("name")
    p.add_argument("--reason", default="")
    p = sub.add_parser("unban", help="remove a ban")
    p.add_argument("name")
    p = sub.add_parser("analyze", help="run the server's ban-evasion analysis")
    p.add_argument("--level", choices=("review", "high", "all"), default="review")
    p.add_argument("--json", action="store_true", help="print JSON instead of a table")
    p.add_argument("--csv", metavar="FILE", help="also write the shown rows to a CSV file")
    p = sub.add_parser("download", help="save a fingerprint file")
    p.add_argument("name")
    p.add_argument("-o", "--output")


def run(args):
    cfg = config.load(args.config)
    if args.url:
        cfg["url"] = args.url
    if args.key:
        cfg["key"] = args.key
    api = ServerApi(cfg)
    try:
        if not api.wake(progress=lambda m: print(m, file=sys.stderr)):
            print("Could not reach the server. Check the URL, or try again in a minute.")
            return 1
        return _dispatch(api, args)
    except ApiError as e:
        print(f"Server said: {e}")
        return 1
    except ValueError as e:
        print(e)
        return 2


def _dispatch(api, a):
    c = a.command
    if c == "status":
        s = api.status()
        print(f"Connected. {s.get('fingerprints')} fingerprint(s), {s.get('bans')} ban(s).")
    elif c == "players":
        bans = api.banlist()
        for r in sorted(api.fingerprints(), key=lambda r: str(r.get("name"))):
            print(f"{str(r.get('name')):28s} {str(r.get('updated', '')):20s}{'  BANNED' if r.get('name') in bans else ''}")
    elif c == "show":
        for k, v in summarize(api.fingerprint_raw(a.name)).items():
            print(f"{k:12s} {v}")
    elif c == "bans":
        for n, e in sorted(api.banlist().items()):
            print(f"{n:28s} {e.get('date', ''):12s} {e.get('reason', '')}")
    elif c == "ban":
        api.ban(a.name, a.reason)
        print(f"Banned '{a.name}'.")
    elif c == "unban":
        api.unban(a.name)
        print(f"Unbanned '{a.name}'.")
    elif c == "analyze":
        rows = filter_results(api.analysis(), a.level)
        if a.csv:
            with open(a.csv, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["flag", "candidate", "banned", "distance", "shared_features"])
                w.writerows([[r.get("flag"), r.get("candidate"), r.get("banned"), r.get("distance"), r.get("shared_features")] for r in rows])
        if a.json:
            print(json.dumps(rows, indent=1))
        else:
            for r in rows:
                print(f"{r.get('flag'):7s} {str(r.get('candidate')):24s} vs banned {str(r.get('banned')):24s} "
                      f"distance {r.get('distance')}  ({r.get('shared_features')} shared features)")
            print(f"{len(rows)} row(s). Flags are leads for HUMAN REVIEW - never auto-ban on a fingerprint match alone.")
    elif c == "download":
        out = a.output or f"{a.name}.fpr"
        with open(out, "wb") as fh:
            fh.write(api.fingerprint_raw(a.name))
        print(f"Saved {out}")
    return 0
