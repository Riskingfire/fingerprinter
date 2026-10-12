"""Integrations tab: start / stop game-state sources and watch live values."""
NAME = "ui_integrations"
DESCRIPTION = "Integrations tab (needs the integrations module)"
REQUIRES = ['ui_tk', 'integrations']
ORDER = 60

import os

from fp.settings import APP_DATA_DIR

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog


class IntegrationsTab:
    """Methods added to the main window (mixin). `self` is the window."""

    def _build_integrations(self):
        t = self.integ_tab
        ttk.Label(t, text="In-game integrations", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(t, text=INTEG_NOTICE, foreground="#7a4b00", wraplength=1000, justify="left").pack(
            anchor="w", pady=(4, 8))
        bar = ttk.Frame(t)
        bar.pack(fill="x")
        ttk.Button(bar, text="Start selected", command=self.integ_start_sel).pack(side="left")
        ttk.Button(bar, text="Stop selected", command=self.integ_stop_sel).pack(side="left", padx=6)
        ttk.Button(bar, text="Stop all", command=self.integ_stop_all).pack(side="left")
        self.integ_example_btn = ttk.Button(bar, text="Write example config", command=self.integ_write_example)
        self.integ_example_btn.pack(side="right")
        self.integ_reload_btn = ttk.Button(bar, text="Reload config", command=self.integ_reload)
        self.integ_reload_btn.pack(side="right", padx=6)

        cols = ("kind", "status", "age", "msgs", "error")
        self.integ_tree = ttk.Treeview(t, columns=cols, height=6, selectmode="browse")
        for col, title, w, stretch in (("#0", "Integration", 150, False), ("kind", "Source", 230, False),
                                       ("status", "Status", 80, False), ("age", "Last update", 90, False),
                                       ("msgs", "Messages", 110, False), ("error", "Last error", 330, True)):
            self.integ_tree.heading(col, text=title)
            self.integ_tree.column(col, width=w, stretch=stretch)
        self.integ_tree.tag_configure("running", foreground="#1a8a3a")
        self.integ_tree.tag_configure("error", foreground="#c00")
        self.integ_tree.tag_configure("off", foreground="#888")
        self.integ_tree.pack(fill="x", pady=(8, 0))
        self.integ_tree.bind("<<TreeviewSelect>>", lambda _e: self._refresh_integ())

        self.integ_ctx = ttk.Label(t, text="", font=("Segoe UI", 10, "bold"), wraplength=1000, justify="left")
        self.integ_ctx.pack(anchor="w", pady=(10, 2))
        ttk.Label(t, text="Live values of the selected integration (memory only - never saved or uploaded):",
                  foreground="#52647d").pack(anchor="w")
        self.integ_vals = tk.Text(t, height=9, state="disabled", wrap="none", font=("Consolas", 9),
                                  background="#ffffff", relief="flat", borderwidth=1)
        self.integ_vals.pack(fill="both", expand=True, pady=(2, 6))
        self.integ_msg = ttk.Label(t, text="", foreground="#555", wraplength=1000, justify="left")
        self.integ_msg.pack(anchor="w")

    def _integ_selected(self):
        sel = self.integ_tree.selection()
        return sel[0] if sel else None

    def integ_start_sel(self):
        n = self._integ_selected()
        if not n:
            messagebox.showinfo("Select an integration", "Select an integration in the list first.")
            return
        if not self.integ.start(n):
            messagebox.showwarning("Could not start", f"{n}: {self.integ.sources[n].last_error or 'failed'}")
        self._refresh_integ()

    def integ_stop_sel(self):
        n = self._integ_selected()
        if n:
            self.integ.stop(n)
        self._refresh_integ()

    def integ_stop_all(self):
        self.integ.stop_all()
        self._refresh_integ()

    def integ_reload(self):
        ok, msg = self.integ.load()
        if not ok:
            messagebox.showinfo("Reload blocked", msg)
        self.integ_tree.delete(*self.integ_tree.get_children())
        self._refresh_integ()

    def integ_write_example(self):
        if os.path.exists(self.integ.path):
            messagebox.showinfo("Already exists", f"{self.integ.path}\nalready exists - edit it directly.")
            return
        try:
            os.makedirs(APP_DATA_DIR, exist_ok=True)
            with open(self.integ.path, "w", encoding="utf-8") as fh:
                fh.write(example_config_text() + "\n")
            try:
                os.chmod(self.integ.path, 0o600)          # may hold a shared token
            except OSError:
                pass
            messagebox.showinfo("Example written", f"{self.integ.path}\n\nEverything in it is disabled and "
                                                   "uses placeholders. Edit it, set \"enabled\": true on what "
                                                   "you want, then press Reload config.")
        except Exception as e:
            messagebox.showerror("Could not write", str(e))

    def _refresh_integ(self):
        """Refresh the integrations tab. Never raises."""
        try:
            self.integ_reload_btn.config(state="disabled" if self.mode else "normal")
            rows = self.integ.rows()
            names = {r["name"] for r in rows}
            have = set(self.integ_tree.get_children())
            for iid in have - names:
                self.integ_tree.delete(iid)
            for r in rows:
                age = "never" if r["age"] is None else f"{r['age']:.1f}s ago"
                status = r["status"] if r["enabled"] else "disabled"
                err = ""
                if r["error"]:
                    err = r["error"] + ("" if r["error_age"] is None else f"  ({r['error_age']:.0f}s ago)")
                vals = (f"{r['kind']}  {r['detail']}".strip(), status, age,
                        f"{r['count']} ok / {r['bad']} bad", err)
                tag = ("off",) if not r["enabled"] else (("running",) if r["status"] == "running"
                                                         else (("error",) if r["status"] == "error" else ()))
                if r["name"] in have:
                    self.integ_tree.item(r["name"], values=vals, tags=tag)
                else:
                    self.integ_tree.insert("", "end", iid=r["name"], text=r["name"], values=vals, tags=tag)
            applies, rule = self.integ.afk_check()
            tag = self.integ.current_tag()
            self.integ_ctx.config(text=f"Game-state context now:  tag = {tag or '(none)'}   |   AFK rule: "
                                       f"{('APPLIES - ' + rule) if applies else 'none applies'}")
            sel = self._integ_selected()
            row = next((r for r in rows if r["name"] == sel), None)
            if row is None:
                text = "(select an integration above)"
            elif not row["values"]:
                text = "(no fresh values - not started, nothing received yet, or values went stale)"
            else:
                text = "\n".join(f"{k} = {v!r}" for k, v in sorted(row["values"].items())[:INTEG_MAX_KEYS])
            if text != self._integ_vals_shown:
                self._integ_vals_shown = text
                self.integ_vals.config(state="normal")
                self.integ_vals.delete("1.0", "end")
                self.integ_vals.insert("end", text)
                self.integ_vals.config(state="disabled")
            note = f"Config: {self.integ.path}" + ("" if os.path.exists(self.integ.path)
                                                   else "  (not found - nothing is configured)")
            if self.integ.warnings:
                note += "\nWarnings: " + "; ".join(self.integ.warnings[:5])
            if self.mode:
                note += "\nA session is running: config reload is blocked until it stops."
            self.integ_msg.config(text=note)
        except Exception as e:
            print("integrations ui error:", e)

    def update_integrations_ui(self):
        try:
            self._refresh_integ()
        finally:
            if self.winfo_exists():
                self.after(1000, self.update_integrations_ui)


def _attach(app):
    app.integ = app.ctx.get("integrations")
    app._integ_vals_shown = None
    app.after(1000, app.update_integrations_ui)


def register(ctx):
    global INTEG_NOTICE, INTEG_MAX_KEYS, example_config_text
    api = ctx.get("integration_api")
    INTEG_NOTICE, INTEG_MAX_KEYS = api["notice"], api["INTEG_MAX_KEYS"]
    example_config_text = api["example_config_text"]
    ui = ctx.get("ui")
    ui.add_mixin(IntegrationsTab)
    ui.add_tab("  Integrations  ", "integ_tab", "_build_integrations", order=60, lock=False)
    ctx.hooks.on("ui.built", _attach)
