#!/usr/bin/env python3
"""Keep modules/modules.json (the installer's component list) in step with the modules - without wiping your choices.

    python tools/make_manifest.py            write the manifest
    python tools/make_manifest.py --check    only report problems (exit 1 if the manifest is out of date)

For every modules/*.py (not starting with "_") it reads, WITHOUT importing it: NAME, TITLE, DESCRIPTION, REQUIRES,
ORDER, INSTALL_DEFAULT.  Then:
  * "modules" is rebuilt from the files - so a module's REQUIRES can never disagree with what the installer installs;
  * your existing "features" (the check boxes, their names, grouping and defaults) are KEPT exactly as they are;
  * a module that no feature includes gets a check box of its own (unticked unless it sets INSTALL_DEFAULT = True);
  * the "base_only" box (for the game and server bases, which have no modules) is added if missing;
  * features that mention a module that no longer exists are reported, never silently dropped.
With no existing manifest it creates one check box per module.
"""
import ast
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODULES = os.path.join(os.path.dirname(HERE), "modules")
MANIFEST = os.path.join(MODULES, "modules.json")
BASE_ONLY = {"id": "base_only", "name": "No optional modules (game and server bases)",
             "description": "Install just the chosen base. Use this for the game and server bases, which do not use modules.",
             "default": False, "includes": []}


def meta(path):
    out = {}
    for node in ast.parse(open(path, encoding="utf-8").read()).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except Exception:
                pass
    return out


def build(old=None):
    """-> (new manifest dict, list of notes). `old` is the existing manifest (or None)."""
    notes, found = [], []
    for fn in sorted(os.listdir(MODULES)):
        if fn.endswith(".py") and not fn.startswith("_"):
            m = meta(os.path.join(MODULES, fn))
            found.append({"id": m.get("NAME") or fn[:-3], "file": fn, "m": m})
    found.sort(key=lambda d: (d["m"].get("ORDER", 50), d["id"]))
    ids = {d["id"] for d in found}
    old = old or {}
    old_mods = {m.get("id"): m for m in old.get("modules", []) if isinstance(m, dict)}

    mods = []
    for d in found:
        req = list(d["m"].get("REQUIRES", []))
        for r in req:
            if r not in ids:
                raise SystemExit(f"{d['id']} requires '{r}', which is not in modules/")
        entry = dict(old_mods.get(d["id"], {}))                # keep any extra keys you added (name, type, ...)
        entry.setdefault("id", d["id"])
        entry.setdefault("name", d["m"].get("TITLE") or d["id"].replace("_", " ").title())
        entry["file"] = d["file"]
        entry.setdefault("type", "python")
        was = old_mods.get(d["id"], {}).get("requires", [])
        if d["id"] in old_mods and set(was) != set(req):
            notes.append(f"requires of '{d['id']}' changed: {was} -> {req}")
        if set(was) == set(req) and was:
            req = list(was)                                  # same set: keep your ordering, no noise in the diff
        if req:
            entry["requires"] = req
        else:
            entry.pop("requires", None)
        mods.append(entry)
    order = {mid: i for i, mid in enumerate(old_mods)}            # keep the manifest's existing order; new modules go last
    mods.sort(key=lambda e: order.get(e["id"], len(order)))
    for gone in sorted(set(old_mods) - ids):
        notes.append(f"module '{gone}' is in the old manifest but its file is gone - removed from 'modules'")

    features = [dict(f) for f in old.get("features", []) if isinstance(f, dict)]
    covered = set()
    for f in features:
        inc = f.get("includes", [])
        inc = [inc] if isinstance(inc, str) else list(inc)
        for i in inc:
            if i not in ids:
                notes.append(f"feature '{f.get('id')}' includes '{i}', which has no file in modules/ - the installer would fail on it")
        covered |= set(inc)
    for d in found:
        if d["id"] not in covered:
            m = d["m"]
            features.append({"id": d["id"], "name": m.get("TITLE") or d["id"].replace("_", " ").title(),
                             "description": m.get("DESCRIPTION", ""),
                             "default": bool(m.get("INSTALL_DEFAULT", not old)), "includes": [d["id"]]})
            if old:
                notes.append(f"new module '{d['id']}' was in no feature - added as its own check box (unticked)")
    if not any(f.get("id") == "base_only" for f in features):
        features.append(dict(BASE_ONLY))
        notes.append("added the 'base_only' check box (needed to install the game / server bases)")
    out = dict(old)                                                # keep other top-level keys (e.g. "version")
    out["features"], out["modules"] = features, mods
    return out, notes


if __name__ == "__main__":
    try:
        old = json.load(open(MANIFEST, encoding="utf-8"))
    except (OSError, ValueError):
        old = None
    new, notes = build(old)
    for n in notes:
        print(" -", n)
    if "--check" in sys.argv:
        print("manifest is up to date." if not notes and new == old else "manifest needs updating (run without --check).")
        sys.exit(0 if not notes and new == old else 1)
    with open(MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(new, fh, indent=2)
        fh.write("\n")
    print(f"wrote {MANIFEST}: {len(new['modules'])} module(s), {len(new['features'])} check box(es)")
