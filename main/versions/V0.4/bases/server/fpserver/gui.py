"""The viewer window: Players, Bans and Analysis tabs against the fingerprint server. Standard library only."""
import csv
import json
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import config
from .api import ApiError, ServerApi, filter_results, summarize

REMINDER = "Flags are leads for HUMAN REVIEW - similar players, shared PCs and siblings can look alike. Never auto-ban on a fingerprint match alone."


class Viewer(tk.Tk):
    def __init__(self, cfg):
        super().__init__()
        self.cfg, self.api = cfg, ServerApi(cfg)
        self.title("Play Fingerprint - Anti-cheat viewer")
        self.geometry("1020x700")
        self.minsize(820, 560)
        self.q = queue.Queue()
        self.bans, self.results, self.connected = {}, [], False

        top = ttk.Frame(self, padding=(12, 10, 12, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Server URL").pack(side="left")
        self.url = tk.StringVar(value=cfg.get("url", ""))
        ttk.Entry(top, textvariable=self.url, width=44).pack(side="left", padx=(6, 12))
        ttk.Label(top, text="API key").pack(side="left")
        self.key = tk.StringVar(value=cfg.get("key", ""))
        ttk.Entry(top, textvariable=self.key, show="*", width=24).pack(side="left", padx=(6, 12))
        ttk.Button(top, text="Connect", command=self.connect).pack(side="left")
        ttk.Button(top, text="Save settings", command=self.save).pack(side="left", padx=6)
        self.status = tk.Label(self, text="Not connected", fg="#888", anchor="w")
        self.status.pack(fill="x", padx=14)
        tk.Label(self, text=REMINDER, fg="#a60", anchor="w", wraplength=980, justify="left").pack(fill="x", padx=14, pady=(2, 6))

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._players_tab()
        self._bans_tab()
        self._analysis_tab()
        self.after(100, self._pump)

    # ---- plumbing -----------------------------------------------------------------------------
    def say(self, text, fg="#555"):
        self.status.config(text=text, fg=fg)

    def bg(self, fn, ok, err=None):
        """Run fn in a thread; ok(result) / err(exception) then run on the UI thread."""
        def work():
            try:
                self.q.put((ok, (fn(),)))
            except Exception as e:
                self.q.put((err or self._fail, (e,)))
        threading.Thread(target=work, daemon=True).start()

    def _pump(self):
        try:
            while True:
                fn, args = self.q.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        self.after(100, self._pump)

    def _fail(self, e):
        self.say(f"Error: {e}", "#d11")

    def _apply(self):
        self.cfg["url"], self.cfg["key"] = self.url.get().strip().rstrip("/"), self.key.get().strip()

    def save(self):
        self._apply()
        try:
            self.say(f"Saved to {config.save(self.cfg)}", "#1a8a3a")
        except OSError as e:
            self._fail(e)

    def connect(self):
        self._apply()
        self.say("Connecting (a sleeping Render instance can take up to a minute)...", "#d77a00")

        def work():
            if not self.api.wake(progress=lambda m: self.q.put((self.say, (m, "#d77a00")))):
                raise RuntimeError("could not reach the server")
            return self.api.status()
        self.bg(work, self._connected)

    def _connected(self, s):
        self.connected = True
        self.say(f"Connected - {s.get('fingerprints')} fingerprint(s), {s.get('bans')} ban(s) on the server.", "#1a8a3a")
        self.refresh_bans()
        self.refresh_players()

    def _need(self):
        if not self.connected:
            messagebox.showinfo("Not connected", "Press Connect first.")
        return self.connected

    # ---- players --------------------------------------------------------------------------------
    def _players_tab(self):
        t = ttk.Frame(self.nb, padding=10)
        self.nb.add(t, text="  Players  ")
        bar = ttk.Frame(t)
        bar.pack(fill="x")
        ttk.Button(bar, text="Refresh", command=self.refresh_players).pack(side="left")
        ttk.Button(bar, text="Ban selected...", command=self.ban_selected).pack(side="left", padx=6)
        ttk.Button(bar, text="Download selected...", command=self.download_selected).pack(side="left")
        self.p_tree = ttk.Treeview(t, columns=("updated", "size", "status"), height=12, selectmode="browse")
        for col, text, w in (("#0", "Player", 240), ("updated", "Last updated", 170), ("size", "Size", 80), ("status", "Status", 90)):
            self.p_tree.heading(col, text=text)
            self.p_tree.column(col, width=w, stretch=(col == "#0"))
        self.p_tree.tag_configure("banned", foreground="#c00")
        self.p_tree.pack(fill="both", expand=True, pady=(8, 6))
        self.p_tree.bind("<<TreeviewSelect>>", lambda e: self.show_player())
        self.p_detail = tk.Label(t, text="Select a player to see their fingerprint header.", anchor="w", justify="left",
                                 fg="#335", font=("TkFixedFont", 9))
        self.p_detail.pack(fill="x")

    def refresh_players(self):
        if self._need():
            self.bg(self.api.fingerprints, self._players_loaded)

    def _players_loaded(self, rows):
        self.p_tree.delete(*self.p_tree.get_children())
        for r in sorted(rows, key=lambda r: str(r.get("name"))):
            n = str(r.get("name"))
            size = r.get("size")
            self.p_tree.insert("", "end", iid=n, text=n, tags=("banned",) if n in self.bans else (),
                               values=(r.get("updated", ""), f"{size / 1024:.0f} KB" if isinstance(size, (int, float)) else "",
                                       "BANNED" if n in self.bans else ""))

    def _selected_player(self):
        sel = self.p_tree.selection()
        return sel[0] if sel else None

    def show_player(self):
        n = self._selected_player()
        if not n:
            return
        self.p_detail.config(text=f"Loading {n}...")

        def ok(s):
            if self._selected_player() == n:
                self.p_detail.config(text=(
                    f"{n}   class {s['class']}   {s['playstyle'] or ''}\nID {s['id']}\n"
                    f"{s['windows']} windows, {s['minutes']} min recorded, stability {s['stability']}, "
                    f"per-key data {'yes' if s['keys_tracked'] else 'no'}\ncreated {s['created']}   updated {s['updated']}"))
        self.bg(lambda: summarize(self.api.fingerprint_raw(n)), ok,
                lambda e: self.p_detail.config(text=f"Could not read {n}: {e}"))

    def ban_selected(self):
        n = self._selected_player()
        if n and self._need():
            self.add_ban(n)

    def download_selected(self):
        n = self._selected_player()
        if not n:
            return
        path = filedialog.asksaveasfilename(initialfile=f"{n}.fpr", defaultextension=".fpr")
        if path:
            def save(raw):
                with open(path, "wb") as fh:
                    fh.write(raw)
                self.say(f"Saved {path}", "#1a8a3a")
            self.bg(lambda: self.api.fingerprint_raw(n), save)

    # ---- bans -----------------------------------------------------------------------------------
    def _bans_tab(self):
        t = ttk.Frame(self.nb, padding=10)
        self.nb.add(t, text="  Bans  ")
        bar = ttk.Frame(t)
        bar.pack(fill="x")
        ttk.Button(bar, text="Refresh", command=self.refresh_bans).pack(side="left")
        ttk.Button(bar, text="Add ban...", command=lambda: self.add_ban(None)).pack(side="left", padx=6)
        ttk.Button(bar, text="Unban selected", command=self.unban_selected).pack(side="left")
        self.b_tree = ttk.Treeview(t, columns=("date", "reason"), height=12, selectmode="browse")
        for col, text, w in (("#0", "Banned account", 240), ("date", "Date", 110), ("reason", "Reason", 480)):
            self.b_tree.heading(col, text=text)
            self.b_tree.column(col, width=w, stretch=(col == "reason"))
        self.b_tree.pack(fill="both", expand=True, pady=(8, 0))

    def refresh_bans(self):
        if self._need():
            self.bg(self.api.banlist, self._bans_loaded)

    def _bans_loaded(self, bans):
        self.bans = bans
        self.b_tree.delete(*self.b_tree.get_children())
        for n, e in sorted(bans.items()):
            self.b_tree.insert("", "end", iid=n, text=n, values=(e.get("date", ""), e.get("reason", "")))
        for iid in self.p_tree.get_children():                 # keep the Players tab's BANNED marks in step
            vals = list(self.p_tree.item(iid, "values"))
            vals[2] = "BANNED" if iid in bans else ""
            self.p_tree.item(iid, values=vals, tags=("banned",) if iid in bans else ())

    def add_ban(self, name):
        name = name or simpledialog.askstring("Ban", "Account name to ban:", parent=self)
        if not name:
            return
        reason = simpledialog.askstring("Ban reason", f"Reason for banning '{name}':", parent=self)
        if reason is None:
            return
        self.bg(lambda: self.api.ban(name, reason), lambda _e: (self.say(f"Banned '{name}'.", "#1a8a3a"), self.refresh_bans()))

    def unban_selected(self):
        sel = self.b_tree.selection()
        if sel and messagebox.askyesno("Unban", f"Remove '{sel[0]}' from the ban list?"):
            n = sel[0]
            self.bg(lambda: self.api.unban(n), lambda _r: (self.say(f"Unbanned '{n}'.", "#1a8a3a"), self.refresh_bans()))

    # ---- analysis -------------------------------------------------------------------------------
    def _analysis_tab(self):
        t = ttk.Frame(self.nb, padding=10)
        self.nb.add(t, text="  Analysis  ")
        bar = ttk.Frame(t)
        bar.pack(fill="x")
        self.run_btn = ttk.Button(bar, text="Run server ban analysis", command=self.run_analysis)
        self.run_btn.pack(side="left")
        ttk.Label(bar, text="  Show").pack(side="left")
        self.level = tk.StringVar(value="review")
        lv = ttk.Combobox(bar, textvariable=self.level, values=("review", "high", "all"), state="readonly", width=8)
        lv.pack(side="left", padx=4)
        lv.bind("<<ComboboxSelected>>", lambda e: self.show_results())
        ttk.Button(bar, text="Export...", command=self.export).pack(side="right")
        ttk.Button(bar, text="Ban selected candidate...", command=self.ban_candidate).pack(side="right", padx=6)
        self.a_summary = ttk.Label(t, text="Run the analysis to compare every account against the banned ones.")
        self.a_summary.pack(anchor="w", pady=(8, 4))
        self.a_tree = ttk.Treeview(t, columns=("flag", "banned", "distance", "shared"), height=12, selectmode="browse")
        for col, text, w in (("#0", "Candidate", 220), ("flag", "Flag", 80), ("banned", "Similar to banned", 220),
                             ("distance", "Distance", 90), ("shared", "Shared features", 120)):
            self.a_tree.heading(col, text=text)
            self.a_tree.column(col, width=w, stretch=(col == "#0"))
        self.a_tree.tag_configure("HIGH", foreground="#c00")
        self.a_tree.tag_configure("REVIEW", foreground="#d77a00")
        self.a_tree.pack(fill="both", expand=True)

    def run_analysis(self):
        if not self._need():
            return
        self.run_btn.config(state="disabled")
        self.a_summary.config(text="Running the server's analysis...")
        self.bg(self.api.analysis, self._analysis_done, lambda e: (self.run_btn.config(state="normal"), self._fail(e)))

    def _analysis_done(self, results):
        self.run_btn.config(state="normal")
        self.results = results
        self.show_results()

    def show_results(self):
        rows = filter_results(self.results, self.level.get())
        self.a_tree.delete(*self.a_tree.get_children())
        for i, r in enumerate(rows):
            self.a_tree.insert("", "end", iid=str(i), text=str(r.get("candidate")), tags=(r.get("flag", ""),),
                               values=(r.get("flag"), r.get("banned"), r.get("distance"), r.get("shared_features")))
        self._shown = rows
        flagged = sum(1 for r in self.results if r.get("flag") in ("REVIEW", "HIGH"))
        self.a_summary.config(text=f"{len(self.results)} comparison(s); {flagged} need human review. Showing {len(rows)}.")

    def ban_candidate(self):
        sel = self.a_tree.selection()
        if not sel:
            return
        r = self._shown[int(sel[0])]
        if messagebox.askyesno("Ban after review?", f"Ban '{r.get('candidate')}'?\n\nOnly do this after a human has reviewed the evidence - "
                               "a similar fingerprint is a lead, not proof."):
            self.add_ban(str(r.get("candidate")))

    def export(self):
        rows = getattr(self, "_shown", [])
        if not rows:
            messagebox.showinfo("Nothing to export", "Run the analysis first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv"), ("JSON", "*.json")])
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            if path.lower().endswith(".json"):
                json.dump(rows, fh, indent=1)
            else:
                w = csv.writer(fh)
                w.writerow(["flag", "candidate", "banned", "distance", "shared_features"])
                w.writerows([[r.get("flag"), r.get("candidate"), r.get("banned"), r.get("distance"), r.get("shared_features")] for r in rows])
        self.say(f"Exported {len(rows)} row(s) to {path}", "#1a8a3a")


def run_gui(args):
    cfg = config.load(getattr(args, "config", None))
    if getattr(args, "url", None):
        cfg["url"] = args.url
    if getattr(args, "key", None):
        cfg["key"] = args.key
    app = Viewer(cfg)
    if cfg.get("url") and cfg.get("key"):
        app.after(300, app.connect)               # settings already known: connect straight away
    app.mainloop()
    return 0
