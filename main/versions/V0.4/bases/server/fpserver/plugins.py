"""Module discovery and loading for the SERVER base.

This mirrors the client-side module loader so modules placed in the usual
`modules/` folders are discovered and can register services / commands for the
server viewer.
"""
import ast
import importlib.util
import json
import os
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DATA_DIR = os.path.join(os.path.expanduser("~"), "AppData", "fingerprint_server")
MODULES_CONFIG_FILE = os.path.join(APP_DATA_DIR, "modules.json")
USER_MODULES_DIR = os.path.join(APP_DATA_DIR, "modules")


class ModuleUnavailable(Exception):
    """Raised by a module to skip itself with a useful reason."""


class Context:
    def __init__(self, engine=None, hooks=None, args=None):
        self.hooks = hooks
        self.engine = engine
        self.args = args
        self.services = {}
        self.loaded = {}
        self.skipped = {}
        self.available = {}
        self.frontends = {}
        self.commands = {}

    def provide(self, name, obj):
        self.services[name] = obj
        return obj

    def get(self, name, default=None):
        return self.services.get(name, default)

    def has(self, name):
        return name in self.services

    def add_frontend(self, name, run, priority=0, description=""):
        self.frontends[name] = (priority, run, description)

    def add_command(self, name, run, help="", setup=None):
        self.commands[name] = {"run": run, "help": help, "setup": setup}

    def has_module(self, name):
        return name in self.loaded


class ModuleInfo:
    def __init__(self, name, path, is_pkg):
        self.name, self.path, self.is_pkg = name, path, is_pkg
        self.description, self.requires, self.optional, self.order = "", [], [], 100
        self.error = None

    def __repr__(self):
        return f"<module {self.name} {self.path}>"


def default_search_paths(extra=(), app_dir=None):
    paths = [p for p in extra if p]
    env = os.environ.get("FP_MODULES_PATH", "")
    paths += [p for p in env.split(os.pathsep) if p]
    if app_dir:
        paths.append(os.path.join(app_dir, "modules"))
        paths.append(os.path.join(app_dir, "..", "..", "modules"))
    paths.append(USER_MODULES_DIR)
    seen, out = set(), []
    for p in paths:
        p = os.path.abspath(os.path.expanduser(p))
        if p not in seen and os.path.isdir(p):
            seen.add(p)
            out.append(p)
    return out


def _literal_meta(path):
    meta = {}
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), path)
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            key = node.targets[0].id
            if key in ("NAME", "DESCRIPTION", "REQUIRES", "OPTIONAL", "ORDER"):
                try:
                    meta[key] = ast.literal_eval(node.value)
                except Exception:
                    pass
    return meta


def discover(paths):
    found = {}
    for base in paths:
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            continue
        for entry in entries:
            if entry.startswith(("_", ".")):
                continue
            full = os.path.join(base, entry)
            if os.path.isfile(full) and entry.endswith(".py"):
                stem, src, is_pkg = entry[:-3], full, False
            elif os.path.isdir(full) and os.path.isfile(os.path.join(full, "__init__.py")):
                stem, src, is_pkg = entry, os.path.join(full, "__init__.py"), True
            else:
                continue
            meta = _literal_meta(src)
            name = str(meta.get("NAME", stem))
            if name in found:
                continue
            info = ModuleInfo(name, full, is_pkg)
            info.description = str(meta.get("DESCRIPTION", ""))
            info.requires = [str(x) for x in meta.get("REQUIRES", [])]
            info.optional = [str(x) for x in meta.get("OPTIONAL", [])]
            try:
                info.order = int(meta.get("ORDER", 100))
            except (TypeError, ValueError):
                pass
            info.error = meta.get("_error")
            found[name] = info
    return found


def load_modules_config():
    try:
        with open(MODULES_CONFIG_FILE, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _import(info):
    modname = f"fpserver_modules.{info.name}"
    if info.is_pkg:
        spec = importlib.util.spec_from_file_location(
            modname, os.path.join(info.path, "__init__.py"), submodule_search_locations=[info.path]
        )
    else:
        spec = importlib.util.spec_from_file_location(modname, info.path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(modname, None)
        raise
    return mod


def _closure(names, available):
    out, todo = set(), list(names)
    while todo:
        n = todo.pop()
        if n in out:
            continue
        out.add(n)
        if n in available:
            todo.extend(available[n].requires)
    return out


def select(available, only=None, disable=(), config=None):
    config = config or {}
    only = list(only) if only is not None else config.get("only")
    off = set(disable) | set(config.get("disabled", []) or [])
    skipped = {}
    names = set(available) if not only else _closure(only, available)
    for n in sorted(names - set(available)):
        skipped[n] = "not found in any modules folder"
    names &= set(available)
    for n in sorted(names & off):
        skipped[n] = "disabled"
    return names - off, skipped


def load_modules(ctx, paths, only=None, disable=(), config=None, verbose=False):
    """Discover, order and register modules into `ctx`. Never raises because of a module."""
    ctx.available = discover(paths)
    names, skipped = select(ctx.available, only, disable, config if config is not None else load_modules_config())
    ctx.skipped.update(skipped)
    for n in sorted(names):
        if ctx.available[n].error:
            ctx.skipped[n] = f"unreadable: {ctx.available[n].error}"
    names = {n for n in names if n not in ctx.skipped}

    changed = True
    while changed:
        changed = False
        for n in sorted(names):
            missing = [r for r in ctx.available[n].requires if r not in names]
            if missing:
                ctx.skipped[n] = "needs " + ", ".join(missing)
                names.discard(n)
                changed = True

    ordered, state = [], {}

    def visit(n):
        if state.get(n) == 2:
            return
        if state.get(n) == 1:
            return
        state[n] = 1
        info = ctx.available[n]
        for d in sorted(info.requires + [o for o in info.optional if o in names], key=lambda x: (ctx.available[x].order if x in ctx.available else 0, x)):
            if d in names:
                visit(d)
        state[n] = 2
        ordered.append(n)

    for n in sorted(names, key=lambda x: (ctx.available[x].order, x)):
        visit(n)

    for n in ordered:
        info = ctx.available[n]
        missing = [r for r in info.requires if r not in ctx.loaded]
        if missing:
            ctx.skipped[n] = "needs " + ", ".join(missing)
            continue
        try:
            mod = _import(info)
        except ModuleUnavailable as e:
            ctx.skipped[n] = str(e) or "unavailable"
            continue
        except Exception as e:
            ctx.skipped[n] = f"import failed: {type(e).__name__}: {e}"
            continue
        reg = getattr(mod, "register", None)
        if not callable(reg):
            ctx.skipped[n] = "no register(ctx) function"
            continue
        try:
            reg(ctx)
        except ModuleUnavailable as e:
            ctx.skipped[n] = str(e) or "unavailable"
            continue
        except Exception as e:
            ctx.skipped[n] = f"register failed: {type(e).__name__}: {e}"
            continue
        ctx.loaded[n] = mod
        if verbose:
            print(f"  module loaded: {n}")
    return ctx
