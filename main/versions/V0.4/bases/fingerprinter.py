#!/usr/bin/env python3
"""Play Fingerprint launcher.

    python fingerprinter.py                      start (window when a UI module is present)
    python fingerprinter.py --no-modules list    base only, no modules, no window
    python fingerprinter.py --only ui_tk,ui_keys window with just those tabs
    python fingerprinter.py modules              what is loaded / skipped and why

Everything optional lives in the modules/ folder next to this file; drop a module in and it is loaded
automatically. Full documentation: fp/__init__.py, fp/plugins.py and README.md.
"""
import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


def main(argv=None):
    from fp.app import main as run
    return run(argv, app_dir=APP_DIR)


if __name__ == "__main__":
    sys.exit(main())
