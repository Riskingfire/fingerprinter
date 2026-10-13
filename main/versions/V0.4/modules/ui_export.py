"""Export... header button: export a fingerprint (JSON / CSV), back up or restore all of them (needs export_tools)."""
NAME = "ui_export"
DESCRIPTION = "Export... button: export a fingerprint, back up / restore all fingerprints"
REQUIRES = ["ui_tk", "export_tools"]
ORDER = 80

import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from fp.store import list_profiles


class ExportDialog:
    """Methods added to the main window (mixin). `self` is the window."""

    def open_export_dialog(self):
        tools = self.ctx.get("export_tools")
        win = tk.Toplevel(self)
        win.title("Export / Backup")
        win.transient(self)
        win.resizable(False, False)
        body = ttk.Frame(win, padding=16)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="Export one fingerprint", font=("Segoe UI", 12, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        names = list_profiles()
        sel = tk.StringVar(value=names[0] if names else "")
        ttk.Combobox(body, textvariable=sel, values=names, state="readonly", width=30).grid(row=1, column=0, columnspan=3, sticky="we", pady=(6, 6))
        fmt = tk.StringVar(value="json")
        ttk.Radiobutton(body, text="JSON summary", variable=fmt, value="json").grid(row=2, column=0, sticky="w")
        ttk.Radiobutton(body, text="CSV (every window)", variable=fmt, value="csv").grid(row=2, column=1, sticky="w", padx=8)
        full = tk.BooleanVar(value=False)
        ttk.Checkbutton(body, text="JSON: include all windows", variable=full).grid(row=3, column=0, columnspan=3, sticky="w")

        def do_export():
            name = sel.get()
            if not name:
                messagebox.showinfo("Export", "There is no fingerprint to export yet.", parent=win)
                return
            ext = fmt.get()
            path = filedialog.asksaveasfilename(parent=win, defaultextension="." + ext, initialfile=f"{name}.{ext}",
                                                filetypes=[(ext.upper(), "*." + ext)])
            if not path:
                return
            try:
                tools.export_profile(name, path, ext, full.get())
                messagebox.showinfo("Export", f"Saved:\n{path}", parent=win)
            except Exception as e:
                messagebox.showerror("Export failed", str(e), parent=win)

        ttk.Button(body, text="Export...", command=do_export).grid(row=4, column=0, sticky="w", pady=(8, 14))

        ttk.Separator(body).grid(row=5, column=0, columnspan=3, sticky="we")
        ttk.Label(body, text="All fingerprints", font=("Segoe UI", 12, "bold")).grid(row=6, column=0, columnspan=3, sticky="w", pady=(12, 6))

        def do_backup():
            path = filedialog.asksaveasfilename(parent=win, defaultextension=".zip", initialfile=tools.default_backup_name(),
                                                filetypes=[("Zip backup", "*.zip")])
            if not path:
                return
            try:
                path, n = tools.make_backup(path)
                messagebox.showinfo("Backup", f"Backed up {n} file(s):\n{path}", parent=win)
            except Exception as e:
                messagebox.showerror("Backup failed", str(e), parent=win)

        def do_restore():
            path = filedialog.askopenfilename(parent=win, filetypes=[("Zip backup", "*.zip")])
            if not path:
                return
            over = messagebox.askyesno("Restore", "Overwrite fingerprints that already exist?\n\nYes = overwrite, No = keep existing ones.", parent=win)
            try:
                restored, skipped, rejected = tools.restore_backup(path, over)
            except Exception as e:
                messagebox.showerror("Restore failed", str(e), parent=win)
                return
            messagebox.showinfo("Restore", f"Restored {restored}, skipped {skipped} existing, ignored {rejected} other file(s).", parent=win)
            self.refresh_profiles()

        ttk.Button(body, text="Back up all...", command=do_backup).grid(row=7, column=0, sticky="w")
        ttk.Button(body, text="Restore backup...", command=do_restore).grid(row=7, column=1, sticky="w", padx=8)
        ttk.Button(body, text="Close", command=win.destroy).grid(row=8, column=2, sticky="e", pady=(14, 0))
        win.grab_set()


def register(ctx):
    ui = ctx.get("ui")
    ui.add_mixin(ExportDialog)
    ui.add_header_button("Export...", "open_export_dialog", order=30)
