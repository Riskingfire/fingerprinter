"""Resources tab: CPU and RAM of this process (needs psutil for numbers; shows a hint without it)."""
NAME = "ui_resources"
DESCRIPTION = "Resources tab: CPU / RAM of this process"
REQUIRES = ['ui_tk']
ORDER = 50

import os
import sys

PSUTIL_IMPORT_ERROR = None
try:
    import psutil  # optional: live per-process CPU/RAM metrics
except Exception as _psutil_error:
    psutil = None
    PSUTIL_IMPORT_ERROR = f"{type(_psutil_error).__name__}: {_psutil_error}"

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog


class ResourcesTab:
    """Methods added to the main window (mixin). `self` is the window."""

    def _build_resource_monitor(self):
        """Show resource usage for this Play Fingerprint process, not the whole PC."""
        t = self.resource_tab
        ttk.Label(t, text="Application resource monitor", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(t, text="Measures Play Fingerprint itself (CPU and RAM), not total system usage.",
                  foreground="#52647d", wraplength=850).pack(anchor="w", pady=(4, 14))
        self.resource_values = {}
        self.resource_bars = {}
        for key, title, maximum in (("cpu", "CPU usage", 100), ("ram", "RAM used", 100)):
            card = ttk.LabelFrame(t, text=title, padding=12)
            card.pack(fill="x", pady=7)
            value = ttk.Label(card, text="Starting monitor…", font=("Segoe UI", 14, "bold"))
            value.pack(anchor="w")
            bar = ttk.Progressbar(card, maximum=maximum, mode="determinate")
            bar.pack(fill="x", pady=(8, 3))
            detail = ttk.Label(card, text="")
            detail.pack(anchor="w")
            self.resource_values[key] = (value, detail)
            self.resource_bars[key] = bar
        self.resource_note = ttk.Label(t, text="", foreground="#7a4b00", wraplength=850)
        self.resource_note.pack(anchor="w", pady=(12, 0))
        if psutil is None:
            self.resource_note.config(text=(
                "psutil could not be imported by this Python interpreter. "
                f"Interpreter: {sys.executable}. Install into this exact interpreter with: "
                f'"{sys.executable}" -m pip install --upgrade psutil. '
                f"Details: {PSUTIL_IMPORT_ERROR or 'unknown import error'}"
            ))

    def update_resource_monitor(self):
        """Refresh process-only CPU and resident-memory figures without blocking the UI."""
        try:
            if self._process is None:
                for key in ("cpu", "ram"):
                    value, detail = self.resource_values[key]
                    value.config(text="Unavailable")
                    if psutil is None:
                        detail.config(text=f"Import failed: {self._resource_init_error or PSUTIL_IMPORT_ERROR or 'unknown error'}")
                    else:
                        detail.config(text=f"Process initialization failed: {self._resource_init_error or 'unknown error'}")
                    self.resource_bars[key]["value"] = 0
            else:
                cpu = max(0.0, self._process.cpu_percent(interval=None))
                mem = self._process.memory_info().rss
                total = psutil.virtual_memory().total
                ram_mb = mem / (1024 * 1024)
                ram_pct = (mem / total * 100) if total else 0.0
                cpu_value, cpu_detail = self.resource_values["cpu"]
                cpu_value.config(text=f"{cpu:.1f}% CPU")
                cpu_detail.config(text="Process CPU usage; may exceed 100% when using multiple cores.")
                self.resource_bars["cpu"]["value"] = min(100, cpu)
                ram_value, ram_detail = self.resource_values["ram"]
                ram_value.config(text=f"{ram_mb:.1f} MB RAM")
                ram_detail.config(text=f"{ram_pct:.3f}% of system RAM ({mem:,} bytes resident memory)")
                self.resource_bars["ram"]["value"] = min(100, ram_pct)
                if self.resource_note:
                    self.resource_note.config(text="Updates every second. CPU bar is capped at 100%; the numeric reading can exceed 100% on multi-core systems.")
        except Exception as e:
            self.resource_note.config(text=f"Resource monitor error: {e}")
        finally:
            if self.winfo_exists():
                self.after(1000, self.update_resource_monitor)


def _init_resources(app):
    app._resource_init_error = None
    if psutil is not None:
        try:
            app._process = psutil.Process(os.getpid())
            app._process.cpu_percent(interval=None)  # prime non-blocking CPU measurement
        except Exception as _resource_error:
            app._process = None
            app._resource_init_error = f"{type(_resource_error).__name__}: {_resource_error}"
    else:
        app._process = None
        app._resource_init_error = PSUTIL_IMPORT_ERROR or "psutil could not be imported"
    app.after(1000, app.update_resource_monitor)


def register(ctx):
    ui = ctx.get("ui")
    ui.add_mixin(ResourcesTab)
    ui.add_tab("  Resources  ", "resource_tab", "_build_resource_monitor", order=50, lock=False)
    ctx.hooks.on("ui.built", _init_resources)
