"""Entry point shared by fingerprinter.py and fingerprinterV0.4.py.

    python fingerprinter.py                       window if a UI module is loaded, otherwise help
    python fingerprinter.py --no-modules list     base only
    python fingerprinter.py --only ui_tk,ui_keys  just these modules (+ whatever they require)
    python fingerprinter.py --disable ui_server   everything except these
    python fingerprinter.py modules               show what loaded and what did not (and why)
"""
import argparse
import os
import sys

from . import cli, deps
from .engine import Engine
from .hooks import Hooks
from .plugins import Context, default_search_paths, load_modules


def _global_parser(add_help=False):
    """Options that decide which modules load - parsed first, before any module exists."""
    ap = argparse.ArgumentParser(add_help=add_help, allow_abbrev=False)
    ap.add_argument("--only", help="comma-separated modules to load (their requirements are added)")
    ap.add_argument("--disable", help="comma-separated modules NOT to load")
    ap.add_argument("--no-modules", action="store_true", help="base only: load no modules")
    ap.add_argument("--modules-dir", action="append", default=[], help="extra folder to load modules from")
    ap.add_argument("--frontend", help="which front-end to start (default: highest priority loaded)")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap


def parse_global(argv=None):
    """-> (namespace of the global options, leftover words). Unknown words are left for the second pass."""
    return _global_parser().parse_known_args(argv)


def build_parser(ctx=None):
    """The full parser. With a ctx, it also knows the commands the loaded modules added."""
    ap = argparse.ArgumentParser(prog="fingerprinter", description="Play Fingerprint", parents=[_global_parser()],
                                 epilog="Modules are loaded automatically from the modules/ folder.")
    sub = ap.add_subparsers(dest="command")
    sub.add_parser("gui", help="start the window (needs a UI module)")
    cli.add_subcommands(sub, ctx)
    return ap


def _csv(s):
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def build_context(args, app_dir):
    """Create hooks + engine + context and load the selected modules into it."""
    hooks = Hooks(debug=getattr(args, "verbose", False))
    ctx = Context(engine=Engine(hooks), hooks=hooks, args=args)
    if getattr(args, "no_modules", False):
        ctx.available = {}
    else:
        paths = default_search_paths(getattr(args, "modules_dir", []), app_dir)
        load_modules(ctx, paths, only=_csv(args.only) or None, disable=_csv(args.disable),
                     verbose=getattr(args, "verbose", False))
    hooks.emit("app.ready", ctx)
    return ctx


def main(argv=None, app_dir=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    app_dir = app_dir or os.path.dirname(os.path.abspath(sys.argv[0]))
    pre, rest = parse_global(argv)                       # pass 1: which modules?
    if not (rest and rest[0] == "modules"):
        problem = deps.check()
        if problem:
            deps.fail_with_dialog(problem)
    ctx = build_context(pre, app_dir)                    # load them
    args = build_parser(ctx).parse_args(argv)            # pass 2: now the modules' commands are known
    ctx.args = args
    try:
        if args.command in cli.COMMANDS or args.command in ctx.commands:
            return cli.run(ctx, args)
        frontends = sorted(ctx.frontends.items(), key=lambda kv: -kv[1][0])
        if args.frontend:
            frontends = [f for f in frontends if f[0] == args.frontend]
        if not frontends:
            if args.command == "gui" or args.frontend:
                print("No UI front-end is loaded" + (f" ({args.frontend})" if args.frontend else "") + ".")
                for n, why in sorted(ctx.skipped.items()):
                    print(f"  {n}: {why}")
                return 1
            print("No UI module is loaded - running headless. Commands: list, train NAME, detect [NAME ...], modules"
                  + "".join(f", {n}" for n in sorted(ctx.commands)) + ".")
            print("(Use --help for options.)")
            return 0
        name, (_prio, run, _desc) = frontends[0]
        return run(ctx) or 0
    finally:
        ctx.hooks.emit("app.exit")
