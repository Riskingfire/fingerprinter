"""Modules tab: see what is loaded / skipped (and why), and turn modules off or on for the next start."""
NAME = "ui_modules"
DESCRIPTION = "Modules tab: loaded / skipped modules with reasons; enable or disable modules for the next start"
REQUIRES = ["ui_tk"]
ORDER = 90

import json
import os
import tkinter as tk
from tkinter import ttk, messagebox

from fp.plugins import MODULES_CONFIG_FILE, load_modules_config

PROTECTED = ("ui_tk", "ui_modules")        # turning these off would remove the window / this tab


def save_disabled(names):
    """Write the `disabled` list into modules.json, keeping every other key (e.g. `only`)."""
    cfg = load_modules_config()
    cfg["disabled"] = sorted(set(names))
    os.makedirs(os.path.dirname(MODULES_CONFIG_FILE), exist_ok=True)
    tmp = MODULES_CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=1)
    os.replace(tmp, MODULES_CONFIG_FILE)


class ModulesTab:
    """Methods added to the main window (mixin). `self` is the window."""

    def _build_modules_tab(self):
        t = self.modules_tab
        ttk.Label(t, text="Modules", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(t, text="Select a module and use the button to switch it off or on. Changes apply the next time Fingerprinter starts.",
                  foreground="#52647d", wraplength=850, justify="left").pack(anchor="w", pady=(4, 10))
        bar = ttk.Frame(t)
        bar.pack(fill="x", pady=(0, 8))
        self.modules_btn = ttk.Button(bar, text="Disable on next start", command=self.toggle_selected_module)
        self.modules_btn.pack(side="left")
        ttk.Button(bar, text="Refresh", command=self.refresh_modules_tab).pack(side="left", padx=8)
        self.modules_note = ttk.Label(bar, text="", foreground="#7a4b00")
        self.modules_note.pack(side="left", padx=8)

        cols = ("status", "next", "requires", "description")
        self.modules_tree = ttk.Treeview(t, columns=cols, show="tree headings", height=16)
        self.modules_tree.heading("#0", text="MODULE")
        self.modules_tree.column("#0", width=150, stretch=False)
        for col, title, width in (("status", "Now", 220), ("next", "Next start", 90), ("requires", "Requires", 130), ("description", "Description", 480)):
            self.modules_tree.heading(col, text=title)
            self.modules_tree.column(col, width=width, anchor="w")
        self.modules_tree.tag_configure("bad", foreground="#c00")
        self.modules_tree.tag_configure("off", foreground="#888")
        sb = ttk.Scrollbar(t, orient="vertical", command=self.modules_tree.yview)
        self.modules_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.modules_tree.pack(fill="both", expand=True)
        self.modules_tree.bind("<<TreeviewSelect>>", lambda e: self._modules_selected())
        self.refresh_modules_tab()

    def _modules_disabled(self):
        return set(load_modules_config().get("disabled") or [])

    def refresh_modules_tab(self):
        ctx = self.ctx
        disabled = self._modules_disabled()
        self.modules_tree.delete(*self.modules_tree.get_children())
        for name in sorted(set(ctx.available) | set(ctx.loaded) | set(ctx.skipped)):
            info = ctx.available.get(name)
            if name in ctx.loaded:
                status, tag = "Loaded", ()
            elif name in ctx.skipped:
                status, tag = f"Not loaded: {ctx.skipped[name]}", ("bad",)
            else:
                status, tag = "Not selected", ("off",)
            self.modules_tree.insert("", "end", iid=name, text=name, tags=tag, values=(
                status, "off" if name in disabled else "on", ", ".join(getattr(info, "requires", []) or []) or "-",
                getattr(info, "description", "") or ""))
        self._modules_selected()

    def _modules_selected(self):
        sel = self.modules_tree.selection()
        if not sel:
            self.modules_btn.config(text="Disable on next start", state="disabled")
            return
        name = sel[0]
        if name in PROTECTED:
            self.modules_btn.config(text="Required", state="disabled")
        else:
            self.modules_btn.config(text="Enable on next start" if name in self._modules_disabled() else "Disable on next start",
                                    state="normal")

    def toggle_selected_module(self):
        sel = self.modules_tree.selection()
        if not sel or sel[0] in PROTECTED:
            return
        name = sel[0]
        disabled = self._modules_disabled()
        dependants = sorted(n for n, i in self.ctx.available.items() if name in (getattr(i, "requires", []) or []) and n not in disabled)
        if name in disabled:
            disabled.discard(name)
        else:
            if dependants and not messagebox.askyesno(
                    "Disable module", f"These modules need '{name}' and will be skipped too:\n\n  " + "\n  ".join(dependants) + "\n\nContinue?", parent=self):
                return
            disabled.add(name)
        try:
            save_disabled(disabled)
        except OSError as e:
            messagebox.showerror("Modules", f"Could not save modules.json:\n{e}", parent=self)
            return
        self.modules_note.config(text="Saved - restart Fingerprinter to apply.")
        self.refresh_modules_tab()
        if self.modules_tree.exists(name):
            self.modules_tree.selection_set(name)


def register(ctx):
    ui = ctx.get("ui")
    ui.add_mixin(ModulesTab)
    ui.add_tab("  Modules  ", "modules_tab", "_build_modules_tab", order=90, lock=False)
