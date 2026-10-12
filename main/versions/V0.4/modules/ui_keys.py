"""Keys tab: per-key usage & keybinds, two accounts side by side."""
NAME = "ui_keys"
DESCRIPTION = "Keys tab: per-key usage and keybind comparison"
REQUIRES = ['ui_tk']
ORDER = 30

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from fp.keybinds import display_key, key_idf, key_rows, key_similarity, keybind_summary, read_keys
from fp.settings import NONE_LABEL
from fp.store import list_profiles, profile_path


class KeysTab:
    """Methods added to the main window (mixin). `self` is the window."""

    def _build_keys(self):
        t = self.keys_tab
        ttk.Label(t, text="Per-key usage & keybinds", font=("TkDefaultFont",  12, "bold")).pack(anchor="w")
        ttk.Label(t, text="Compare each recorded key for two accounts side by side, including press count, usage share, and average hold time. Train with 'Track individual keys' enabled.",
                  foreground="#666", wraplength=850, justify="left").pack(anchor="w", pady=(2, 8))
        row = ttk.Frame(t)
        row.pack(fill="x")
        ttk.Label(row, text="Account A:").pack(side="left")
        self.keys_a = tk.StringVar()
        self.keys_ca = ttk.Combobox(row, textvariable=self.keys_a, state="readonly", width=20)
        self.keys_ca.pack(side="left", padx=6)
        ttk.Label(row, text="Account B:").pack(side="left", padx=(10, 0))
        self.keys_b = tk.StringVar(value=NONE_LABEL)
        self.keys_cb = ttk.Combobox(row, textvariable=self.keys_b, state="readonly", width=20)
        self.keys_cb.pack(side="left", padx=6)
        for c in (self.keys_ca, self.keys_cb):
            c.bind("<<ComboboxSelected>>", lambda e: self.show_keys())

        cols = ("a_share", "a_cls", "a_n", "a_hold", "b_share", "b_cls", "b_n", "b_hold", "delta")
        self.keys_tree = ttk.Treeview(t, columns=cols, show="tree headings", height=16)
        self.keys_tree.heading("#0", text="KEY")
        self.keys_tree.column("#0", width=75, minwidth=60, stretch=False, anchor="center")
        definitions = [
            ("a_share", "A Share", 75), ("a_cls", "A Class", 85), ("a_n", "A Presses", 85), ("a_hold", "A Hold (ms)", 100),
            ("b_share", "B Share", 75), ("b_cls", "B Class", 85), ("b_n", "B Presses", 85), ("b_hold", "B Hold (ms)", 100),
            ("delta", "Share Δ", 80),
        ]
        for col, title, width in definitions:
            self.keys_tree.heading(col, text=title)
            self.keys_tree.column(col, width=width, minwidth=65, stretch=True, anchor="center")
        yscroll = ttk.Scrollbar(t, orient="vertical", command=self.keys_tree.yview)
        xscroll = ttk.Scrollbar(t, orient="horizontal", command=self.keys_tree.xview)
        self.keys_tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.keys_tree.pack(fill="both", expand=True, pady=(10, 0))
        xscroll.pack(fill="x")
        yscroll.place(relx=1.0, rely=0.22, relheight=0.68, anchor="ne")
        self.keys_tree.tag_configure("rare", foreground="#a60")
        self.keys_tree.tag_configure("move", foreground="#1a6fd1")
        self.keys_tree.tag_configure("distinct", foreground="#c00")
        self.keys_tree.tag_configure("odd", background="#f3f6fa")
        self.keys_sum = ttk.Label(t, text="", wraplength=900, justify="left")
        self.keys_sum.pack(anchor="w", pady=(6, 0))

    def refresh_keys_tab(self):
        names = list_profiles()
        self.keys_ca["values"] = names
        self.keys_cb["values"] = [NONE_LABEL] + names
        if self.keys_a.get() not in names:
            self.keys_a.set(names[0] if names else "")
        if self.keys_b.get() not in [NONE_LABEL] + names:
            self.keys_b.set(names[1] if len(names) > 1 else NONE_LABEL)
        elif self.keys_b.get() == NONE_LABEL and len(names) > 1 and names[0] == self.keys_a.get():
            self.keys_b.set(names[1])
        self.show_keys()

    def show_keys(self):
        self.keys_tree.delete(*self.keys_tree.get_children())
        names = list_profiles()
        pop = {n: read_keys(profile_path(n)) for n in names}
        name_a, name_b = self.keys_a.get(), self.keys_b.get()
        comparing = bool(name_b and name_b != NONE_LABEL and name_b != name_a)
        # When Account B isn't selected, show only Account A's columns so the
        # single-account view doesn't waste space on empty comparison fields.
        self.keys_tree.configure(displaycolumns=(
            ("a_share", "a_cls", "a_n", "a_hold", "b_share", "b_cls", "b_n", "b_hold", "delta")
            if comparing else ("a_share", "a_cls", "a_n", "a_hold")
        ))
        self.keys_tree.heading("a_share", text=(f"{name_a} Share" if name_a else "Share"))
        self.keys_tree.heading("a_cls", text="Class")
        self.keys_tree.heading("a_n", text="Presses")
        self.keys_tree.heading("a_hold", text="Hold (ms)")
        self.keys_tree.heading("b_share", text=f"{name_b} Share" if comparing else "B Share")
        self.keys_tree.heading("b_cls", text="Class")
        self.keys_tree.heading("b_n", text="Presses")
        self.keys_tree.heading("b_hold", text="Hold (ms)")
        self.keys_tree.heading("delta", text="Share Δ")
        ka = pop.get(name_a) or {}
        kb = pop.get(name_b) or {} if comparing else {}
        if not name_a:
            self.keys_sum.config(text="No fingerprints yet - train one first.")
            return
        if not ka and not kb:
            self.keys_sum.config(text="Neither selected account has per-key data. Retrain with 'Track individual keys' enabled.")
            return

        def index_keys(agg):
            return {r["key"]: r for r in key_rows(agg)} if agg else {}
        ra, rb = index_keys(ka), index_keys(kb)
        all_keys = set(ra) | set(rb)
        ordered = sorted(all_keys, key=lambda key: max(ra.get(key, {}).get("n", 0), rb.get(key, {}).get("n", 0)), reverse=True)
        total_a = sum(max(0, int(v[0])) for v in ka.values()) if ka else 0
        total_b = sum(max(0, int(v[0])) for v in kb.values()) if kb else 0
        for i, key in enumerate(ordered):
            a, b = ra.get(key), rb.get(key)
            def fields(r, total):
                if not r:
                    return ("—", "—", "0", "—")
                hold = "—" if r.get("hold") is None else f"{r['hold'] * 1000:.0f} ± {r.get('hold_sd', 0) * 1000:.0f}"
                return (f"{r['share'] * 100:.1f}%", r["cls"], str(r["n"]), hold)
            av, bv = fields(a, total_a), fields(b, total_b)
            delta = ((a["share"] if a else 0) - (b["share"] if b else 0)) * 100
            tags = ["odd"] if i % 2 else []
            if (a and a["cls"] == "Rare") or (b and b["cls"] == "Rare"):
                tags.append("rare")
            elif (a and a["cls"] == "Movement") or (b and b["cls"] == "Movement"):
                tags.append("move")
            self.keys_tree.insert("", "end", text=display_key(key), values=(*av, *bv, f"{delta:+.1f} pp"), tags=tuple(tags))

        lines = []
        for label, account, agg in (("A", name_a, ka), ("B", name_b, kb)):
            if agg:
                summary = keybind_summary(agg)
                lines.append(f"{label} ({account}) movement keys: {summary['movement_text']} | {summary['scheme']} | keybinds: {summary['text']}")
            elif account and account != NONE_LABEL:
                lines.append(f"{label} ({account}): no per-key data recorded; retrain with individual-key tracking enabled.")
        if ka and kb:
            sim = key_similarity(ka, kb, key_idf(list(pop.values())))
            if sim is not None:
                lines.append(f"Keybind similarity: {sim * 100:.0f}%  (A-only and B-only keys are visible in the table above.)")
        elif not comparing:
            lines.append("Choose Account B to compare both accounts' keys in adjacent columns.")
        self.keys_sum.config(text="\n".join(lines))


def register(ctx):
    ui = ctx.get("ui")
    ui.add_mixin(KeysTab)
    ui.add_tab("  Keys  ", "keys_tab", "_build_keys", order=30)
    ctx.hooks.on("ui.profiles_refreshed", lambda app: app.refresh_keys_tab())
