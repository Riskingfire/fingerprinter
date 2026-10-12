"""Server settings dialog + status line. All the work is done by server_sync; this is only the UI."""
NAME = "ui_server"
DESCRIPTION = "Server... dialog and status line (needs server_sync)"
REQUIRES = ['ui_tk', 'server_sync']
ORDER = 70

import json
import os
import queue
import threading

from fp.settings import PROFILE_DIR
from fp.store import list_profiles, load_banlist, profile_path, sanitize, save_banlist

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog


class ServerDialog:
    """Methods added to the main window (mixin). `self` is the window."""

    def _poll_server(self):
        self.srv_label.config(text=self.srv.state["text"], fg=self.srv.state["fg"])
        if self.srv.state.get("dirty") and self.mode is None:
            self.srv.state["dirty"] = False
            self.refresh_profiles()

    def open_server_dialog(self):
        client = self.srv
        if self._srv_win is not None and self._srv_win.winfo_exists():
            self._srv_win.lift()
            return
        w = self._srv_win = tk.Toplevel(self)
        w.title("Server sync")
        pad = ttk.Frame(w, padding=14)
        pad.pack(fill="both", expand=True)
        url_v = tk.StringVar(value=client.cfg["url"])
        key_v = tk.StringVar(value=client.cfg["key"])
        wake_v = tk.BooleanVar(value=client.cfg["wake_on_start"])
        auto_v = tk.BooleanVar(value=client.cfg["auto_upload"])
        ttk.Label(pad, text="Server URL  (e.g. https://play-fingerprint-server.onrender.com)").pack(anchor="w")
        ttk.Entry(pad, textvariable=url_v, width=64).pack(fill="x", pady=(2, 8))
        ttk.Label(pad, text="API key  (Render dashboard > Environment > API_KEY)").pack(anchor="w")
        ttk.Entry(pad, textvariable=key_v, show="*", width=64).pack(fill="x", pady=(2, 8))
        ttk.Checkbutton(pad, text="Ping the server when the app starts (wakes a sleeping Render instance)",
                        variable=wake_v).pack(anchor="w")
        ttk.Checkbutton(pad, text="Upload a fingerprint automatically after training",
                        variable=auto_v).pack(anchor="w")
        log = tk.Text(pad, height=11, width=64, state="disabled", wrap="word")
        q = queue.Queue()

        def say(msg):
            q.put(msg)

        def apply_cfg():
            client.cfg = {"url": url_v.get().strip().rstrip("/"), "key": key_v.get().strip(),
                            "wake_on_start": wake_v.get(), "auto_upload": auto_v.get()}
            save_server_cfg(client.cfg)

        def run(fn):
            apply_cfg()

            def work():
                try:
                    fn()
                except Exception as e:
                    say(f"Error: {e}")
            threading.Thread(target=work, daemon=True).start()

        def need_ready():
            say("Waking the server (a sleeping Render instance can take up to a minute)...")
            if not client.ensure_awake():
                say("Could not reach the server. Check the URL, or try again in a minute.")
                return False
            return True

        def do_test():
            if not need_ready():
                return
            code, body = server_call(client.cfg, "GET", "/api/status")
            if code == 200:
                j = json.loads(body)
                say(f"Connected. Server holds {j['fingerprints']} fingerprint(s) and {j['bans']} ban(s).")
            elif code == 401:
                say("Server is up but rejected the API key (401).")
            else:
                say(f"Unexpected response: HTTP {code}")

        def do_upload():
            if not need_ready():
                return
            names = list_profiles()
            if not names:
                say("No local fingerprints to upload.")
                return
            ok = 0
            for n in names:
                good, msg = client.upload_one(n)
                ok += good
                say(f"  {n}: {msg}")
            say(f"Uploaded {ok} of {len(names)}.")

        def do_download():
            if not need_ready():
                return
            code, body = server_call(client.cfg, "GET", "/api/fingerprints")
            if code != 200:
                say(f"List failed: HTTP {code}")
                return
            os.makedirs(PROFILE_DIR, exist_ok=True)
            got = skipped = 0
            for r in json.loads(body):
                name = sanitize(str(r.get("name", "")))
                if not name:
                    continue
                path = profile_path(name)
                if os.path.exists(path) and read_updated(path) >= str(r.get("updated", "")):
                    skipped += 1
                    continue
                c, raw = server_call(client.cfg, "GET", "/api/fingerprints/" + _q(name), timeout=60)
                if c == 200 and 0 < len(raw) <= MAX_BYTES:
                    with open(path, "wb") as fh:
                        fh.write(raw)
                    got += 1
                    say(f"  downloaded {name}")
                else:
                    say(f"  {name}: HTTP {c}")
            say(f"Downloaded {got}, already up to date {skipped}.")
            client.state["dirty"] = True

        def do_pull_bans():
            if not need_ready():
                return
            code, body = server_call(client.cfg, "GET", "/api/banlist")
            if code != 200:
                say(f"Failed: HTTP {code}")
                return
            data = json.loads(body)
            save_banlist({sanitize(k): {"reason": str(v.get("reason", "")), "date": str(v.get("date", ""))}
                          for k, v in data.items() if isinstance(v, dict) and sanitize(k)})
            say(f"Ban list replaced with the server's ({len(data)} entries).")
            client.state["dirty"] = True

        def do_push_bans():
            if not need_ready():
                return
            ban = load_banlist()
            for n, e in ban.items():
                c, _ = server_call(client.cfg, "PUT", "/api/banlist/" + _q(n),
                                   json.dumps(e).encode(), content_type="application/json")
                say(f"  {n}: {'ok' if c == 200 else 'HTTP ' + str(c)}")
            say(f"Pushed {len(ban)} ban(s). (Bans/unbans you make in the app are also sent automatically.)")

        def do_server_analysis():
            if not need_ready():
                return
            code, raw = server_call(client.cfg, "POST", "/api/analysis", b"{}",
                                    content_type="application/json", timeout=90)
            if code != 200:
                say(f"Server analysis failed: HTTP {code} {raw[:300].decode('utf-8', 'replace')}")
                return
            result = json.loads(raw)
            matches = result.get("results", [])
            flagged = [x for x in matches if x.get("flag") in ("REVIEW", "HIGH")]
            say(f"Server analysis checked {len(matches)} candidate-to-banned comparison(s); {len(flagged)} need human review.")
            for item in flagged[:100]:
                say(f"  {item.get('flag')}: {item.get('candidate')} vs banned {item.get('banned')} — distance {item.get('distance')} ({item.get('shared_features')} shared features)")
            if not flagged:
                say("No potential matches were flagged by the server's approximate analysis.")
            say("Reminder: heuristic matches are not proof of identity. Review manually; never auto-ban from this score.")

        row1 = ttk.Frame(pad)
        row1.pack(fill="x", pady=(10, 4))
        ttk.Button(row1, text="Save + test connection", command=lambda: run(do_test)).pack(side="left")
        ttk.Button(row1, text="Save only", command=apply_cfg).pack(side="left", padx=6)
        row2 = ttk.Frame(pad)
        row2.pack(fill="x", pady=2)
        ttk.Button(row2, text="Upload my fingerprints", command=lambda: run(do_upload)).pack(side="left")
        ttk.Button(row2, text="Download from server", command=lambda: run(do_download)).pack(side="left", padx=6)
        row3 = ttk.Frame(pad)
        row3.pack(fill="x", pady=2)
        ttk.Button(row3, text="Pull ban list (replaces local)", command=lambda: run(do_pull_bans)).pack(side="left")
        ttk.Button(row3, text="Push my ban list", command=lambda: run(do_push_bans)).pack(side="left", padx=6)
        ttk.Button(row3, text="Run server ban analysis", command=lambda: run(do_server_analysis)).pack(side="left", padx=6)
        log.pack(fill="both", expand=True, pady=(10, 0))

        def poll():
            try:
                while True:
                    msg = q.get_nowait()
                    log.config(state="normal")
                    log.insert("end", msg + "\n")
                    log.see("end")
                    log.config(state="disabled")
            except queue.Empty:
                pass
            if w.winfo_exists():
                w.after(200, poll)
        poll()


def _attach(app):
    app.srv = app.ctx.get("server")
    app._srv_win = None
    app.srv_label = tk.Label(app.footer_row, text=app.srv.state["text"], fg=app.srv.state["fg"])
    app.srv_label.pack(side="left")


def register(ctx):
    global server_call, save_server_cfg, read_updated, _q, MAX_BYTES
    api = ctx.get("server_api")
    server_call, save_server_cfg, read_updated, _q, MAX_BYTES = (
        api["server_call"], api["save_cfg"], api["read_updated"], api["quote"], api["MAX_BYTES"])
    ui = ctx.get("ui")
    ui.add_mixin(ServerDialog)
    ui.add_header_button("Server...", "open_server_dialog", order=20)
    ctx.hooks.on("ui.built", _attach)
    ctx.hooks.on("ui.tick", lambda app: app._poll_server())
