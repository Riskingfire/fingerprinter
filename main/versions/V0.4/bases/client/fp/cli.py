"""Headless command line - the base can run with no modules at all.

    python fingerprinter.py list
    python fingerprinter.py train NAME [--minutes 15]     F8 (or Ctrl+C) stops and saves
    python fingerprinter.py detect [NAME ...]             F8 (or Ctrl+C) stops; no names = all fingerprints
    python fingerprinter.py modules                       what is loaded / skipped and why

Modules still take part when loaded (e.g. the integrations module supplies game context and AFK rules).
"""
import os
import time

from .profile import Profile, stability_text
from .settings import HOTKEY, WINDOW_SEC
from .store import list_profiles, load_banlist, profile_path, read_summary, sanitize

COMMANDS = ("list", "train", "detect", "modules")


def add_subcommands(sub, ctx=None):
    sub.add_parser("list", help="list fingerprints")
    t = sub.add_parser("train", help="build or refine a fingerprint (headless)")
    t.add_argument("name")
    t.add_argument("--minutes", type=float, default=0.0, help="stop automatically after N minutes")
    t.add_argument("--no-keys", action="store_true", help="do not store per-key statistics")
    d = sub.add_parser("detect", help="live-score input against fingerprints (headless)")
    d.add_argument("names", nargs="*")
    d.add_argument("--strict", type=float, default=1.8, help="1.2 strict .. 3.0 lenient (default 1.8)")
    d.add_argument("--minutes", type=float, default=0.0, help="stop automatically after N minutes")
    sub.add_parser("modules", help="show modules")
    for name, c in sorted((ctx.commands if ctx else {}).items()):      # commands added by modules
        if name in COMMANDS or name == "gui":
            print(f"module command '{name}' ignored: that name is built in")
            continue
        p = sub.add_parser(name, help=c["help"] or None)
        if c["setup"]:
            c["setup"](p)


def run(ctx, args):
    cmd = args.command
    if cmd == "list":
        return cmd_list()
    if cmd == "modules":
        return cmd_modules(ctx)
    if cmd == "train":
        return cmd_train(ctx, args)
    if cmd == "detect":
        return cmd_detect(ctx, args)
    if cmd in ctx.commands:
        return ctx.commands[cmd]["run"](ctx, args) or 0
    return 2


def cmd_list():
    names = list_profiles()
    ban = load_banlist()
    if not names:
        print("No fingerprints yet. Train one with:  train NAME")
        return 0
    for n in names:
        code, _emb = read_summary(profile_path(n))
        print(f"{n:24s} {code:42s}{'  BANNED' if n in ban else ''}")
    return 0


def cmd_modules(ctx):
    print("Loaded:")
    for n in ctx.loaded:
        print(f"  {n:18s} {ctx.available[n].description if n in ctx.available else ''}")
    if not ctx.loaded:
        print("  (none - running base only)")
    if ctx.skipped:
        print("Not loaded:")
        for n, why in sorted(ctx.skipped.items()):
            print(f"  {n:18s} {why}")
    off = sorted(set(ctx.available) - set(ctx.loaded) - set(ctx.skipped))
    for n in off:
        print(f"  {n:18s} (not selected)")
    return 0


def _loop(eng, minutes):
    end = time.perf_counter() + minutes * 60 if minutes else None
    try:
        while eng.mode:
            eng.tick()
            if eng.poll_hotkey():
                break
            if end and time.perf_counter() >= end:
                break
            time.sleep(0.25)
    except KeyboardInterrupt:
        print()


def cmd_train(ctx, args):
    eng = ctx.engine
    name = sanitize(args.name)
    if not name:
        print("Give the fingerprint a name.")
        return 2
    path = profile_path(name)
    try:
        profile = Profile.load(path) if os.path.exists(path) else Profile(name)
    except Exception as e:
        print(f"Could not read '{name}': {e}")
        return 1
    eng.track_keys = not args.no_keys

    def on_window(info):
        if info["state"] == "window":
            print(f"  window {eng.new_windows:3d}  {info['class_text']}  ADS: {info['ads_short']}"
                  + (f"  tag={info['tag']}" if info.get("tag") else ""))
        elif info["state"] == "afk_skipped":
            print("  (AFK - window skipped)")
        else:
            print("  (low activity - window skipped)")

    ctx.hooks.on("train.window", on_window)
    print(f"Training '{name}' - play normally. {HOTKEY.upper()} or Ctrl+C stops and saves. "
          f"First windows appear after ~{20 + int(WINDOW_SEC)} s.")
    eng.start_train(profile)
    _loop(eng, args.minutes)
    res = eng.stop_train()
    if res["error"]:
        print(f"Save failed: {res['error']}")
        return 1
    if not res["saved"]:
        print("Nothing saved - no full windows were captured (train for ~30 s of real play at least).")
        return 0
    p = res["profile"]
    c = p.classify()
    print(f"Saved '{p.name}': {len(p.windows)} windows, {res['size'] / 1024:.0f} KB -> {profile_path(p.name)}")
    print(f"  Class: {c['code']}   {stability_text(p)}")
    return 0


def cmd_detect(ctx, args):
    eng = ctx.engine
    names = [sanitize(n) for n in args.names] or list_profiles()
    if not names:
        print("No fingerprints to compare against. Train one first.")
        return 1
    profiles, problems = eng.load_detect_profiles(names)
    for _kind, _n, msg in problems:
        print(msg)
    if not profiles:
        return 1
    eng.strict = float(args.strict)

    def on_update(info):
        t = info.get("time") or time.strftime("%H:%M:%S")
        if info["state"] == "paused":
            print(f"[{t}] paused - {info.get('why')}")
        elif info["state"] == "waiting":
            print(f"[{t}] not enough activity yet...")
        elif info["state"] == "scored":
            sims = "  ".join(f"{n}={s:.0f}%" for n, s in sorted(info["similarity"].items(), key=lambda kv: -kv[1]))
            label = {"banned": f"[FLAG] matches banned account '{info['best']}'",
                     "match": f"playing: {info['best']}", "unknown": "unknown player"}[info["verdict"]]
            print(f"[{t}] {label:45s} {sims}")
            for n in info.get("notes", []):
                print(f"         ! {n}")
        elif info["state"] == "partial" and info.get("live_class"):
            pass

    ctx.hooks.on("detect.update", on_update)
    print(f"Detecting against {', '.join(p.name for p in profiles)}. {HOTKEY.upper()} or Ctrl+C stops.")
    eng.start_detect(profiles)
    _loop(eng, args.minutes)
    eng.stop_detect()
    return 0

