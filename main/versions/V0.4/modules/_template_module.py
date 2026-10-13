"""Template for a new module. Copy this file, rename it (no leading underscore) and it loads automatically.

Files starting with "_" are ignored by the loader, so this one never runs as shipped.
"""
NAME = "my_module"                      # optional; defaults to the file name
TITLE = "My module"                  # optional: name in the installer's component list (python tools/make_manifest.py)
DESCRIPTION = "What this module adds"   # shown by:  python fingerprinter.py modules
REQUIRES = []                           # hard dependencies, e.g. ["ui_tk"]; skipped if one is missing
OPTIONAL = []                           # soft: loaded before this one if present
ORDER = 100                             # load order tie-break (lower first)


def register(ctx):
    """Called once at start-up.

    ctx.hooks   events:  train.window(info)  detect.update(info)  profile.saved(profile, size)
                         session.start(mode)  session.stop(mode, result)  engine.tick(now)  shutdown()
                         banlist.changed(added=, removed=)  app.ready(ctx)  app.started()
                UI hooks (only fire if ui_tk is loaded): ui.built(app)  ui.tick(app)  ui.profiles_refreshed(app)
    ctx.engine  the headless Engine (settings: strict, afk_secs, ...; state: mode, profile, ema, ...)
    ctx.provide("name", obj) / ctx.get("name")   share services with other modules
    ctx.add_frontend("name", run_fn, priority)   offer another way to run the app
    Answer these to add game context (return a dict, or None to stay out of it):
        ctx.hooks.on("context.sample", lambda engine, now: {"afk": False, "rule": None, "tag": "mode=a"})
        ctx.hooks.on("context.nums",   lambda engine, now: {"hp": 80})
    """
    ctx.hooks.on("train.window", lambda info: print("window:", info.get("state")))

    # A command-line command (python fingerprinter.py hello --who you) - no base file needs to know about it:
    #   ctx.add_command("hello", lambda ctx, args: print("hi", args.who) or 0, help="say hi",
    #                   setup=lambda p: p.add_argument("--who", default="world"))

    # A tab (only if the window module is present):
    #   ui = ctx.get("ui")
    #   if ui: ui.add_mixin(MyTab); ui.add_tab("  Mine  ", "mine_tab", "_build_mine", order=35)
