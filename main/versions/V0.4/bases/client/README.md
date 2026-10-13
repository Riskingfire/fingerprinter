# Play Fingerprint V0.4 — base + modules

Split into a **base** that runs on its own and **modules** that
plug into it. Nothing in the base has to be edited to add, remove or choose modules.

```
bases/client/                          THIS BASE (what the installer puts in the install folder)
  fingerprinter.py                     launcher (python fingerprinter.py)
  fingerprinterV0.4.py                 same launcher under the versioned name
  fp/                                  BASE - never imports tkinter or a module
    settings  recorder  features  classify  embedding  profile  store  keybinds  analysis  context
    hooks.py     event bus          engine.py   headless Train/Detect engine
    plugins.py   module loader      cli.py      headless commands       app.py   entry logic
modules/                               OPTIONAL - one level up in the repo, beside bases/ (the installer puts it next to fp/)
  integrations.py   game-state sources, AFK rules, context tags      (no UI)
  server_sync.py    Render server sync                               (no UI)
  ui_tk.py          the window + the Train tab (nothing else is built in)
  ui_detect.py      Detect tab (live scoring)
  ui_class.py       Class guide button + class display (Train live box, Detect, saved dialog)
  ui_screen / ui_keys / ui_compare / ui_resources                    tabs
  ui_integrations   Integrations tab   (needs ui_tk + integrations)
  ui_server         Server... dialog   (needs ui_tk + server_sync)
  ui_library        Library tab: every fingerprint in one sortable table
  ui_notes          Notes tab: a note per fingerprint (also shown in the saved dialog)
  ui_history        History tab: every Train / Detect session       (needs session_log)
  session_log.py    records sessions to history.json; adds the `history` command   (no UI)
  export_tools.py   adds the `export`, `backup` and `restore` commands             (no UI)
  ui_export         Export... button                                  (needs export_tools)
  ui_zoom           Zoom button: text size 100 / 115 / 130 / 150 %
  ui_modules        Modules tab: what loaded / was skipped, switch modules off or on
  modules.json      the installer's component list - kept in step by tools/make_manifest.py
  _template_module.py   copy me to write your own
```

The loader looks for `modules/` beside the launcher (installed layout) and, failing that, at `../../modules`
(running straight from the repo / unzipped folder), so both work without any setting.

## Running it

```
python fingerprinter.py                          everything (same window as before)
python fingerprinter.py --no-modules list        base only
python fingerprinter.py --no-modules train Me    headless training; F8 or Ctrl+C saves (--minutes N, --no-keys)
python fingerprinter.py --no-modules detect      headless live scoring against all fingerprints (names optional)
python fingerprinter.py --only ui_tk,ui_keys     window with just Train + Keys (requirements are added for you)
python fingerprinter.py --disable ui_detect,ui_class   no Detect tab, no Class guide, no class display
python fingerprinter.py --disable ui_server,integrations     everything except these
python fingerprinter.py modules                  what loaded, what didn't, and why
```
`--disable`/`--only` can also live in `%USERPROFILE%\AppData\fingerprint\modules.json`:
`{"disabled": ["ui_resources"]}` or `{"only": ["ui_tk", "integrations"]}`.
A module whose requirement is missing is skipped with a reason; a module that crashes at import or in
`register()` is skipped too and never takes the app down.

## Adding a module (no code edits)

**Nothing in the base, the launchers or any other module is ever edited, imported from or listed in for a new module.**
The loader finds it by scanning the folder, and the module reaches everything through `register(ctx)`:
a tab (`ui.add_tab`), a header button, a hook, a service, a front-end, or a **command-line command**
(`ctx.add_command("mycmd", run, help="...", setup=lambda p: p.add_argument("--x"))` -> `python fingerprinter.py mycmd --x 1`).


Drop a `.py` file (or a folder with `__init__.py`) in `modules/` (the top-level one), or in
`%USERPROFILE%\AppData\fingerprint\modules\` for your own, or anywhere via `--modules-dir` /
`FP_MODULES_PATH`. It needs a `register(ctx)` function; `NAME`, `DESCRIPTION`, `REQUIRES`, `OPTIONAL`, `ORDER`
are optional literals. See `modules/_template_module.py`.

Modules talk to the base and to each other only through:
* **hooks** – `train.window`, `detect.update`, `profile.saved`, `session.start/stop`, `engine.tick`,
  `banlist.changed`, `shutdown`, `app.started`; UI-only: `ui.built`, `ui.tick`, `ui.profiles_refreshed`.
* **context providers** – answer `context.sample` / `context.nums` to supply game state (that's what
  `integrations` does); the engine uses AFK rules and tags if any module answers, and works without.
* **services** – `ctx.provide("name", obj)` / `ctx.get("name")`.
* **UI extension points** (from `ui_tk`) – `ui.add_tab(...)`, `ui.add_header_button(...)`, `ui.add_mixin(cls)`, `ui.add_session(...)`
  (a tab the hotkey can start/stop); empty slots `train_class_slot`, `train_info_slot`, `detect_class_slot`; UI hooks
  `ui.profile_selected`, `ui.saved_details`, `ui.ids_changed`, `ui.detect_reset`.

## Backwards compatibility

* **Data**: nothing moved or changed. Same folder (`%USERPROFILE%\AppData\fingerprint`), same `.fpr` format,
  `banlist.json`, `integrations.json`, `server_config.json`, `integration_plugins/`. Files written by the old
  version load in the new one and vice versa (checked byte-for-byte on the saved JSON).
* **Behaviour**: feature extraction, classes, IDs, matching, screening and keybind maths are the original code
  (cut and pasted, not rewritten). Tab order, tab locking while a session runs, hotkeys and every dialog are unchanged. Each UI part only appears when its
  module is loaded: with `ui_class` off the class lines, Class guide button and class rows disappear; with `ui_detect` off there is
  no Detect tab (the hotkey then only drives Train).
* **Entry points**: `python fingerprinter.py` and `python fingerprinterV0.4.py` start the same program. The old
  single-file launcher name is gone; import from `fp` instead.
* **What did change** (only matters if you poked at internals): the window no longer owns the session state; it lives
  on `app.engine` and is still readable as `app.mode`, `app.profile`, `app.t0`, etc. Server state moved from
  `app.srv_cfg` / `app.srv` (dict) to `app.srv` (a `ServerClient`: `.cfg`, `.state`). Integrations are `app.integ` as before.
* PyInstaller: bundle the `modules/` folder as data (modules are found by folder, not by import).
