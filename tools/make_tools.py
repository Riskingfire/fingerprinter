#!/usr/bin/env python3
"""Create and manage Play Fingerprint API keys through the server's API.

Run it and paste in your server URL and an admin key (or any key that has the
MANAGE_KEYS permission). Needs only Python 3.8+, nothing to install.

    python make_keys.py

You can skip the prompts with environment variables or flags:

    PF_SERVER=https://your-app.onrender.com PF_ADMIN_KEY=... python make_keys.py
    python make_keys.py --server https://your-app.onrender.com
"""
import argparse
import getpass
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

# Used only if the server can't be asked for its permission list.
FALLBACK_PERMS = [
    ("ADMIN", 1, "Everything, including permissions added later"),
    ("VIEW_STATUS", 2, "GET /api/status"),
    ("READ_FINGERPRINTS", 4, "List, search, inspect, download fingerprints"),
    ("UPLOAD_FINGERPRINTS", 8, "Upload / overwrite fingerprints"),
    ("DELETE_FINGERPRINTS", 16, "Delete fingerprints"),
    ("READ_BANLIST", 32, "View the ban list"),
    ("MANAGE_BANS", 64, "Add / remove bans"),
    ("RUN_ANALYSIS", 128, "Run similarity analysis"),
    ("MANAGE_KEYS", 256, "Create / edit / revoke keys"),
]


SERVER_DEFAULT_DAYS = 60  # replaced by the server's real setting once we're signed in


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Client:
    def __init__(self, server, key):
        self.server = server
        self.key = key

    def call(self, method, path, body=None, auth=True, timeout=90):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.server + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "play-fingerprint-make-keys/1")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if auth:
            req.add_header("Authorization", "Bearer " + self.key)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                message = json.loads(raw).get("error", raw)
            except Exception:
                message = raw[:300] or exc.reason
            raise ApiError(exc.code, message)
        except urllib.error.URLError as exc:
            raise ApiError(0, f"Could not reach the server: {exc.reason}")


def normalize_server(value):
    value = value.strip().rstrip("/")
    if not value:
        sys.exit("No server URL given.")
    if not re.match(r"^https?://", value, re.I):
        local = re.match(r"^(localhost|127\.0\.0\.1)(:|/|$)", value, re.I)
        value = ("http://" if local else "https://") + value
    return value


def wake_server(client):
    """Render's free tier sleeps when idle; the first request can take up to a minute."""
    print("Contacting server (a sleeping Render free-tier server can take ~1 minute to wake)...")
    started = time.time()
    for attempt in range(4):
        try:
            client.call("GET", "/health", auth=False, timeout=90)
            print(f"Server is up ({time.time() - started:.0f}s).")
            return
        except ApiError as exc:
            if exc.status and exc.status < 500:
                sys.exit(f"Server answered {exc.status}: {exc}. Check the URL.")
            if attempt == 3:
                sys.exit(f"Server is not responding: {exc}")
            time.sleep(5)


def load_perms(client):
    global SERVER_DEFAULT_DAYS
    try:
        info = client.call("GET", "/api/permissions")
        if "default_key_lifetime_days" in info:
            SERVER_DEFAULT_DAYS = info["default_key_lifetime_days"]
        return [(p["name"], p["value"], p["description"]) for p in info["permissions"]]
    except (ApiError, KeyError):
        return FALLBACK_PERMS


def describe(perms, number):
    names = {name: value for name, value, _ in perms}
    if number & 1:
        return "ADMIN (everything)"
    return ", ".join(n for n, v in names.items() if v != 1 and number & v) or "nothing"


def show_perm_table(perms):
    print("\nPermissions (add the numbers together, or type names):")
    for name, value, desc in perms:
        print(f"  {value:>4}  {name:<20} {desc}")
    print("  Examples: 14 = upload + read + status | 38 = read-only | 1 = admin\n")


def parse_permissions(text, perms):
    """Accepts 14, '14', 'upload_fingerprints,view_status' or 'all'. Returns an int or None."""
    text = text.strip()
    if not text:
        return None
    by_name = {name: value for name, value, _ in perms}
    max_value = sum(by_name.values())
    if re.fullmatch(r"\d+", text):
        number = int(text)
    else:
        number = 0
        for token in re.split(r"[,\s|+]+", text):
            token = token.strip().upper().replace("-", "_")
            if not token:
                continue
            if token == "ALL":
                token = "ADMIN"
            if token not in by_name:
                print(f"  Unknown permission '{token}'.")
                return None
            number |= by_name[token]
    if number <= 0 or number & ~max_value:
        print(f"  Not a valid permission number (must be 1 to {max_value}).")
        return None
    return number


def fmt_expiry(key):
    """Human-readable expiry for a key dict returned by the server."""
    if key.get("expires") is None:
        return "never"
    date = key["expires"][:10]
    if key.get("expired"):
        return f"EXPIRED {date}"
    return f"{date} ({key['days_left']:.0f}d left)"


def parse_expiry_input(text):
    """'45' -> 45 days, '2027-01-31' -> that date, 'never' -> no expiry. Returns a request-body dict or None if invalid."""
    text = text.strip().lower()
    if text == "never":
        return {"never_expires": True}
    if re.fullmatch(r"\d+(\.\d+)?", text):
        days = float(text)
        if days <= 0:
            print("  Days must be more than 0.")
            return None
        return {"expires_in_days": int(days) if days == int(days) else days}
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return {"expires_at": text}
    print("  Type a number of days (e.g. 45), a date (e.g. 2027-01-31), or 'never'.")
    return None


def ask_expiry(for_edit=False):
    """Returns a dict of expiry fields for the request body ({} means: server default / keep unchanged)."""
    if for_edit:
        prompt = "New expiry: days, a date like 2027-01-31, 'never', or blank to keep it: "
    elif SERVER_DEFAULT_DAYS is None:
        prompt = "Expiry: days, a date like 2027-01-31, or 'never' [server default: never]: "
    else:
        prompt = (f"Expiry: days, a date like 2027-01-31, or 'never' "
                  f"[Enter = server default, {SERVER_DEFAULT_DAYS:g} days]: ")
    while True:
        text = ask(prompt).strip()
        if not text:
            return {}
        parsed = parse_expiry_input(text)
        if parsed is not None:
            return parsed


def ask(prompt):
    try:
        return input(prompt)
    except EOFError:
        sys.exit("\nInput ended.")


def ask_permissions(perms, default=None):
    show_perm_table(perms)
    while True:
        suffix = f" [{default}]" if default else ""
        text = ask(f"Permissions{suffix}: ") or (str(default) if default else "")
        number = parse_permissions(text, perms)
        if number is not None:
            return number


def create_key(client, perms):
    name = ask("\nName for this key (e.g. 'desktop uploader'): ").strip()
    if not name:
        print("A name is required.")
        return
    number = ask_permissions(perms)
    expiry = ask_expiry()
    print(f"\n  Name:        {name}")
    print(f"  Permissions: {number}  ->  {describe(perms, number)}")
    if "never_expires" in expiry:
        print("  Expires:     never")
    elif "expires_in_days" in expiry:
        print(f"  Expires:     in {expiry['expires_in_days']:g} days")
    elif "expires_at" in expiry:
        print(f"  Expires:     end of {expiry['expires_at']} (UTC)")
    else:
        print("  Expires:     server default" + ("" if SERVER_DEFAULT_DAYS is None else f" ({SERVER_DEFAULT_DAYS:g} days)"))
    if ask("Create this key? [Y/n] ").strip().lower() in ("n", "no"):
        print("Cancelled.")
        return
    result = client.call("POST", "/api/keys", {"name": name, "permissions": number, **expiry})
    bar = "=" * 64
    print(f"\n{bar}\n  NEW API KEY (shown only once, copy it now):\n\n  {result['key']}\n{bar}")
    print(f"  id: {result['id']}   permissions: {result['permissions']} ({describe(perms, result['permissions'])})")
    print(f"  expires: {fmt_expiry(result)}")
    if ask("\nAlso save it to a text file? [y/N] ").strip().lower() in ("y", "yes"):
        filename = re.sub(r"[^A-Za-z0-9_.-]+", "_", name) + "_api_key.txt"
        with open(filename, "w", encoding="utf-8") as handle:
            handle.write(result["key"] + "\n")
        print(f"Saved to {os.path.abspath(filename)}. Keep that file private.")


def list_keys(client, perms):
    keys = client.call("GET", "/api/keys")
    print(f"\n{'ID':<22} {'NAME':<22} {'PERM':>5}  {'SOURCE':<8} {'EXPIRES':<24} ALLOWS")
    for key in keys:
        print(f"{key['id']:<22} {key['name'][:21]:<22} {key['permissions']:>5}  {key['source']:<8} "
              f"{fmt_expiry(key):<24} {describe(perms, key['permissions'])}")
    expired = sum(1 for k in keys if k.get("expired"))
    note = f" {expired} expired (renew with option 3, or revoke with 4)." if expired else ""
    print(f"\n{len(keys)} key(s).{note} 'env' keys live in Render's environment variables and can't be changed here.")
    return keys


def pick_runtime_key(client, perms, action):
    keys = [k for k in list_keys(client, perms) if k["source"] == "runtime"]
    if not keys:
        print("No keys created through the API yet.")
        return None
    key_id = ask(f"\nID of the key to {action} (blank to cancel): ").strip()
    if not key_id:
        return None
    match = next((k for k in keys if k["id"] == key_id), None)
    if not match:
        print("That ID isn't one of the API-created keys above.")
    return match


def edit_key(client, perms):
    key = pick_runtime_key(client, perms, "edit")
    if not key:
        return
    new_name = ask(f"New name [{key['name']}]: ").strip()
    change = ask("Change permissions? [y/N] ").strip().lower() in ("y", "yes")
    body = {}
    if new_name:
        body["name"] = new_name
    if change:
        body["permissions"] = ask_permissions(perms, default=key["permissions"])
    print(f"Current expiry: {fmt_expiry(key)}")
    body.update(ask_expiry(for_edit=True))
    if not body:
        print("Nothing to change.")
        return
    updated = client.call("PATCH", "/api/keys/" + key["id"], body)
    print(f"Updated: {updated['name']} -> {updated['permissions']} ({describe(perms, updated['permissions'])}), "
          f"expires {fmt_expiry(updated)}")


def revoke_key(client, perms):
    key = pick_runtime_key(client, perms, "revoke")
    if not key:
        return
    if ask(f"Really revoke '{key['name']}' ({key['id']})? It stops working immediately. [y/N] ").strip().lower() in ("y", "yes"):
        client.call("DELETE", "/api/keys/" + key["id"])
        print("Revoked.")
    else:
        print("Cancelled.")


def main():
    parser = argparse.ArgumentParser(description="Create and manage Play Fingerprint API keys.")
    parser.add_argument("--server", default=os.environ.get("PF_SERVER"), help="e.g. https://your-app.onrender.com")
    parser.add_argument("--key", default=os.environ.get("PF_ADMIN_KEY"), help="an admin key (prefer the prompt or PF_ADMIN_KEY)")
    args = parser.parse_args()

    server = normalize_server(args.server or ask("Server URL (e.g. https://your-app.onrender.com): "))
    key = (args.key or getpass.getpass("Admin API key (typing is hidden): ")).strip()
    if not key:
        sys.exit("No API key given.")
    client = Client(server, key)

    wake_server(client)
    try:
        me = client.call("GET", "/api/me")
    except ApiError as exc:
        sys.exit(f"Key rejected: {exc}" if exc.status == 401 else f"Error: {exc}")
    print(f"Signed in as: {me['name']}  (permissions {me['permissions']}, expires {fmt_expiry(me)})")
    if me.get("days_left") is not None and me["days_left"] < 14:
        print(f"  Heads up: this key expires in {me['days_left']:.0f} days.")
    if "MANAGE_KEYS" not in me["can"]:
        sys.exit("This key doesn't have MANAGE_KEYS (or ADMIN), so it can't manage keys. Use your admin key.")
    perms = load_perms(client)

    actions = {"1": create_key, "2": list_keys, "3": edit_key, "4": revoke_key}
    while True:
        print("\n  1) Create a new key\n  2) List keys\n  3) Edit / renew a key\n  4) Revoke a key\n  5) Quit")
        choice = ask("Choose: ").strip()
        if choice in ("5", "q", "quit", "exit"):
            return
        action = actions.get(choice)
        if not action:
            print("Pick 1-5.")
            continue
        try:
            action(client, perms)
        except ApiError as exc:
            print(f"\nServer said ({exc.status}): {exc}")
        except KeyboardInterrupt:
            print("\nCancelled.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
