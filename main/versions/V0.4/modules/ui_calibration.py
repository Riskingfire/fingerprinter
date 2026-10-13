"""Tk window for the similarity calibration module.

Only loaded when the UI package is present: the loader skips this file with the reason
"needs ui_tk" if ``ui_tk`` is not loaded, and "needs similarity_calibration" if the core
module is missing. All real work is done by the core module's "calibration" service;
this file is just a window with a button. Calibration runs ONLY when the button is clicked.

Services provided : "calibration_ui_open"  -> open_window(parent=None)
Commands added    : python fingerprinter.py calibrate-ui   (standalone window)
"""

NAME = "similarity_calibration_ui"
DESCRIPTION = "Window with a 'Recalibrate similarity' button (needs the ui_tk package)"
REQUIRES = ["ui_tk", "similarity_calibration"]
OPTIONAL = []
ORDER = 160

import queue
import threading

try:
    from fp.plugins import ModuleUnavailable
except Exception:  # pragma: no cover - running outside the app
    class ModuleUnavailable(Exception):
        pass

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError as _e:
    raise ModuleUnavailable("tkinter is not available") from _e


class CalibrationPanel(ttk.Frame):
    COLUMNS = (("a", "Profile A", 160), ("b", "Profile B", 160), ("pct", "Percentile", 80),
               ("z", "z", 60), ("score", "Score", 60), ("flag", "Review", 70))

    def __init__(self, parent, service):
        super().__init__(parent, padding=8)
        self.service = service
        self._q = queue.Queue()
        self._busy = False

        top = ttk.Frame(self)
        top.pack(fill="x")
        self.button = ttk.Button(top, text="Recalibrate similarity", command=self._on_click)
        self.button.pack(side="left")
        self.status = ttk.Label(top, text="Not run yet. Click to calibrate against all profiles.")
        self.status.pack(side="left", padx=10)

        self.summary = tk.Text(self, height=9, wrap="word", state="disabled")
        self.summary.pack(fill="x", pady=(8, 4))

        ttk.Label(self, text="Closest pairs (flagged pairs are for human review, not proof)"
                  ).pack(anchor="w")
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=[c[0] for c in self.COLUMNS],
                                 show="headings", height=12)
        for key, title, width in self.COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="w" if key in ("a", "b") else "center")
        self.tree.tag_configure("flagged", background="#ffe3b3", foreground="#000000")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    # -- button -> background thread -> poll queue, so the window never freezes --------
    def _on_click(self):
        if self._busy:
            return
        self._busy = True
        self.button.state(["disabled"])
        self.status.config(text="Calibrating...")
        threading.Thread(target=self._worker, daemon=True).start()
        self.after(100, self._poll)

    def _worker(self):
        try:
            self._q.put(("ok", self.service.recalibrate()))
        except Exception as e:
            self._q.put(("err", e))

    def _poll(self):
        try:
            kind, payload = self._q.get_nowait()
        except queue.Empty:
            self.after(100, self._poll)
            return
        self._busy = False
        self.button.state(["!disabled"])
        if kind == "err":
            self.status.config(text="Calibration failed.")
            messagebox.showerror("Calibration failed", str(payload), parent=self)
        else:
            self._show(payload)

    def _show(self, report):
        flagged = len(report.flagged_pairs())
        self.status.config(text=("Done. " + (f"{flagged} pair(s) flagged for review."
                                              if report.informative
                                              else "Scores are NOT informative - see warnings.")))
        self.summary.config(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", report.summary())
        self.summary.config(state="disabled")
        self.tree.delete(*self.tree.get_children())
        for p in report.pairs:
            self.tree.insert("", "end", tags=("flagged",) if p.flagged else (), values=(
                p.a, p.b, f"{p.percentile:.2f}", f"{p.z:.2f}", f"{p.display_score:.1f}",
                "REVIEW" if p.flagged else ""))


_WINDOW = None


def open_window(service, parent=None):
    """Open (or raise) the calibration window. With no parent it attaches to the app's
    existing Tk root if there is one, otherwise it creates its own (standalone)."""
    global _WINDOW
    try:
        if _WINDOW is not None and _WINDOW.winfo_exists():
            _WINDOW.lift()
            return _WINDOW
    except tk.TclError:
        _WINDOW = None
    parent = parent or getattr(tk, "_default_root", None)
    standalone = parent is None
    win = tk.Tk() if standalone else tk.Toplevel(parent)
    win.title("Similarity calibration")
    win.geometry("640x520")
    CalibrationPanel(win, service).pack(fill="both", expand=True)
    _WINDOW = win
    if standalone:
        win.mainloop()
    return win


# ---- hooking into the ui_tk window ---------------------------------------------------
# I could not see how ui_tk exposes its window/menus, so this probes a few likely service
# and method names. If none match, the standalone `calibrate-ui` command still works.
# Replace this with the real ui_tk call once you know it (one place, one function).
_HOST_SERVICES = ("ui_tk", "tk_ui", "ui", "tk")
_HOST_METHODS = ("add_menu_item", "add_tool", "add_tab", "add_panel", "add_button")


def _attach_to_host(ctx, label, callback):
    for sname in _HOST_SERVICES:
        host = ctx.get(sname)
        if host is None:
            continue
        for mname in _HOST_METHODS:
            fn = getattr(host, mname, None)
            if callable(fn):
                try:
                    fn(label, callback)
                    return f"{sname}.{mname}"
                except Exception:
                    continue
    return None


def register(ctx):
    service = ctx.get("calibration")
    if service is None:
        raise ModuleUnavailable("calibration service not available")

    def _open(parent=None):
        return open_window(service, parent)

    ctx.provide("calibration_ui_open", _open)

    def _run_cli(ctx_, args):
        _open()
        return 0

    ctx.add_command("calibrate-ui", _run_cli,
                    help="open the similarity calibration window on its own")
    _attach_to_host(ctx, "Similarity calibration", _open)
