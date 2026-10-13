"""History tab: every Train / Detect session you ran (needs session_log)."""
NAME = "ui_history"
DESCRIPTION = "History tab: list of recorded Train / Detect sessions"
REQUIRES = ["ui_tk", "session_log"]
ORDER = 55

import tkinter as tk
from tkinter import ttk, messagebox


class HistoryTab:
    """Methods added to the main window (mixin). `self` is the window."""

    def _build_history(self):
        t = self.history_tab
        ttk.Label(t, text="Session history", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(t, text="Every Train and Detect session, newest first.", foreground="#52647d").pack(anchor="w", pady=(4, 10))
        bar = ttk.Frame(t)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Button(bar, text="Refresh", command=self.refresh_history).pack(side="left")
        ttk.Button(bar, text="Clear history", command=self.clear_history).pack(side="left", padx=8)

        cols = ("mode", "duration", "profiles", "windows", "result")
        self.history_tree = ttk.Treeview(t, columns=cols, show="tree headings", height=16)
        self.history_tree.heading("#0", text="STARTED")
        self.history_tree.column("#0", width=170, stretch=False)
        for col, title, width in (("mode", "Mode", 80), ("duration", "Duration", 100), ("profiles", "Fingerprint(s)", 260),
                                  ("windows", "New windows", 100), ("result", "Result", 160)):
            self.history_tree.heading(col, text=title)
            self.history_tree.column(col, width=width, anchor="w" if col == "profiles" else "center")
        sb = ttk.Scrollbar(t, orient="vertical", command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.history_tree.pack(fill="both", expand=True)
        self.history_summary = ttk.Label(t, text="", justify="left")
        self.history_summary.pack(anchor="w", pady=(8, 0))

    def refresh_history(self):
        log = self.ctx.get("session_log")
        if log is None or not self.winfo_exists():
            return
        self.history_tree.delete(*self.history_tree.get_children())
        entries = log.entries()
        for e in reversed(entries):
            if e.get("mode") == "train":
                result = "saved" if e.get("saved") else ("error" if e.get("error") else "not saved")
            else:
                result = "-"
            self.history_tree.insert("", "end", text=e.get("started", "?"), values=(
                e.get("mode", "?"), log.fmt_duration(e.get("seconds")), ", ".join(e.get("profiles") or []) or "-",
                e.get("new_windows", 0) if e.get("mode") == "train" else "-", result))
        totals = log.totals(entries)
        parts = [f"{mode}: {n} session(s), {log.fmt_duration(secs)}" for mode, (n, secs) in sorted(totals.items())]
        self.history_summary.config(text="   |   ".join(parts) if parts else "No sessions recorded yet.")

    def clear_history(self):
        if messagebox.askyesno("Clear history", "Delete the whole session history?", parent=self):
            self.ctx.get("session_log").clear()
            self.refresh_history()


def register(ctx):
    ui = ctx.get("ui")
    ui.add_mixin(HistoryTab)
    ui.add_tab("  History  ", "history_tab", "_build_history", order=55, lock=False)

    def on_built(app):
        app.refresh_history()
        # session_log registered earlier (priority 10), so the new entry is already on disk here.
        ctx.hooks.on("session.stop", lambda mode, result=None: app.after(200, app.refresh_history))

    ctx.hooks.on("ui.built", on_built)
