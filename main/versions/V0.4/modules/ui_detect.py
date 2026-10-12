"""Detect tab: live scoring of who is at the controls against the ticked fingerprints.

All the scoring is done by the headless Engine (fp/engine.py); this module is only the window side.
Without it the window has no Detect tab (and the hotkey only drives Train). The engine and the
`python fingerprinter.py detect` command are unaffected.

Other modules can add to this tab without this module knowing about them:
  * app.detect_class_slot       an empty frame under the verdict (ui_class shows the live class there)
  * hook "ui.detect_reset"(app) fired when a new Detect session starts - clear any live display
  * hook "detect.update"(info)  the engine's live result (subscribe in your own "ui.built" handler)
"""
NAME = "ui_detect"
DESCRIPTION = "Detect tab: live scoring against fingerprints, banned-account flag, live keybinds"
REQUIRES = ['ui_tk']
ORDER = 10

import tkinter as tk
from tkinter import ttk, messagebox

from fp.embedding import fingerprint_id, format_id
from fp.keybinds import display_key, key_rows, keybind_summary
from fp.settings import HOTKEY, TAG_MIN_WINDOWS
from fp.store import list_profiles, load_banlist, profile_path, read_summary


class DetectTab:
    """Methods added to the main window (mixin). `self` is the window."""

    def _build_detect(self):
        t = self.detect_tab
        self.det_vars = {}
        self.det_rows = {}
        self._live_emb = None
        head = ttk.Frame(t)
        head.pack(fill="x")
        ttk.Label(head, text="Fingerprints to compare against:").pack(side="left")
        ttk.Button(head, text="Import...", command=self.import_profile).pack(side="right")
        ttk.Button(head, text="Refresh", command=self.refresh_profiles).pack(side="right", padx=6)

        self.det_list = ttk.Frame(t)
        self.det_list.pack(fill="x", pady=(6, 8))

        self.detect_btn = ttk.Button(t, text=f"Start detecting ({HOTKEY.upper()})", command=self.toggle_detect)
        self.detect_btn.pack(fill="x", pady=6, ipady=6)

        srow = ttk.Frame(t)
        srow.pack(fill="x", pady=(2, 8))
        ttk.Label(srow, text="Strictness").pack(side="left")
        ttk.Label(srow, text="strict").pack(side="left", padx=(10, 2))
        ttk.Scale(srow, from_=1.2, to=3.0, variable=self.strict, orient="horizontal").pack(
            side="left", fill="x", expand=True)
        ttk.Label(srow, text="lenient").pack(side="left", padx=(2, 0))
        ttk.Checkbutton(t, text=f"Compare only against windows with the current context tag "
                                f"(needs {TAG_MIN_WINDOWS}+ such windows, else whole fingerprint)",
                        variable=self.use_tags).pack(anchor="w", pady=(0, 6))

        self.verdict = tk.Label(t, text="Not running", font=("TkDefaultFont", 20, "bold"), fg="#888",
                                wraplength=480, pady=12)
        self.verdict.pack(fill="x")
        self.detect_class_slot = ttk.Frame(t)          # ui_class shows "Current style: ..." here
        self.detect_class_slot.pack(fill="x")
        self.live_keys = tk.Label(t, text="", fg="#335", wraplength=480, justify="center")
        self.live_keys.pack(fill="x", pady=(0, 4))
        self.flag_label = tk.Label(t, text="", fg="#c00", wraplength=480, justify="center")
        self.flag_label.pack(fill="x", pady=(0, 4))
        lrow = ttk.Frame(t)
        lrow.pack(fill="x", pady=(0, 8))
        ttk.Label(lrow, text="Live ID:").pack(side="left")
        self.live_id_var = tk.StringVar()
        ttk.Entry(lrow, textvariable=self.live_id_var, state="readonly",
                  font=("TkFixedFont", 9)).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(lrow, text="Copy", width=6,
                   command=lambda: self.copy_text(self.live_id_var.get())).pack(side="left")
        self.results = ttk.Frame(t)
        self.results.pack(fill="x")
        self.det_status = ttk.Label(t, text="", foreground="#666", wraplength=900, justify="left")
        self.det_status.pack(anchor="w", pady=(10, 0))

    # ---- the list of fingerprints to tick -------------------------------------------------------
    def refresh_detect_list(self):
        names = list_profiles()
        ban = load_banlist()
        old = {n: v.get() for n, v in self.det_vars.items()}
        for w in self.det_list.winfo_children():
            w.destroy()
        self.det_vars = {}
        if not names:
            ttk.Label(self.det_list, text="No fingerprints yet - train one first.",
                      foreground="#a60").pack(anchor="w")
        for n in names:
            v = tk.BooleanVar(value=old.get(n, True))
            self.det_vars[n] = v
            code, emb = read_summary(profile_path(n))
            short = format_id(fingerprint_id(emb), min(self._idlen(), 32)) if emb is not None else "no ID yet"
            tag = "   (BANNED)" if n in ban else ""
            ttk.Checkbutton(self.det_list, text=f"{n}   [{code}]   {short}{tag}",
                            variable=v).pack(anchor="w")

    # ---- live keybinds line ---------------------------------------------------------------------
    def _update_live_keys(self, agg):
        total = sum(e[0] for e in agg.values())
        if total < 20:
            self.live_keys.config(text=f"Keys: gathering data ({total} presses so far)")
            return
        rows = key_rows(agg)[:6]
        usage = "  ".join(f"{display_key(r['key'])} {r['share'] * 100:.0f}%" for r in rows)
        k = keybind_summary(agg)
        extra = f"  |  unusual binds: {', '.join(k['unusual'])}" if k["unusual"] else ""
        self.live_keys.config(text=f"Live keys: {usage}\nMovement: {k['movement_text']} ({k['scheme']})  |  "
                                   f"Keybinds: {k['text']}{extra}")

    # ---- start / stop ---------------------------------------------------------------------------
    def toggle_detect(self):
        if self.mode == "train":
            return
        if self.mode == "detect":
            return self.stop_detect()
        chosen = [n for n, v in self.det_vars.items() if v.get()]
        if not chosen:
            messagebox.showinfo("Pick fingerprints", "Tick at least one fingerprint.")
            return
        profiles, problems = self.engine.load_detect_profiles(chosen)
        for kind, _n, msg in problems:
            (messagebox.showwarning("Too little data", msg) if kind == "short"
             else messagebox.showerror("Error", msg))
        if not profiles:
            return
        self._push_settings()
        self.flag_label.config(text="")
        self.live_keys.config(text="")
        self._live_emb = None
        self.live_id_var.set("")
        self.hooks.emit("ui.detect_reset", self)          # e.g. ui_class clears the live class line
        for w in self.results.winfo_children():
            w.destroy()
        self.det_rows = {}
        for p in profiles:
            row = ttk.Frame(self.results)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=p.name, width=16).pack(side="left")
            bar = ttk.Progressbar(row, maximum=100)
            bar.pack(side="left", fill="x", expand=True, padx=6)
            pct = ttk.Label(row, text="--", width=6)
            pct.pack(side="left")
            kb = ttk.Label(row, text="KB --", width=7, foreground="#555")
            kb.pack(side="left")
            self.det_rows[p.name] = (bar, pct, kb)
        try:
            self.engine.start_detect(profiles)
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return
        self._last_counts = (0, 0, self.engine.t0)
        self.detect_btn.config(text=f"Stop detecting ({HOTKEY.upper()})")
        self.verdict.config(text="Collecting...", fg="#888")
        self.rec_label.config(text="● RECORDING")
        self._lock_tabs(self.detect_tab)

    def stop_detect(self):
        self.engine.stop_detect()
        self.rec_label.config(text="")
        self.detect_btn.config(text=f"Start detecting ({HOTKEY.upper()})")
        self.verdict.config(text="Stopped", fg="#888")
        self.flag_label.config(text="")
        self.det_status.config(text="")
        self._lock_tabs(None)

    # ---- engine events -> widgets ---------------------------------------------------------------
    def _on_detect_update(self, info):
        if self.mode != "detect":
            return
        st = info["state"]
        if st == "paused":                       # game-state AFK rule: scoring paused
            self.verdict.config(text="Paused (game state)", fg="#888")
            self.det_status.config(text=f"Scoring paused - {info['why']}. Last check {info['time']}")
            return
        self._update_live_keys(info["live_agg"])
        for name, kb in info["kb_scores"].items():
            self.det_rows[name][2].config(text="KB --" if kb is None else f"KB {kb * 100:.0f}%")
        if st == "waiting":
            self.det_status.config(text="Not enough activity in the last few seconds - waiting...")
            return
        self._live_emb = info["live_embedding"]
        self.live_id_var.set(self._id_text(self._live_emb))
        if st == "partial":
            return
        for name, sim in info["similarity"].items():
            bar, pct, _kb = self.det_rows[name]
            bar["value"] = sim
            pct.config(text=f"{sim:.0f}%")
        best, verdict = info["best"], info["verdict"]
        if verdict == "banned":
            self.verdict.config(text=f"[FLAG] Matches banned account '{best}'", fg="#d11")
        elif verdict == "match":
            self.verdict.config(text=f"Playing: {best}" if info["multi"] else f"Match: {best}", fg="#1a8a3a")
        else:
            self.verdict.config(text="Unknown player", fg="#d77a00")
        self.flag_label.config(text="\n".join(info["notes"]))
        self.det_status.config(text=info["status"])


def _attach(app):
    app.hooks.on("detect.update", app._on_detect_update)


def register(ctx):
    ui = ctx.get("ui")
    ui.add_mixin(DetectTab)
    ui.add_tab("  Detect  ", "detect_tab", "_build_detect", order=10, lock=True)
    ui.add_session("detect", "detect_tab", "toggle_detect")
    ctx.hooks.on("ui.built", _attach)
    ctx.hooks.on("ui.profiles_refreshed", lambda app: app.refresh_detect_list())
    ctx.hooks.on("ui.ids_changed",
                 lambda app: app.live_id_var.set(app._id_text(app._live_emb)) if app._live_emb is not None else None)
