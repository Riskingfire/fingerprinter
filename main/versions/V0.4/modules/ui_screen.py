"""Screen (anti-cheat) tab: ban list + ban-evasion screening. Bans / unbans are announced on the
"banlist.changed" hook, so server_sync (if loaded) mirrors them - this module does not know about it.
"""
NAME = "ui_screen"
DESCRIPTION = "Screen tab: ban list and ban-evasion screening"
REQUIRES = ['ui_tk']
ORDER = 20

import json
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from fp.analysis import feature_weights, screen_player
from fp.keybinds import key_idf
from fp.profile import Profile
from fp.store import list_profiles, load_banlist, profile_path, read_summary, save_banlist


class ScreenTab:
    """Methods added to the main window (mixin). `self` is the window."""

    def _build_screen(self):
        t = self.screen_tab
        self.screen_report = []
        ttk.Label(t, text="Ban list & ban-evasion screening",
                  font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        ttk.Label(t, text="Flags are leads for human review - never auto-ban on a fingerprint match alone.",
                  foreground="#a60").pack(anchor="w", pady=(2, 8))

        self.ban_tree = ttk.Treeview(t, columns=("code", "status", "reason"), height=6,
                                     selectmode="extended")
        for col, text, w in (("#0", "Account", 150), ("code", "Class", 70),
                             ("status", "Status", 70), ("reason", "Ban reason", 230)):
            self.ban_tree.heading(col, text=text)
            self.ban_tree.column(col, width=w, stretch=(col == "reason"))
        self.ban_tree.tag_configure("banned", foreground="#c00")
        self.ban_tree.pack(fill="x")

        brow = ttk.Frame(t)
        brow.pack(fill="x", pady=6)
        ttk.Button(brow, text="Ban selected...", command=self.ban_selected).pack(side="left")
        ttk.Button(brow, text="Unban selected", command=self.unban_selected).pack(side="left", padx=6)

        srow = ttk.Frame(t)
        srow.pack(fill="x", pady=(4, 6))
        ttk.Label(srow, text="Strictness").pack(side="left")
        ttk.Label(srow, text="strict").pack(side="left", padx=(10, 2))
        ttk.Scale(srow, from_=1.2, to=3.0, variable=self.strict, orient="horizontal").pack(
            side="left", fill="x", expand=True)
        ttk.Label(srow, text="lenient").pack(side="left", padx=(2, 0))

        arow = ttk.Frame(t)
        arow.pack(fill="x", pady=4)
        ttk.Button(arow, text="Screen all clean accounts", command=self.run_screen).pack(side="left")
        ttk.Button(arow, text="Screen selected",
                   command=lambda: self.run_screen(only_selected=True)).pack(side="left", padx=6)
        ttk.Button(arow, text="Export report...", command=self.export_report).pack(side="right")

        self.screen_tree = ttk.Treeview(t, columns=("banned", "sim", "pop", "kb", "flag"), height=8)
        for col, text, w in (("#0", "Account", 130), ("banned", "Closest banned", 130),
                             ("sim", "Similarity", 80), ("pop", "More alike than", 100),
                             ("kb", "Keybinds", 70), ("flag", "Flag", 60)):
            self.screen_tree.heading(col, text=text)
            self.screen_tree.column(col, width=w, stretch=False)
        self.screen_tree.tag_configure("high", foreground="#c00")
        self.screen_tree.tag_configure("review", foreground="#d77a00")
        self.screen_tree.pack(fill="both", expand=True, pady=(6, 0))
        self.screen_status = ttk.Label(t, text="", foreground="#666", wraplength=600, justify="left")
        self.screen_status.pack(anchor="w", pady=(8, 0))

    def refresh_ban_tree(self):
        ban = load_banlist()
        self.ban_tree.delete(*self.ban_tree.get_children())
        for n in list_profiles():
            code, _ = read_summary(profile_path(n))
            b = ban.get(n)
            self.ban_tree.insert("", "end", iid=n, text=n,
                                 values=(code, "BANNED" if b else "clean", (b or {}).get("reason", "")),
                                 tags=("banned",) if b else ())

    def ban_selected(self):
        sel = self.ban_tree.selection()
        if not sel:
            messagebox.showinfo("Select accounts", "Select one or more accounts in the list first.")
            return
        reason = simpledialog.askstring("Ban reason", "Reason / ticket reference:", parent=self)
        if reason is None:
            return
        ban = load_banlist()
        for n in sel:
            ban[n] = {"reason": reason, "date": datetime.now().isoformat(timespec="seconds")}
        save_banlist(ban)
        self.hooks.emit("banlist.changed", added={n: ban[n] for n in sel}, removed=[])   # e.g. server_sync
        self.refresh_profiles()

    def unban_selected(self):
        sel = self.ban_tree.selection()
        if not sel:
            return
        ban = load_banlist()
        for n in sel:
            ban.pop(n, None)
        save_banlist(ban)
        self.hooks.emit("banlist.changed", added={}, removed=list(sel))                  # e.g. server_sync
        self.refresh_profiles()

    def run_screen(self, only_selected=False):
        ban = load_banlist()
        profiles = {}
        for n in list_profiles():
            try:
                p = Profile.load(profile_path(n))
                p.name = n
                if len(p.windows) >= 5:
                    profiles[n] = p
            except Exception:
                pass
        banned = {n: p for n, p in profiles.items() if n in ban}
        clean = {n: p for n, p in profiles.items() if n not in ban}
        if not banned:
            messagebox.showinfo("No banned fingerprints",
                                "Ban at least one account (with 5+ windows of data) first.")
            return
        sel = set(self.ban_tree.selection())
        cands = [p for n, p in clean.items() if not only_selected or n in sel]
        if not cands:
            messagebox.showinfo("Nothing to screen", "No clean accounts to screen"
                                + (" - select some first." if only_selected else "."))
            return
        thr = float(self.strict.get())
        w = feature_weights(list(profiles.values()))
        idf = key_idf([p.keys for p in profiles.values() if p.keys])
        self.screen_tree.delete(*self.screen_tree.get_children())
        self.screen_report = []
        high = review = 0
        for c in cands:
            rows = screen_player(c, banned, [p for n, p in clean.items() if n != c.name], thr, w, idf)
            if not rows:
                continue
            top = rows[0]
            high += top["flag"] == "HIGH"
            review += top["flag"] == "REVIEW"
            pop = "n/a" if top["population_rank"] is None else f"{top['population_rank']:.0f}% of players"
            kbs = "n/a" if top["key_sim"] is None else f"{top['key_sim'] * 100:.0f}%"
            self.screen_tree.insert("", "end", text=c.name,
                                    values=(top["banned"], f"{top['similarity']:.0f}%", pop, kbs, top["flag"]),
                                    tags=(top["flag"].lower(),))
            self.screen_report.append({"candidate": c.name, "flag": top["flag"], "matches": rows})
        pop_note = ""
        if len(clean) < 6:
            pop_note = "  Fewer than 5 other clean players - scores are uncalibrated, treat with extra caution."
        self.screen_status.config(
            text=f"Screened {len(self.screen_report)} account(s): {high} HIGH, {review} REVIEW.{pop_note}"
                 + ("  Feature weighting on." if w is not None
                    else "  Feature weighting off (needs 3+ fingerprints)."))

    def export_report(self):
        if not self.screen_report:
            messagebox.showinfo("No report", "Run a screen first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".json", initialfile="screening_report.json",
                                            filetypes=[("JSON", "*.json")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"generated": datetime.now().isoformat(timespec="seconds"),
                       "strictness": float(self.strict.get()),
                       "note": "Fingerprint matches are leads for human review, not proof of identity.",
                       "results": self.screen_report}, fh, indent=1)
        messagebox.showinfo("Exported", path)


def register(ctx):
    ui = ctx.get("ui")
    ui.add_mixin(ScreenTab)
    ui.add_tab("  Screen (anti-cheat)  ", "screen_tab", "_build_screen", order=20)
    ctx.hooks.on("ui.profiles_refreshed", lambda app: app.refresh_ban_tree())
