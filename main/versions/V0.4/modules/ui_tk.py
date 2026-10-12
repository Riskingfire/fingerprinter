"""Main Tk window: the Train tab, plus the extension points the other ui_* modules use.

Everything else in the window comes from its own module and is only there when that module is installed:
Detect tab (ui_detect), class display + Class guide (ui_class), Screen, Keys, Compare, Resources,
Integrations, Server...

This module is the only place that knows about tkinter. Everything it does with recording, windows,
AFK handling and scoring goes through the headless Engine (fp/engine.py), so the same logic runs
without a window (see fp/cli.py).

Extending the window from another module (see ui_keys.py for a small example):

    ui = ctx.get("ui")
    ui.add_mixin(MyTab)                                   # class whose methods become window methods
    ui.add_tab("  Title  ", "my_tab", "_build_my_tab", order=35, lock=True)
    ui.add_header_button("Text", "method_name", order=15)
    ui.add_session("detect", "detect_tab", "toggle_detect")   # a tab the hotkey can start / stop
    ctx.hooks.on("ui.built", fn(app))  /  "ui.tick" fn(app)  /  "ui.profiles_refreshed" fn(app)

lock=True tabs are disabled while a Train / Detect session runs (the old behaviour for the first five tabs).

Slots other modules can fill (plain ttk.Frames that stay empty when nobody uses them):
    app.train_class_slot   Train tab, "Live" box        app.train_info_slot   Train tab, under the profile summary
Hooks fired by this window: ui.built, ui.tick, ui.profiles_refreshed, ui.ids_changed(app),
    ui.profile_selected(app, profile_or_None), ui.saved_details(profile) -> list of text lines (collect).
"""
NAME = "ui_tk"
DESCRIPTION = "Main window with the Train tab; other ui_* modules plug tabs, panels and buttons into it"
REQUIRES = []
ORDER = 5

import os
import shutil
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from fp.settings import (AFK_DEFAULT_KEY, AFK_IDLE_SECS, AFK_KEYS, EXT, HOTKEY, NEW_LABEL, PROFILE_DIR,
                         TAG_MIN_WINDOWS, TARGET_WINDOWS)
from fp.embedding import ID_DEFAULT_LEN, ID_MAX_LEN, fingerprint_id, format_id
from fp.profile import Profile, stability_text
from fp.store import list_profiles, load_banlist, profile_path, read_summary, sanitize, tags_line
from fp.keybinds import keys_line


class UIRegistry:
    """What add-on modules fill in before the window is built."""

    def __init__(self):
        self.mixins, self.tabs, self.header_buttons, self.sessions = [], [], [], []

    def add_mixin(self, cls):
        self.mixins.append(cls)

    def add_tab(self, title, attr, build, order=50, lock=True):
        """title: tab label; attr: name of the ttk.Frame attribute created for it on the window;
        build: name of the window method that fills that frame."""
        self.tabs.append({"title": title, "attr": attr, "build": build, "order": order, "lock": lock})

    def add_header_button(self, text, method, order=50):
        self.header_buttons.append({"text": text, "method": method, "order": order})

    def add_session(self, mode, attr, toggle):
        """Register a tab that runs a session (like Detect) so the start/stop hotkey knows it.
        mode: engine mode name; attr: the tab's frame attribute; toggle: window method that starts/stops it."""
        self.sessions.append({"mode": mode, "attr": attr, "toggle": toggle})


class BaseApp(tk.Tk):
    def __init__(self, ctx, reg):
        super().__init__()
        self.ctx, self.reg, self.hooks, self.engine = ctx, reg, ctx.hooks, ctx.engine
        self.title("Play Fingerprint")
        self.geometry("1120x860")
        self.minsize(900, 680)
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        # A consistent, higher-contrast desktop theme.
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        # A consistent, higher-contrast desktop theme.
        self.configure(bg="#eef2f7")
        style.configure(".", font=("Segoe UI", 10))
        style.configure("TFrame", background="#eef2f7")
        style.configure("TLabel", background="#eef2f7", foreground="#182235")
        style.configure("TLabelFrame", background="#eef2f7", bordercolor="#cbd5e1")
        style.configure("TLabelFrame.Label", background="#eef2f7", foreground="#263b5a",
                        font=("Segoe UI", 10, "bold"))
        style.configure("TButton", padding=(12, 7), background="#e0e8f4", foreground="#183153")
        style.map("TButton", background=[("active", "#cbdcf3"), ("disabled", "#e5e7eb")])
        style.configure("TNotebook", background="#eef2f7", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 10), font=("Segoe UI", 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", "#ffffff"), ("!selected", "#dce5f1")],
                  foreground=[("selected", "#1756a9"), ("!selected", "#34445b")])
        style.configure("Treeview", rowheight=28, background="#ffffff", fieldbackground="#ffffff",
                        foreground="#182235", bordercolor="#d5deea")
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"), padding=7,
                        background="#dce5f1", foreground="#233955")
        style.map("Treeview", background=[("selected", "#cfe2ff")],
                  foreground=[("selected", "#102b50")])
        self.rec            # create the recorder now (starts the idle F8 listener, as before)
        self.id_len = tk.IntVar(value=ID_DEFAULT_LEN)
        self._train_emb = None
        self.afk_auto = tk.BooleanVar(value=True)
        self.track_keys = tk.BooleanVar(value=True)
        self.afk_secs = tk.IntVar(value=AFK_IDLE_SECS)
        self.afk_key_var = tk.StringVar(value=AFK_DEFAULT_KEY)
        self._last_counts = (0, 0, time.perf_counter())
        self.use_tags = tk.BooleanVar(value=False)   # Detect: compare only against same-tag windows
        self.strict = tk.DoubleVar(value=1.8)        # shared by Detect and Screen (whichever are installed)

        top = ttk.Frame(self, padding=(14, 12, 14, 0))
        top.pack(fill="x")
        ttk.Label(top, text="Play Fingerprint", font=("TkDefaultFont", 16, "bold")).pack(side="left")
        self.rec_label = tk.Label(top, text="", fg="#d11", font=("TkDefaultFont", 11, "bold"))
        self.rec_label.pack(side="right")
        # Buttons contributed by modules (Class guide, Server...), kept in the always-visible header.
        for hb in sorted(reg.header_buttons, key=lambda b: b["order"]):
            ttk.Button(top, text=hb["text"], command=getattr(self, hb["method"])).pack(side="right", padx=8)
        spin = ttk.Spinbox(top, from_=16, to=ID_MAX_LEN, increment=16, width=4,
                           textvariable=self.id_len, command=self.refresh_ids)
        spin.pack(side="right")
        spin.bind("<Return>", self.refresh_ids)
        spin.bind("<FocusOut>", self.refresh_ids)
        ttk.Label(top, text="ID length").pack(side="right", padx=(8, 4))

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=12, pady=10)
        self.train_tab = ttk.Frame(self.nb, padding=14)
        self.nb.add(self.train_tab, text="  Train  ")
        self._locked_frames = [self.train_tab]
        builders = [self._build_train]
        for spec in sorted(reg.tabs, key=lambda t: (t["order"], t["title"])):
            frame = ttk.Frame(self.nb, padding=14)
            setattr(self, spec["attr"], frame)
            self.nb.add(frame, text=spec["title"])
            if spec["lock"]:
                self._locked_frames.append(frame)
            builders.append(getattr(self, spec["build"]))
        for build in builders:
            build()

        ttk.Label(self, text=f"Hotkey: {HOTKEY.upper()} starts/stops the current tab - works while your game has focus.",
                  foreground="#666").pack(pady=(0, 4))
        self.footer_row = ttk.Frame(self)             # modules may add status widgets here (e.g. server status)
        self.footer_row.pack(pady=(0, 10))
        self.hooks.on("train.window", self._on_train_window)
        self.refresh_profiles()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.hooks.emit("ui.built", self)             # modules attach state / start their own timers
        self.hooks.emit("app.started")
        self.after(250, self.tick)

    # ---- engine state, readable under the old attribute names ----------------------------------
    rec = property(lambda self: self.engine.rec)
    mode = property(lambda self: self.engine.mode)
    t0 = property(lambda self: self.engine.t0)
    profile = property(lambda self: self.engine.profile)
    new_windows = property(lambda self: self.engine.new_windows)
    skipped = property(lambda self: self.engine.skipped)
    afk_skipped = property(lambda self: self.engine.afk_skipped)
    afk_manual = property(lambda self: self.engine.afk_manual)

    def _push_settings(self):
        """Copy the widget variables into the engine (called every tick, so changes apply live)."""
        e = self.engine
        try:
            e.afk_secs = int(self.afk_secs.get())
        except (tk.TclError, ValueError):
            e.afk_secs = AFK_IDLE_SECS
        try:
            e.afk_auto = bool(self.afk_auto.get())
            e.track_keys = bool(self.track_keys.get())
            e.use_tags = bool(self.use_tags.get())
            e.strict = float(self.strict.get())
        except (tk.TclError, ValueError, AttributeError):
            pass


    # ---- train tab ---------------------------------------------------------
    def _build_train(self):
        t = self.train_tab
        ttk.Label(t, text="Choose which fingerprint to build or refine:").pack(anchor="w")
        self.train_sel = tk.StringVar(value=NEW_LABEL)
        self.train_combo = ttk.Combobox(t, textvariable=self.train_sel, state="readonly")
        self.train_combo.pack(fill="x", pady=(4, 8))
        self.train_combo.bind("<<ComboboxSelected>>", lambda e: self._train_sel_changed())

        row = ttk.Frame(t)
        row.pack(fill="x")
        ttk.Label(row, text="New name:").pack(side="left")
        self.new_name = tk.StringVar()
        self.name_entry = ttk.Entry(row, textvariable=self.new_name)
        self.name_entry.pack(side="left", fill="x", expand=True, padx=(8, 0))

        self.train_info = ttk.Label(t, text="", foreground="#555")
        self.train_info.pack(anchor="w", pady=(8, 0))
        self.train_info_slot = ttk.Frame(t)           # ui_class puts the class summary here
        self.train_info_slot.pack(fill="x")

        idrow = ttk.Frame(t)
        idrow.pack(fill="x", pady=(6, 0))
        ttk.Label(idrow, text="Fingerprint ID:").pack(side="left")
        self.train_id_var = tk.StringVar()
        ttk.Entry(idrow, textvariable=self.train_id_var, state="readonly",
                  font=("TkFixedFont", 9)).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(idrow, text="Copy", width=6,
                   command=lambda: self.copy_text(self.train_id_var.get())).pack(side="left")

        self.train_btn = ttk.Button(t, text=f"Start training ({HOTKEY.upper()})", command=self.toggle_train)
        self.train_btn.pack(fill="x", pady=14, ipady=6)

        box = ttk.LabelFrame(t, text="Live", padding=10)
        box.pack(fill="x")
        self.tv = {k: tk.StringVar(value="-") for k in ("time", "wins", "mouse", "keys", "tag")}

        def live_row(label, key):
            r = ttk.Frame(box)
            r.pack(fill="x", pady=1)
            ttk.Label(r, text=label, width=20).pack(side="left")
            ttk.Label(r, textvariable=self.tv[key]).pack(side="left")
        for label, key in (("Elapsed", "time"), ("Windows captured", "wins"),
                           ("Mouse events/s", "mouse"), ("Key presses/s", "keys")):
            live_row(label, key)
        self.train_class_slot = ttk.Frame(box)        # ui_class puts "Class so far" / "ADS so far" here
        self.train_class_slot.pack(fill="x")
        live_row("Context tag", "tag")
        ttk.Label(box, text="Training amount").pack(anchor="w", pady=(8, 2))
        self.train_bar = ttk.Progressbar(box, maximum=100)
        self.train_bar.pack(fill="x")
        ttk.Checkbutton(box, text="Track individual keys (per-key usage & keybinds)",
                        variable=self.track_keys).pack(anchor="w", pady=(8, 0))

        afk = ttk.LabelFrame(t, text="AFK handling (training data cleanup)", padding=10)
        afk.pack(fill="x", pady=(10, 0))
        self.afk_status = tk.Label(afk, text="Not recording", fg="#888", font=("TkDefaultFont", 10, "bold"))
        self.afk_status.pack(anchor="w")
        row = ttk.Frame(afk)
        row.pack(fill="x", pady=(6, 0))
        self.afk_btn = ttk.Button(row, text="Mark me AFK", command=self.toggle_afk_manual, state="disabled")
        self.afk_btn.pack(side="left")
        ttk.Label(row, text="   Key:").pack(side="left")
        kc = ttk.Combobox(row, textvariable=self.afk_key_var, values=AFK_KEYS, state="readonly", width=12)
        kc.pack(side="left", padx=4)
        kc.bind("<<ComboboxSelected>>", lambda e: self._set_afk_key())
        row2 = ttk.Frame(afk)
        row2.pack(fill="x", pady=(6, 0))
        ttk.Checkbutton(row2, text="Auto-detect idle after", variable=self.afk_auto).pack(side="left")
        ttk.Spinbox(row2, from_=3, to=15, width=3, textvariable=self.afk_secs).pack(side="left", padx=4)
        ttk.Label(row2, text="seconds").pack(side="left")
        ttk.Label(afk, text="Windows containing AFK time are left out of the fingerprint. Training only - "
                            "the AFK key is never recorded. Game-state AFK rules (Integrations tab) count too.",
                  foreground="#666", wraplength=560, justify="left").pack(anchor="w", pady=(6, 0))
        ttk.Label(t, text="Play normally for 10+ minutes (more sessions = a sturdier fingerprint).\n"
                          "Re-select the same fingerprint later to keep refining it.",
                  foreground="#666", justify="left").pack(anchor="w", pady=10)

    def _train_sel_changed(self):
        new = self.train_sel.get() == NEW_LABEL
        self.name_entry.configure(state="normal" if new else "disabled")
        p = None
        if not new:
            try:
                p = Profile.load(profile_path(self.train_sel.get()))
                tl = tags_line(p)
                self.train_info.config(text=f"{len(p.windows)} windows, "
                                            f"{p.seconds/60:.1f} min recorded, "
                                            f"{os.path.getsize(profile_path(p.name))/1024:.0f} KB  |  "
                                            f"{stability_text(p)}"
                                            + (f"\n{tl}" if tl else ""))
                self._train_emb = p.embedding()
                self.show_train_id()
            except Exception as e:
                p = None
                self.train_info.config(text=f"Could not read: {e}")
        else:
            self.train_info.config(text="")
            self._train_emb = None
            self.show_train_id()
        self.hooks.emit("ui.profile_selected", self, p)    # e.g. ui_class shows the class of the selection

    # ---- fingerprint ID helpers -------------------------------------------
    def _idlen(self):
        try:
            v = int(self.id_len.get())
        except (tk.TclError, ValueError):
            v = ID_DEFAULT_LEN
        return max(16, min(ID_MAX_LEN, v))

    def _id_text(self, emb):
        if emb is None:
            return "(not enough data yet)"
        return format_id(fingerprint_id(emb), self._idlen())

    def show_train_id(self):
        self.train_id_var.set(self._id_text(self._train_emb))

    def refresh_ids(self, *_):
        self.id_len.set(self._idlen())
        self.show_train_id()
        self.hooks.emit("ui.ids_changed", self)          # e.g. ui_detect redraws the live ID
        if self.mode is None:
            self.refresh_profiles()

    def copy_text(self, text):
        if text and not text.startswith("("):
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update()

    # ---- profile lists -----------------------------------------------------
    def refresh_profiles(self):
        names = list_profiles()
        self.train_combo["values"] = [NEW_LABEL] + names
        if self.train_sel.get() not in [NEW_LABEL] + names:
            self.train_sel.set(NEW_LABEL)
        self._train_sel_changed()
        self.hooks.emit("ui.profiles_refreshed", self)    # tabs from other modules refresh themselves

    def import_profile(self):
        path = filedialog.askopenfilename(title="Import fingerprint",
                                          filetypes=[("Fingerprint", f"*{EXT}"), ("All", "*.*")])
        if not path:
            return
        try:
            p = Profile.load(path)
            os.makedirs(PROFILE_DIR, exist_ok=True)
            shutil.copy(path, profile_path(sanitize(p.name) or "imported"))
            self.refresh_profiles()
        except Exception as e:
            messagebox.showerror("Import failed", str(e))

    # ---- periodic work -----------------------------------------------------
    def refresh_stats(self):
        if not self.mode:
            return
        now = time.perf_counter()
        m, k, t = self._last_counts
        dt = now - t
        if dt >= 1.0:
            mr, kr = (self.rec.n_moves - m) / dt, (self.rec.n_keys - k) / dt
            self._last_counts = (self.rec.n_moves, self.rec.n_keys, now)
            if self.mode == "train":
                self.tv["mouse"].set(f"{mr:.0f}")
                self.tv["keys"].set(f"{kr:.1f}")
        if self.mode == "train":
            el = now - self.t0
            self.tv["time"].set(f"{int(el//60)}:{int(el%60):02d}")
            total = len(self.profile.windows)
            self.tv["wins"].set(f"{self.new_windows} new  ({total} total, "
                                f"{self.skipped} low-activity, {self.afk_skipped} AFK skipped)")
            self.train_bar["value"] = min(100, total / TARGET_WINDOWS * 100)

    # ---- start / stop (UI side; the engine does the recording and scoring) -----------------------
    def toggle_current(self):
        """Hotkey: stop whatever is running, or start the session that belongs to the selected tab."""
        if self.mode == "train":
            return self.toggle_train()
        for spec in self.reg.sessions:
            if self.mode == spec["mode"]:
                return getattr(self, spec["toggle"])()
        if self.mode is None:
            sel = self.nb.select()
            if sel == str(self.train_tab):
                return self.toggle_train()
            for spec in self.reg.sessions:
                frame = getattr(self, spec["attr"], None)
                if frame is not None and sel == str(frame):
                    return getattr(self, spec["toggle"])()

    def _lock_tabs(self, active_frame):
        """Disable the lock=True tabs while a session runs (all but the active one)."""
        for f in self._locked_frames:
            self.nb.tab(f, state="normal" if active_frame is None or f is active_frame else "disabled")

    def toggle_train(self):
        if self.mode not in (None, "train"):             # another session (e.g. Detect) is running
            return
        if self.mode == "train":
            return self.stop_train()
        sel = self.train_sel.get()
        try:
            if sel == NEW_LABEL:
                name = sanitize(self.new_name.get())
                if not name:
                    messagebox.showinfo("Name needed", "Type a name for the new fingerprint.")
                    return
                if os.path.exists(profile_path(name)):
                    if not messagebox.askyesno("Exists", f"'{name}' already exists. Add to it?"):
                        return
                    profile = Profile.load(profile_path(name))
                else:
                    profile = Profile(name)
            else:
                profile = Profile.load(profile_path(sel))
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return
        self._push_settings()
        self.tv["tag"].set("-")
        self.afk_btn.config(state="normal", text="Mark me AFK")
        try:
            self.engine.start_train(profile)
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return
        self._last_counts = (0, 0, self.engine.t0)
        self.train_btn.config(text=f"Stop & save ({HOTKEY.upper()})")
        self.rec_label.config(text="● RECORDING")
        self._lock_tabs(self.train_tab)

    def stop_train(self):
        res = self.engine.stop_train()          # saves the fingerprint and announces profile.saved
        self.afk_btn.config(state="disabled", text="Mark me AFK")
        self.afk_status.config(text="Not recording", fg="#888")
        self.rec_label.config(text="")
        self.train_btn.config(text=f"Start training ({HOTKEY.upper()})")
        self._lock_tabs(None)
        p = res["profile"]
        if p and res["new_windows"] > 0:
            if res["error"] is not None:
                messagebox.showerror("Save failed", str(res["error"]))
            else:
                try:
                    tl = tags_line(p)
                    details = "".join(f"{ln}\n" for lines in self.hooks.collect("ui.saved_details", p) for ln in lines)
                    messagebox.showinfo("Saved", f"'{p.name}' saved\n{len(p.windows)} windows - {res['size']/1024:.0f} KB\n\n"
                                                 + details
                                                 + f"  {stability_text(p)}\n"
                                                 f"  {keys_line(p)}\n"
                                                 + (f"  {tl}\n" if tl else "")
                                                 + f"\nID: {self._id_text(p.embedding())}\n\n"
                                                   f"{profile_path(p.name)}")
                except Exception as e:
                    messagebox.showerror("Save failed", str(e))
        else:
            messagebox.showinfo("Nothing saved", "No full windows were captured. "
                                                 "Train for at least ~30 seconds of real play.")
        self.new_name.set("")
        self.refresh_profiles()
        if p:
            self.train_sel.set(p.name)
            self._train_sel_changed()

    # ---- AFK handling (training only) --------------------------------------
    def _set_afk_key(self):
        self.engine.set_afk_key(self.afk_key_var.get())

    def toggle_afk_manual(self):
        if self.mode != "train":
            return
        self.engine.toggle_afk_manual()
        self._render_afk()

    def _render_afk(self):
        """Refresh the AFK status label / button from the engine's state."""
        self.afk_btn.config(text="I'm back" if self.engine.afk_manual else "Mark me AFK")
        kind, rule = self.engine.afk_state()
        if kind == "manual":
            self.afk_status.config(text="AFK (manual) - not being recorded into the fingerprint", fg="#d11")
        elif kind == "rule":
            self.afk_status.config(text=f"AFK (game-state rule '{rule}') - not being recorded "
                                        "into the fingerprint", fg="#d77a00")
        elif kind == "idle":
            self.afk_status.config(text="AFK (idle detected) - not being recorded into the fingerprint", fg="#d77a00")
        else:
            self.afk_status.config(text="Active - counted", fg="#1a8a3a")

    # ---- engine events -> widgets ----------------------------------------------
    def _on_train_window(self, info):
        if info.get("state") != "window":
            return
        self.tv["tag"].set(info["tag"] or "(none)")
        self._train_emb = info["embedding"]
        self.show_train_id()

    def tick(self):
        try:
            if self.engine.poll_hotkey():
                self.toggle_current()
            self._push_settings()
            self.engine.tick()                    # AFK key, context, AFK sampling, train / detect evaluation
            if self.mode == "train":
                self._render_afk()
            self.refresh_stats()
            self.hooks.emit("ui.tick", self)
        except Exception as e:  # never kill the UI loop
            print("tick error:", e)
        finally:
            self.after(250, self.tick)

    def on_close(self):
        if self.mode == "train":
            if not messagebox.askyesno("Quit", "Training is running. Stop, save and quit?"):
                return
            self.stop_train()
        self.engine.shutdown()                    # stops sessions, tells modules ("shutdown"), stops listeners
        self.destroy()


def run(ctx):
    reg = ctx.get("ui")
    cls = type("App", tuple(reg.mixins) + (BaseApp,), {})      # the window = base + every UI module's methods
    cls(ctx, reg).mainloop()


def register(ctx):
    ctx.provide("ui", UIRegistry())
    ctx.add_frontend("tk", run, priority=100, description="Tk window")
