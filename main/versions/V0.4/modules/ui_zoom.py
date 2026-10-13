"""Zoom button: make the whole window's text and tables bigger (100 / 115 / 130 / 150 %), remembered between runs."""
NAME = "ui_zoom"
DESCRIPTION = "Zoom... button: cycle the UI text size 100 / 115 / 130 / 150 % (remembered)"
REQUIRES = ["ui_tk"]
ORDER = 85

import json
import os
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from fp.settings import APP_DATA_DIR

ZOOM_FILE = os.path.join(APP_DATA_DIR, "zoom.json")
LEVELS = (1.0, 1.15, 1.3, 1.5)
BASE_SIZE = 10          # ui_tk uses Segoe UI 10
BASE_ROW = 28           # ui_tk's Treeview row height
NAMED_FONTS = ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont", "TkFixedFont")


def load_level():
    try:
        with open(ZOOM_FILE, encoding="utf-8") as fh:
            value = float(json.load(fh).get("zoom", 1.0))
        return value if value in LEVELS else 1.0
    except (OSError, ValueError, AttributeError, TypeError):
        return 1.0


def save_level(level):
    try:
        os.makedirs(os.path.dirname(ZOOM_FILE), exist_ok=True)
        with open(ZOOM_FILE, "w", encoding="utf-8") as fh:
            json.dump({"zoom": level}, fh)
    except OSError:
        pass


def next_level(level):
    return LEVELS[(LEVELS.index(level) + 1) % len(LEVELS)] if level in LEVELS else 1.0


def apply_zoom(app, level):
    size = max(8, round(BASE_SIZE * level))
    style = ttk.Style(app)
    style.configure(".", font=("Segoe UI", size))
    style.configure("TNotebook.Tab", font=("Segoe UI", size, "bold"))
    style.configure("Treeview", rowheight=round(BASE_ROW * level), font=("Segoe UI", size))
    style.configure("Treeview.Heading", font=("Segoe UI", max(8, round(9 * level)), "bold"))
    for name in NAMED_FONTS:                                   # plain tk widgets (Text, Listbox ...)
        try:
            tkfont.nametofont(name).configure(size=size)
        except tk.TclError:
            pass
    label = getattr(app, "zoom_label", None)
    if label is not None:
        label.config(text=f"Zoom {round(level * 100)}%")


class ZoomMixin:
    """Methods added to the main window (mixin). `self` is the window."""

    def cycle_zoom(self):
        self.zoom_level = next_level(getattr(self, "zoom_level", 1.0))
        apply_zoom(self, self.zoom_level)
        save_level(self.zoom_level)


def register(ctx):
    ui = ctx.get("ui")
    ui.add_mixin(ZoomMixin)
    ui.add_header_button("Zoom", "cycle_zoom", order=40)

    def on_built(app):
        app.zoom_label = ttk.Label(app.footer_row, text="", foreground="#666")
        app.zoom_label.pack(side="left", padx=8)
        app.zoom_level = load_level()
        apply_zoom(app, app.zoom_level)

    ctx.hooks.on("ui.built", on_built)
