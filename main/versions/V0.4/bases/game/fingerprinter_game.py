#!/usr/bin/env python3
"""Play Fingerprint GAME base - runs next to (or inside) your game client. No window, no UI.

    python fingerprinter_game.py --player ID --title "My Game" --process mygame.exe
    python fingerprinter_game.py --config game_config.json
    python fingerprinter_game.py --probe              print the window in front, to find the right title / process

It fingerprints the player only while the game window has focus (hooks are removed otherwise), saves the
.fpr locally and uploads it to the server. Settings: game_config.json (see game_config.example.json);
command-line options override it. Needs:  pip install pynput numpy
"""
import argparse
import json
import os
import sys
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Play Fingerprint game base (headless)")
    ap.add_argument("--config", default=os.path.join(APP_DIR, "game_config.json"))
    ap.add_argument("--player", help="player / account id (the fingerprint's name)")
    ap.add_argument("--title", action="append", default=[], help="part of the game window title (repeatable)")
    ap.add_argument("--process", action="append", default=[], help="game process name, e.g. mygame.exe (repeatable)")
    ap.add_argument("--pid", action="append", type=int, default=[], help="game process id (repeatable)")
    ap.add_argument("--no-keys", action="store_true", help="do not store per-key statistics")
    ap.add_argument("--data-dir", help="where fingerprints are kept")
    ap.add_argument("--minutes", type=float, default=0.0, help="stop automatically after N minutes")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--probe", action="store_true", help="show the foreground window (title, process, pid) and exit with Ctrl+C")
    a = ap.parse_args(argv)

    if a.probe:
        from fpgame.focus import foreground
        print("Bring your game to the front; Ctrl+C to stop.")
        try:
            while True:
                try:
                    print(foreground())
                except Exception as e:
                    print(f"cannot read the foreground window: {type(e).__name__}: {e}")
                time.sleep(1)
        except KeyboardInterrupt:
            return 0

    from fpgame.deps import check
    problem = check()
    if problem:
        print(problem)
        return 1
    from fpgame import FocusGate, GameFingerprinter, WindowMatch

    cfg = {}
    if os.path.exists(a.config):
        with open(a.config, encoding="utf-8") as fh:
            cfg = json.load(fh)
    player = a.player or cfg.get("player")
    titles = a.title or cfg.get("window_titles", [])
    procs = a.process or cfg.get("processes", [])
    pids = a.pid or cfg.get("pids", [])
    if not player:
        print("No player id: use --player or set \"player\" in game_config.json")
        return 2
    if not (titles or procs or pids):
        print("Say which window is the game: --title / --process / --pid (or window_titles / processes / pids in the config).\n"
              "Use --probe to see what the window in front is called.")
        return 2
    log = (lambda m: None) if a.quiet else (lambda m: print(time.strftime("[%H:%M:%S]"), m, flush=True))
    kwargs = {}
    if a.data_dir or cfg.get("data_dir"):
        kwargs["data_dir"] = a.data_dir or cfg["data_dir"]
    fp = GameFingerprinter(player, focus=FocusGate(WindowMatch(titles, procs, pids)), server=cfg.get("server"),
                           track_keys=not a.no_keys and cfg.get("track_keys", True),
                           autosave_secs=cfg.get("autosave_secs", 300), upload_secs=cfg.get("upload_secs", 900),
                           log=log, **kwargs)
    fp.start()
    end = time.time() + a.minutes * 60 if a.minutes else None
    try:
        warned = None
        while not (end and time.time() >= end):
            time.sleep(0.5)
            if fp.gate.error and fp.gate.error != warned:       # focus detection is failing: nothing is recorded
                warned = fp.gate.error
                print(f"WARNING: cannot tell which window is in front ({warned}); recording nothing until it works. "
                      "On Linux install xdotool (X11 only).")
    except KeyboardInterrupt:
        print()
    fp.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
