#!/usr/bin/env python3
"""Play Fingerprint SERVER base - the anti-cheat viewer. Connects to the fingerprint server (Render or any host).

    python fingerprinter_server.py                 the viewer window
    python fingerprinter_server.py analyze         headless: run the server's ban-evasion analysis
    python fingerprinter_server.py --help          all commands (status, players, show, bans, ban, unban, analyze, download)

Settings: the same server_config.json as the client ({"url": ..., "key": ...}); see fpserver/config.py.
No third-party packages needed.
"""
import argparse
import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


def main(argv=None):
    from fpserver import cli
    ap = argparse.ArgumentParser(description="Play Fingerprint server viewer")
    ap.add_argument("--config", help="path to server_config.json")
    ap.add_argument("--url", help="server URL (overrides the config)")
    ap.add_argument("--key", help="API key (overrides the config)")
    sub = ap.add_subparsers(dest="command")
    sub.add_parser("gui", help="the viewer window (default)")
    cli.add(sub)
    args = ap.parse_args(argv)
    if args.command in cli.COMMANDS:
        return cli.run(args)
    try:
        from fpserver.gui import run_gui
    except ImportError as e:
        print(f"No window available ({e}). Use a command instead:  {', '.join(cli.COMMANDS)}")
        return 1
    return run_gui(args) or 0


if __name__ == "__main__":
    sys.exit(main())
