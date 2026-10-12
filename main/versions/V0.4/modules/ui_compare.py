"""Compare tab: two accounts feature by feature."""
NAME = "ui_compare"
DESCRIPTION = "Compare tab: side-by-side account comparison"
REQUIRES = ['ui_tk']
ORDER = 40

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import numpy as np
from fp.analysis import feature_weights, load_all_profiles, pair_score
from fp.classify import typical_vector
from fp.features import IDX
from fp.keybinds import display_key, key_idf, key_similarity, read_keys
from fp.profile import Profile, stability_text
from fp.settings import FEATURE_NAMES, NAN
from fp.store import list_profiles, profile_path


class CompareTab:
    """Methods added to the main window (mixin). `self` is the window."""

    def _build_compare(self):
        t = self.compare_tab
        ttk.Label(t, text="Account comparison", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(t, text="Choose two saved fingerprints to compare their recorded behavior side by side.",
                  foreground="#58677d").pack(anchor="w", pady=(2, 12))
        pick = ttk.LabelFrame(t, text="Compare accounts", padding=12)
        pick.pack(fill="x", pady=(0, 12))
        ttk.Label(pick, text="Account A").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.compare_a = tk.StringVar()
        self.compare_ca = ttk.Combobox(pick, textvariable=self.compare_a, state="readonly", width=28)
        self.compare_ca.grid(row=0, column=1, sticky="ew", padx=(0, 18))
        ttk.Label(pick, text="Account B").grid(row=0, column=2, sticky="w", padx=(0, 8))
        self.compare_b = tk.StringVar()
        self.compare_cb = ttk.Combobox(pick, textvariable=self.compare_b, state="readonly", width=28)
        self.compare_cb.grid(row=0, column=3, sticky="ew", padx=(0, 12))
        ttk.Button(pick, text="Compare", command=self.show_compare).grid(row=0, column=4)
        pick.columnconfigure(1, weight=1)
        pick.columnconfigure(3, weight=1)
        self.compare_summary = ttk.Label(t, text="Select two accounts above.", font=("Segoe UI", 12, "bold"),
                                         padding=(12, 10), background="#ffffff", anchor="w")
        self.compare_summary.pack(fill="x", pady=(0, 10))
        tablebox = ttk.Frame(t)
        tablebox.pack(fill="both", expand=True)
        cols = ("metric", "a", "b", "delta")
        self.compare_tree = ttk.Treeview(tablebox, columns=cols, show="headings")
        for col, title, width, anchor in [
            ("metric", "STATISTIC", 260, "w"), ("a", "ACCOUNT A", 240, "center"),
            ("b", "ACCOUNT B", 240, "center"), ("delta", "DIFFERENCE", 180, "center")]:
            self.compare_tree.heading(col, text=title)
            self.compare_tree.column(col, width=width, anchor=anchor, stretch=(col == "metric"))
        scroll = ttk.Scrollbar(tablebox, orient="vertical", command=self.compare_tree.yview)
        self.compare_tree.configure(yscrollcommand=scroll.set)
        self.compare_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.compare_tree.tag_configure("section", background="#e7eef8", font=("Segoe UI", 9, "bold"))
        self.compare_tree.tag_configure("odd", background="#f7f9fc")
        ttk.Label(t, text="Similarity scores are estimates from input patterns, not proof of shared account ownership.",
                  foreground="#65748a").pack(anchor="w", pady=(8, 0))
        for cb in (self.compare_ca, self.compare_cb):
            cb.bind("<<ComboboxSelected>>", lambda _e: self.show_compare())

    def refresh_compare_tab(self):
        names = list_profiles()
        for cb, var in ((self.compare_ca, self.compare_a), (self.compare_cb, self.compare_b)):
            cb["values"] = names
            if var.get() not in names:
                var.set(names[0] if names else "")
        if len(names) > 1 and self.compare_a.get() == self.compare_b.get():
            self.compare_b.set(names[1])
        self.show_compare()

    def show_compare(self):
        if not hasattr(self, "compare_tree"):
            return
        self.compare_tree.delete(*self.compare_tree.get_children())
        a_name, b_name = self.compare_a.get(), self.compare_b.get()
        if not a_name or not b_name:
            self.compare_summary.config(text="Create or import at least two fingerprints to compare accounts.")
            return
        if a_name == b_name:
            self.compare_summary.config(text="Choose two different accounts to see a comparison.")
            return
        try:
            a, b = Profile.load(profile_path(a_name)), Profile.load(profile_path(b_name))
            va = typical_vector(a.windows) if a.windows else np.full(len(FEATURE_NAMES), NAN)
            vb = typical_vector(b.windows) if b.windows else np.full(len(FEATURE_NAMES), NAN)
            score = pair_score(a, b, feature_weights(list(load_all_profiles(min_windows=5).values())))
            sim = None if score is None else 100.0 / (1.0 + (score / 1.8) ** 4)
            key_sim = key_similarity(
                a.keys, b.keys,
                key_idf([read_keys(profile_path(n)) for n in list_profiles()]),
            )
            behavioral_text = (
                f"Behavioral similarity: {sim:.1f}%" if sim is not None
                else "Behavioral similarity: not enough matching data"
            )
            key_text = (
                f"Keybind similarity: {key_sim * 100:.1f}%" if key_sim is not None
                else "Keybind similarity: not enough key data"
            )
            self.compare_summary.config(text=f"{behavioral_text}     •     {key_text}")
            rows = [
                ("OVERVIEW", None, None, None, True),
                ("Class code", a.classify().get("code", "—"), b.classify().get("code", "—"), None, False),
                ("Playstyle class", a.classify().get("playstyle", "—"), b.classify().get("playstyle", "—"), None, False),
                ("Recorded time", f"{a.seconds/60:.1f} min", f"{b.seconds/60:.1f} min", None, False),
                ("Training windows", str(len(a.windows)), str(len(b.windows)), None, False),
                ("Training stability", stability_text(a).replace("Stability: ", ""),
                 stability_text(b).replace("Stability: ", ""), None, False),
                ("Keyboard style", a.classify().get("k_desc", "—"), b.classify().get("k_desc", "—"), None, False),
                ("Mouse style", a.classify().get("m_desc", "—"), b.classify().get("m_desc", "—"), None, False),
                ("ADS / right-click", a.classify().get("ads_desc", "—"), b.classify().get("ads_desc", "—"), None, False),
                ("MOUSE BEHAVIOR", None, None, None, True),
            ]
            metrics = [
                ("Mean pointer speed", "m_speed_mean", "px/s", 1),
                ("Pointer speed variation", "m_speed_cv", "ratio", 3),
                ("Mouse acceleration", "m_acc_mean", "px/s²", 1),
                ("Mouse path straightness", "m_straight", "%", 2),
                ("Average stroke length", "m_stroke_len", "px", 1),
                ("Stroke rate", "m_stroke_rate", "/s", 2),
                ("Click rate", "c_rate", "/s", 2),
                ("Typical click hold", "c_hold_med", "s", 3),
                ("Right-click / ADS time", "r_time_ratio", "%", 2),
                ("Shots while ADS", "r_fire_ratio", "%", 2),
                ("Key presses per second", "k_rate", "/s", 2),
                ("Typical key hold", "k_hold_med", "s", 3),
                ("Key timing variation", "k_flight_cv", "ratio", 3),
                ("Key overlap ratio", "k_overlap", "ratio", 3),
                ("Unique keys per window", "k_unique", "keys", 1),
                ("Scroll rate", "s_rate", "/s", 2),
            ]
            # Keep section headers separate from metric tuples: each metric has
            # exactly four fields (label, feature, unit, precision).
            keyboard_start = next(i for i, item in enumerate(metrics) if item[1] == "k_rate")
            for label, field, unit, decimals in metrics[:keyboard_start]:
                rows.append((label, self._compare_value(va, field, unit, decimals),
                             self._compare_value(vb, field, unit, decimals), field, False))
            rows.append(("KEYBOARD BEHAVIOR", None, None, None, True))
            for label, field, unit, decimals in metrics[keyboard_start:]:
                rows.append((label, self._compare_value(va, field, unit, decimals),
                             self._compare_value(vb, field, unit, decimals), field, False))
            for idx, (label, av, bv, field, section) in enumerate(rows):
                if section:
                    self.compare_tree.insert("", "end", values=(label, "", "", ""), tags=("section",))
                    continue
                delta = ""
                if field:
                    xa, xb = float(va[IDX[field]]), float(vb[IDX[field]])
                    if not np.isnan(xa) and not np.isnan(xb):
                        d = xa - xb
                        if unit == "%":
                            delta = f"{d * 100:+.2f} pp"
                        elif unit in ("s", "ratio", "/s"):
                            delta = f"{d:+.3f} {unit}"
                        elif unit == "keys":
                            delta = f"{d:+.1f}"
                        else:
                            delta = f"{d:+.1f} {unit}"
                self.compare_tree.insert("", "end", values=(label, av, bv, delta),
                                         tags=("odd",) if idx % 2 else ())

            # Compare the actual recorded keys side by side as well as the
            # aggregate keyboard metrics above. This is the per-key detail the
            # account comparison needs (press count, share, and typical hold).
            self.compare_tree.insert("", "end", values=("KEY-BY-KEY USAGE", "", "", ""),
                                     tags=("section",))
            keys_a, keys_b = a.keys or {}, b.keys or {}
            total_a = sum(max(0, int(e[0])) for e in keys_a.values())
            total_b = sum(max(0, int(e[0])) for e in keys_b.values())
            all_keys = set(keys_a) | set(keys_b)
            ordered_keys = sorted(
                all_keys,
                key=lambda key: max(keys_a.get(key, [0])[0], keys_b.get(key, [0])[0]),
                reverse=True,
            )
            if not all_keys or (total_a == 0 and total_b == 0):
                self.compare_tree.insert("", "end", values=(
                    "No individual-key data", "Not recorded", "Not recorded", ""
                ))
            else:
                self.compare_tree.insert("", "end", values=(
                    "Format", "presses | share | avg hold", "presses | share | avg hold", "share difference"
                ), tags=("section",))
                for key_index, key in enumerate(ordered_keys):
                    ea = keys_a.get(key)
                    eb = keys_b.get(key)

                    def key_details(entry, total):
                        if not entry or entry[0] <= 0:
                            return "Not used"
                        presses = int(entry[0])
                        share = presses / total * 100 if total else 0.0
                        hold = (f"{entry[2] / entry[1]:.2f}s" if len(entry) > 1 and entry[1] >= 3 else "n/a")
                        return f"{presses} | {share:.1f}% | {hold}"

                    share_a = (ea[0] / total_a) if ea and total_a else 0.0
                    share_b = (eb[0] / total_b) if eb and total_b else 0.0
                    share_delta = (share_a - share_b) * 100
                    self.compare_tree.insert(
                        "", "end",
                        values=(display_key(key), key_details(ea, total_a), key_details(eb, total_b),
                                f"{share_delta:+.1f} pp" if ea or eb else "—"),
                        tags=("odd",) if key_index % 2 else (),
                    )
        except Exception as e:
            self.compare_summary.config(text=f"Could not compare these fingerprints: {e}")

    @staticmethod
    def _compare_value(vec, field, unit, decimals):
        value = float(vec[IDX[field]])
        if np.isnan(value):
            return "No data"
        if unit == "%":
            return f"{value * 100:.{decimals}f}%"
        if unit in ("s", "ratio", "/s"):
            return f"{value:.{decimals}f} {unit}"
        if unit == "keys":
            return f"{value:.{decimals}f}"
        return f"{value:.{decimals}f} {unit}"


def register(ctx):
    ui = ctx.get("ui")
    ui.add_mixin(CompareTab)
    ui.add_tab("  Compare  ", "compare_tab", "_build_compare", order=40)
    ctx.hooks.on("ui.profiles_refreshed", lambda app: app.refresh_compare_tab())
