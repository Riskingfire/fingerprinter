"""Class module: the Class guide window (header button) and the live class display.

Class display (only shown when this module is installed):
  * Train tab    "Class so far" / "ADS so far" in the Live box, and the class summary of the selected
                 fingerprint under its stats
  * Detect tab   "Current style: ..." under the verdict (if the Detect tab is installed)
  * Saved dialog the Class / Keyboard / Mouse / ADS lines shown after training
The class itself is computed by the base (fp/classify.py); this module only displays it.
"""
NAME = "ui_class"
DESCRIPTION = "Class guide window and class display (Train / Detect / saved dialog)"
REQUIRES = ['ui_tk']
OPTIONAL = ['ui_detect']
ORDER = 60

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from fp.classify import _range_text
from fp.settings import KB_PACE_CUTS, KB_PACE_NAMES, KB_STYLE_CUTS, KB_STYLE_NAMES, MS_PACE_CUTS, MS_PACE_NAMES, MS_STYLE_CUTS, MS_STYLE_NAMES


class GuideDialog:
    """Methods added to the main window (mixin). `self` is the window."""

    def show_guide(self):
        """Open the full, scrollable reference for every compact class-code segment."""
        w = tk.Toplevel(self)
        w.title("Play Fingerprint — Classification Guide")
        w.geometry("920x760")
        w.minsize(720, 560)
        w.transient(self)

        outer = ttk.Frame(w, padding=0)
        outer.pack(fill="both", expand=True)
        header = tk.Frame(outer, bg="#17365e", padx=22, pady=18)
        header.pack(fill="x")
        tk.Label(header, text="CLASSIFICATION GUIDE", bg="#17365e", fg="#ffffff",
                 font=("Segoe UI", 17, "bold")).pack(anchor="w")
        tk.Label(header, text="Understand every segment of a Play Fingerprint class code",
                 bg="#17365e", fg="#dbeafe", font=("Segoe UI", 10)).pack(anchor="w", pady=(4, 0))

        area = ttk.Frame(outer)
        area.pack(fill="both", expand=True)
        canvas = tk.Canvas(area, highlightthickness=0, bg="#eef2f7")
        scrollbar = ttk.Scrollbar(area, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        content = ttk.Frame(canvas, padding=(18, 16, 18, 22))
        win_id = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win_id, width=e.width))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        w.bind("<Destroy>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        def section(title, subtitle, rows, footnote=None):
            """Render each classification as a visual chart rather than a text-heavy table."""
            card = ttk.LabelFrame(content, text=title, padding=(13, 10, 13, 12))
            card.pack(fill="x", pady=(0, 12))
            ttk.Label(card, text=subtitle, wraplength=790, justify="left").pack(anchor="w", pady=(0, 9))

            # k/m use a matrix: columns are pace levels; rows are style levels.
            if len(rows) == 20 and rows[0][0][:1] in ("k", "m"):
                prefix = rows[0][0][0]
                pace_names = KB_PACE_NAMES if prefix == "k" else MS_PACE_NAMES
                style_names = KB_STYLE_NAMES if prefix == "k" else MS_STYLE_NAMES
                grid = tk.Frame(card, bg="#cbd8e8", padx=1, pady=1)
                grid.pack(fill="x", expand=True)
                tk.Label(grid, text="STYLE ↓  /  PACE →", bg="#17365e", fg="white",
                         font=("Segoe UI", 9, "bold"), padx=8, pady=9).grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
                for pi, pace_name in enumerate(pace_names, start=1):
                    tk.Label(grid, text=pace_name, bg="#17365e", fg="white",
                             font=("Segoe UI", 9, "bold"), padx=7, pady=9, wraplength=115,
                             justify="center").grid(row=0, column=pi, sticky="nsew", padx=1, pady=1)
                # Code numbering is pace-major: code = pace_index * number_of_styles + style_index + 1.
                for si, style_name in enumerate(style_names):
                    tk.Label(grid, text=style_name, bg="#dbeafe", fg="#12345a",
                             font=("Segoe UI", 9, "bold"), padx=8, pady=9,
                             wraplength=130, justify="left").grid(row=si+1, column=0, sticky="nsew", padx=1, pady=1)
                    for pi, _pace_name in enumerate(pace_names):
                        number = pi * len(style_names) + si + 1
                        code = f"{prefix}{number:02d}"
                        bg = "#ffffff" if (pi + si) % 2 == 0 else "#f1f5f9"
                        tk.Label(grid, text=code, bg=bg, fg="#12345a",
                                 font=("Consolas", 11, "bold"), padx=10, pady=12,
                                 relief="flat", anchor="center").grid(row=si+1, column=pi+1,
                                                                       sticky="nsew", padx=1, pady=1)
                for col in range(len(pace_names) + 1):
                    grid.columnconfigure(col, weight=1, uniform=f"{prefix}_col")
                for row in range(len(style_names) + 1):
                    grid.rowconfigure(row, weight=1)
            else:
                # Compact chart: each class is a colored band with its code, label, and range.
                chart = tk.Frame(card, bg="#dbe4f0", padx=1, pady=1)
                chart.pack(fill="x", expand=True)
                chart.columnconfigure(0, weight=0)
                chart.columnconfigure(1, weight=2)
                chart.columnconfigure(2, weight=3)
                for i, (code, meaning, ranges) in enumerate(rows):
                    bg = "#ffffff" if i % 2 == 0 else "#f1f5f9"
                    code_bg = "#dbeafe" if not code.endswith("??") else "#e5e7eb"
                    tk.Label(chart, text=code, bg=code_bg, fg="#12345a", font=("Consolas", 10, "bold"),
                             padx=10, pady=7, width=9, anchor="center").grid(row=i, column=0, sticky="nsew", padx=1, pady=1)
                    tk.Label(chart, text=meaning, bg=bg, fg="#243247", font=("Segoe UI", 9, "bold"),
                             padx=10, pady=7, anchor="w").grid(row=i, column=1, sticky="nsew", padx=1, pady=1)
                    tk.Label(chart, text=ranges, bg=bg, fg="#526176", padx=10, pady=7, anchor="w",
                             wraplength=420, justify="left").grid(row=i, column=2, sticky="nsew", padx=1, pady=1)
            if footnote:
                ttk.Label(card, text=footnote, foreground="#596579", wraplength=790,
                          justify="left").pack(anchor="w", pady=(8, 0))

        ttk.Label(content, text="A class code is a compact summary of measured input habits. It is not the full fingerprint ID and it does not prove who is playing.",
                  wraplength=800, justify="left").pack(anchor="w", pady=(0, 10))
        code_card = ttk.LabelFrame(content, text="Example code", padding=13)
        code_card.pack(fill="x", pady=(0, 12))
        tk.Label(code_card, text="k08m14a03cr04p05s03ads02ko04cv01ch03", bg="#dbeafe", fg="#12345a",
                 font=("Consolas", 14, "bold"), padx=12, pady=10).pack(anchor="w", fill="x")
        ttk.Label(code_card, text="Read from left to right: k, m, a, cr, p, s, ads, ko, cv, ch. Each segment has its own scale. A ?? segment means that feature could not be classified from the available data.",
                  wraplength=800, justify="left").pack(anchor="w", pady=(8, 0))

        # Keyboard and mouse codes use a 4-by-5 grid: pace level * 5 + style level + 1.
        def grid_rows(prefix, pace_names, style_names):
            rows = []
            for pi, pace_name in enumerate(pace_names):
                for si, style_name in enumerate(style_names):
                    number = pi * len(style_names) + si + 1
                    rows.append((f"{prefix}{number:02d}", f"{pace_name} pace + {style_name.lower()} style",
                                 f"Pace: {pace_name}; style: {style_name}"))
            return rows

        section("1. Keyboard activity — k01 to k20",
                "Combines the rate of key presses with the typical duration a key is held. The code number is a grid cell, not a rank.",
                grid_rows("k", KB_PACE_NAMES, KB_STYLE_NAMES),
                "Pace thresholds (key presses/second): " + _range_text(KB_PACE_CUTS) + ". Median key-hold thresholds (seconds): " + _range_text(KB_STYLE_CUTS) + ".")
        section("2. Mouse movement — m01 to m20",
                "Combines average pointer speed while moving with mouse-stroke straightness. Straightness ranges from 0 to 1.",
                grid_rows("m", MS_PACE_NAMES, MS_STYLE_NAMES),
                "Speed thresholds (pixels/second): " + _range_text(MS_PACE_CUTS) + ". Straightness thresholds: " + _range_text(MS_STYLE_CUTS) + ". Results can vary with display scaling, sensitivity, and the game.")
        section("3. Aim-path proxy — a01 to a05",
                "Measures how straight recorded mouse paths tend to be. This is only a movement-shape proxy; it does not measure aim accuracy or skill.",
                [("a01", "Very winding paths", "straightness <= 0.25"),
                 ("a02", "Winding paths", "0.25 < straightness <= 0.45"),
                 ("a03", "Mixed straightness", "0.45 < straightness <= 0.65"),
                 ("a04", "Mostly direct paths", "0.65 < straightness <= 0.82"),
                 ("a05", "Very direct paths", "straightness > 0.82")])
        section("4. Click rate — cr01 to cr05",
                "Classifies the rate of recorded mouse clicks, in clicks per second.",
                [("cr01", "Very low", "<= 0.5 clicks/s"), ("cr02", "Low to moderate", "> 0.5 to 1.5 clicks/s"),
                 ("cr03", "Moderate", "> 1.5 to 3.0 clicks/s"), ("cr04", "High", "> 3.0 to 5.0 clicks/s"),
                 ("cr05", "Very high", "> 5.0 clicks/s")],
                "The rate is calculated over the recorded window, so idle time and the type of game activity can affect it.")
        section("5. Overall playstyle — p01 to p09",
                "A single best-match label selected by heuristic rules from the recorded keyboard and mouse features. It is not an independent hardware type.",
                [("p01", "ADS-focused", "Frequent right-click / aiming-time behavior"),
                 ("p02", "Click-intensive", "High mouse-click rate"),
                 ("p03", "Keyboard-led", "Behavior dominated by keyboard activity"),
                 ("p04", "Mouse-led", "More mouse-oriented behavior"),
                 ("p05", "Balanced keyboard + mouse", "Regular use of both inputs"),
                 ("p06", "High-input hybrid", "High keyboard and mouse activity"),
                 ("p07", "Precision / direct-aim", "Frequent, relatively straight mouse movements"),
                 ("p08", "Scroll-intensive", "Frequent scrolling"),
                 ("p09", "Low-intensity / mixed", "Lower-intensity or less distinctive behavior"),
                 ("p??", "Not enough data", "Not enough available features to assign a label")],
                "The app applies priority rules to choose one label. A different game or task can change the label for the same person.")
        section("6. Scrolling — s01 to s05",
                "Counts scroll events per second.",
                [("s01", "Little or no scrolling", "<= 0.05 events/s"), ("s02", "Occasional scrolling", "> 0.05 to 0.25 events/s"),
                 ("s03", "Regular scrolling", "> 0.25 to 0.75 events/s"), ("s04", "Frequent scrolling", "> 0.75 to 1.5 events/s"),
                 ("s05", "Very frequent scrolling", "> 1.5 events/s")])
        section("7. ADS / right-click — ads01 to ads05",
                "Measures the fraction of recorded time that the right mouse button is held. It does not know whether a particular game uses right-click to aim.",
                [("ads01", "Almost never held", "<= 1% of recorded time"), ("ads02", "Low hold share", "> 1% to 8%"),
                 ("ads03", "Moderate hold share", "> 8% to 20%"), ("ads04", "High hold share", "> 20% to 40%"),
                 ("ads05", "Very high hold share", "> 40%")])
        section("8. Keyboard overlap — ko01 to ko05",
                "Measures how often key presses overlap with other key presses, as a ratio.",
                [("ko01", "Very little overlap", "<= 0.02"), ("ko02", "Low overlap", "> 0.02 to 0.10"),
                 ("ko03", "Moderate overlap", "> 0.10 to 0.25"), ("ko04", "High overlap", "> 0.25 to 0.50"),
                 ("ko05", "Very high overlap", "> 0.50")],
                "Overlapping keys are common in games that use simultaneous movement and action inputs.")
        section("9. Keyboard timing variation — cv01 to cv05",
                "Measures variation in the intervals between keyboard events using a coefficient of variation. Larger values mean less consistent intervals.",
                [("cv01", "Very consistent", "<= 0.25"), ("cv02", "Fairly consistent", "> 0.25 to 0.50"),
                 ("cv03", "Moderately variable", "> 0.50 to 1.0"), ("cv04", "Highly variable", "> 1.0 to 2.0"),
                 ("cv05", "Very highly variable", "> 2.0")])
        section("10. Mouse-button hold duration — ch01 to ch05",
                "Uses the median duration of recorded mouse-button holds, in seconds.",
                [("ch01", "Very short holds", "<= 0.05 s"), ("ch02", "Short holds", "> 0.05 to 0.12 s"),
                 ("ch03", "Medium holds", "> 0.12 to 0.25 s"), ("ch04", "Long holds", "> 0.25 to 0.50 s"),
                 ("ch05", "Very long holds", "> 0.50 s")])

        caveats = ttk.LabelFrame(content, text="Important notes", padding=13)
        caveats.pack(fill="x", pady=(0, 12))
        notes = [
            "Class thresholds are hand-set heuristics, not population-calibrated identity boundaries.",
            "The code summarizes behavior; the full fingerprint uses many continuous features beyond these ten categories.",
            "Per-key usage and keybind comparisons are separate from this compact class code.",
            "This version records keyboard and mouse input. It does not currently classify gamepad-only or touchscreen input.",
            "A matching class code is not proof that two accounts belong to the same person. Use matches as a lead for human review, not as an automatic ban decision.",
        ]
        for note in notes:
            ttk.Label(caveats, text="•  " + note, wraplength=790, justify="left").pack(anchor="w", pady=2)
        ttk.Button(content, text="Close guide", command=w.destroy).pack(anchor="e", pady=(0, 4))


# ---- class display ----------------------------------------------------------------------------
def _attach(app):
    """Fill the empty slots the window and the Detect tab leave for this module."""
    app.class_tv = {"class": tk.StringVar(value="-"), "ads": tk.StringVar(value="-")}
    slot = getattr(app, "train_class_slot", None)
    if slot is not None:
        for label, key in (("Class so far", "class"), ("ADS so far", "ads")):
            r = ttk.Frame(slot)
            r.pack(fill="x", pady=1)
            ttk.Label(r, text=label, width=20).pack(side="left")
            ttk.Label(r, textvariable=app.class_tv[key]).pack(side="left")
    slot = getattr(app, "train_info_slot", None)
    app.class_info = ttk.Label(slot, text="", foreground="#555") if slot is not None else None
    if app.class_info is not None:
        app.class_info.pack(anchor="w")
    slot = getattr(app, "detect_class_slot", None)               # only if the Detect tab is installed
    app.live_class = tk.Label(slot, text="", fg="#335", wraplength=480, justify="center") if slot is not None else None
    if app.live_class is not None:
        app.live_class.pack(fill="x", pady=(0, 4))
    app.hooks.on("train.window", lambda info: _on_train_window(app, info))
    app.hooks.on("detect.update", lambda info: _on_detect_update(app, info))


def _on_train_window(app, info):
    if info.get("state") == "window":
        app.class_tv["class"].set(info["class_text"])
        app.class_tv["ads"].set(info["ads_short"])


def _on_detect_update(app, info):
    if app.live_class is None or app.mode != "detect" or info.get("state") in ("paused", "waiting"):
        return
    c = info.get("live_class")
    if c:
        app.live_class.config(text=f"Current style: {c['code']}\n"
                                   f"Keyboard: {c['k_desc']}  |  Mouse: {c['m_desc']}\n"
                                   f"ADS: {c['ads_desc']}")


def _on_profile_selected(app, p):
    if getattr(app, "class_info", None) is None:
        return
    if p is None:
        app.class_info.config(text="")
        return
    try:
        c = p.classify()
        app.class_info.config(text=f"Class: K+M {c['code']} · {c.get('playstyle', 'Not enough data')}\n"
                                   f"ADS: {c['ads_desc']}")
    except Exception as e:
        app.class_info.config(text=f"Class unavailable: {e}")


def _saved_details(p):
    c = p.classify()
    return [f"Class: {c['code']}", f"  Keyboard: {c['k_desc']}", f"  Mouse: {c['m_desc']}",
            f"  ADS: {c['ads_desc']}"]


def _detect_reset(app):
    if getattr(app, "live_class", None) is not None:
        app.live_class.config(text="")


def register(ctx):
    ui = ctx.get("ui")
    ui.add_mixin(GuideDialog)
    ui.add_header_button("Class guide", "show_guide", order=10)
    ctx.hooks.on("ui.built", _attach)
    ctx.hooks.on("ui.profile_selected", _on_profile_selected)
    ctx.hooks.on("ui.saved_details", _saved_details)
    ctx.hooks.on("ui.detect_reset", _detect_reset)
