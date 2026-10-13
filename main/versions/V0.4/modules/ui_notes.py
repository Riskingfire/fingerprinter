"""Notes tab: a free-text note per fingerprint (who it is, what account, anything), saved in notes.json."""
NAME = "ui_notes"
DESCRIPTION = "Notes tab: a free-text note for each fingerprint (also shown in the saved-details box)"
REQUIRES = ["ui_tk"]
ORDER = 35

import json
import os
import tkinter as tk
from tkinter import ttk

from fp.settings import APP_DATA_DIR
from fp.store import list_profiles

NOTES_FILE = os.path.join(APP_DATA_DIR, "notes.json")


class NoteStore:
    """Service \"notes\": ctx.get(\"notes\").get(name) / .set(name, text)."""

    def __init__(self, path=NOTES_FILE):
        self.path = path

    def all(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self, name):
        return self.all().get(name, "")

    def set(self, name, text):
        notes = self.all()
        text = (text or "").rstrip()
        if text:
            notes[name] = text
        else:
            notes.pop(name, None)                       # a blank note is no note
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(notes, fh, indent=1, ensure_ascii=False)
        os.replace(tmp, self.path)


class NotesTab:
    """Methods added to the main window (mixin). `self` is the window."""

    def _build_notes(self):
        t = self.notes_tab
        self.notes_store = self.ctx.get("notes")
        self._notes_loaded = None                        # fingerprint whose note is in the text box
        ttk.Label(t, text="Notes", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(t, text="Keep a note for each fingerprint. Saved automatically when you switch fingerprint or leave the box (Ctrl+S saves now).",
                  foreground="#52647d", wraplength=850, justify="left").pack(anchor="w", pady=(4, 10))
        row = ttk.Frame(t)
        row.pack(fill="x")
        ttk.Label(row, text="Fingerprint:").pack(side="left")
        self.notes_sel = tk.StringVar()
        self.notes_combo = ttk.Combobox(row, textvariable=self.notes_sel, state="readonly", width=32)
        self.notes_combo.pack(side="left", padx=8)
        self.notes_combo.bind("<<ComboboxSelected>>", lambda e: self._notes_switch())
        ttk.Button(row, text="Save", command=self.save_notes).pack(side="right")
        box = ttk.Frame(t)
        box.pack(fill="both", expand=True, pady=(10, 0))
        self.notes_text = tk.Text(box, wrap="word", undo=True, height=16, font=("Segoe UI", 10))
        sb = ttk.Scrollbar(box, orient="vertical", command=self.notes_text.yview)
        self.notes_text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.notes_text.pack(side="left", fill="both", expand=True)
        self.notes_text.bind("<FocusOut>", lambda e: self.save_notes(quiet=True))
        self.notes_text.bind("<Control-s>", lambda e: (self.save_notes(), "break")[1])
        self.notes_status = ttk.Label(t, text="", foreground="#52647d")
        self.notes_status.pack(anchor="w", pady=(6, 0))

    def refresh_notes_tab(self):
        names = list_profiles()
        self.notes_combo["values"] = names
        if self.notes_sel.get() not in names:
            self.save_notes(quiet=True)
            self.notes_sel.set(names[0] if names else "")
            self._notes_loaded = None
        if self._notes_loaded != self.notes_sel.get():
            self._notes_show()

    def _notes_show(self):
        name = self.notes_sel.get()
        self.notes_text.delete("1.0", "end")
        if name:
            self.notes_text.insert("1.0", self.notes_store.get(name))
            self.notes_text.edit_reset()
        self._notes_loaded = name or None
        self.notes_status.config(text="")

    def _notes_switch(self):
        self.save_notes(quiet=True)
        self._notes_show()

    def save_notes(self, quiet=False):
        name = self._notes_loaded
        if not name:
            return
        text = self.notes_text.get("1.0", "end-1c")
        if text.rstrip() == self.notes_store.get(name):
            return
        try:
            self.notes_store.set(name, text)
            if not quiet:
                self.notes_status.config(text=f"Saved note for '{name}'.")
        except OSError as e:
            self.notes_status.config(text=f"Could not save: {e}")


def register(ctx):
    store = ctx.provide("notes", NoteStore())
    ui = ctx.get("ui")
    ui.add_mixin(NotesTab)
    ui.add_tab("  Notes  ", "notes_tab", "_build_notes", order=35, lock=False)
    ctx.hooks.on("ui.profiles_refreshed", lambda app: app.refresh_notes_tab())

    def saved_details(p):                                 # shown in the "saved" dialog after training
        note = store.get(getattr(p, "name", ""))
        return [f"Note: {note.splitlines()[0][:120]}"] if note else []

    ctx.hooks.on("ui.saved_details", saved_details)

    def on_built(app):
        def flush():
            try:
                app.save_notes(quiet=True)
            except Exception:                              # window already gone
                pass
        ctx.hooks.on("shutdown", flush)

    ctx.hooks.on("ui.built", on_built)
