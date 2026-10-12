#!/usr/bin/env python3
"""Play Fingerprint V0.4 - same program as fingerprinter.py, kept as a versioned launcher.

    python fingerprinterV0.4.py            -> same window (every module found in modules/ is loaded)

Everything is discovered from the modules/ folder: nothing in this file (or any base file) lists module names.
To use names from the program in your own scripts, use fingerprinter.py / the fp package directly:
    from fp import Profile, extract_features ...        (base)
    ctx.get("integrations"), ctx.get("server")           (services that modules provide)
"""
import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

if __name__ == "__main__":
    from fingerprinter import main
    sys.exit(main())
