"""Library tab: every saved fingerprint in one sortable table (class, ID, training time, stability...)."""
NAME = "ui_library"
DESCRIPTION = "Library tab: overview of all saved fingerprints, sortable, with copy-ID and open-folder"
REQUIRES = ["ui_tk"]
ORDER = 25

import gzip
import json
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox

from fp.settings import PROFILE_DIR
from fp.store import list_profiles, profile_path

COLUMNS = (("class", "Class code", 280), ("id", "ID (start)", 150), ("trained", "Trained", 80), ("windows", "Windows", 80),
           ("stability", "Stability", 80), ("keys", "Keys", 60), ("updated", "Updated", 150), ("size", "Size", 70))


def read_info(path):
    """Quick facts about one .fpr file, or None if it cannot be read."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            d = json.load(fh)
        stab = d.get("stability")
        return {"class": (d.get("class") or {}).get("code", "?"), "id": d.get("id") or "",
                "seconds": float(d.get("seconds_recorded") or 0), "windows": len(d.get("windows") or []),
                "stability": float(stab) if isinstance(stab, (int, float)) else None,
                "keys": len(d.get("keys") or {}), "updated": str(d.get("updated") or ""),
                "size": os.path.getsize(path)}
    except Exception:
        return None


def sort_value(info, col):
    """Numeric columns sort as numbers, the rest as text."""
    if col == "trained":
        return info["seconds"]
    if col == "stability":
        return -1.0 if info["stability"] is None else info["stability"]
    if col in ("windows", "keys", "size"):
        return info[col]
    return str(info.get(col, "")).lower()


class LibraryTab:
    """Methods added to the main window (mixin). `self` is the window."""

    def _build_library(self):
        t = self.library_tab
        self._library_rows = []                            # [(name, info)]
        self._library_sort = ("#0", False)
        ttk.Label(t, text="Fingerprint library", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(t, text="All saved fingerprints. Click a column heading to sort.", foreground="#52647d").pack(anchor="w", pady=(4, 10))
        bar = ttk.Frame(t)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Button(bar, text="Refresh", command=self.refresh_library).pack(side="left")
        ttk.Button(bar, text="Open folder", command=self.open_library_folder).pack(side="left", padx=8)
        ttk.Button(bar, text="Copy full ID", command=self.copy_library_id).pack(side="left")
        self.library_count = ttk.Label(bar, text="")
        self.library_count.pack(side="right")

        self.library_tree = ttk.Treeview(t, columns=[c[0] for c in COLUMNS], show="tree headings", height=14)
        self.library_tree.heading("#0", text="NAME", command=lambda: self._library_sort_by("#0"))
        self.library_tree.column("#0", width=170, stretch=False)
        for col, title, width in COLUMNS:
            self.library_tree.heading(col, text=title, command=lambda c=col: self._library_sort_by(c))
            self.library_tree.column(col, width=width, anchor="w" if col in ("class", "id") else "center")
        sb = ttk.Scrollbar(t, orient="vertical", command=self.library_tree.yview)
        self.library_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.library_tree.pack(fill="both", expand=True)
        self.library_tree.bind("<<TreeviewSelect>>", lambda e: self._library_selected())
        self.library_detail = ttk.Label(t, text="", wraplength=900, justify="left", foreground="#52647d")
        self.library_detail.pack(anchor="w", pady=(8, 0), fill="x")

    def refresh_library(self):
        self._library_rows = []
        for name in list_profiles():
            info = read_info(profile_path(name))
            if info:
                self._library_rows.append((name, info))
        self._library_fill()

    def _library_fill(self):
        col, reverse = self._library_sort
        key = (lambda r: r[0].lower()) if col == "#0" else (lambda r: sort_value(r[1], col))
        self.library_tree.delete(*self.library_tree.get_children())
        for name, i in sorted(self._library_rows, key=key, reverse=reverse):
            self.library_tree.insert("", "end", iid=name, text=name, values=(
                i["class"], (i["id"][:16] + "...") if len(i["id"]) > 16 else i["id"] or "-", f"{i['seconds'] / 60:.1f} min",
                i["windows"], "-" if i["stability"] is None else f"{i['stability']:.2f}", i["keys"],
                i["updated"].replace("T", " ") or "-", f"{i['size'] / 1024:.0f} KB"))
        self.library_count.config(text=f"{len(self._library_rows)} fingerprint(s)")
        self.library_detail.config(text="")

    def _library_sort_by(self, col):
        prev, reverse = self._library_sort
        self._library_sort = (col, (not reverse) if prev == col else False)
        self._library_fill()

    def _library_current(self):
        sel = self.library_tree.selection()
        if not sel:
            return None, None
        return sel[0], dict(self._library_rows).get(sel[0])

    def _library_selected(self):
        name, info = self._library_current()
        if info:
            self.library_detail.config(text=f"{name}\nClass: {info['class']}\nID: {info['id'] or '-'}")

    def copy_library_id(self):
        name, info = self._library_current()
        if not info or not info["id"]:
            messagebox.showinfo("Copy ID", "Select a fingerprint that has an ID first.", parent=self)
            return
        self.copy_text(info["id"])

    def open_library_folder(self):
        os.makedirs(PROFILE_DIR, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(PROFILE_DIR)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", PROFILE_DIR])
        except Exception as e:
            messagebox.showerror("Open folder", str(e), parent=self)


def register(ctx):
    ui = ctx.get("ui")
    ui.add_mixin(LibraryTab)
    ui.add_tab("  Library  ", "library_tab", "_build_library", order=25, lock=False)
    ctx.hooks.on("ui.profiles_refreshed", lambda app: app.refresh_library())
