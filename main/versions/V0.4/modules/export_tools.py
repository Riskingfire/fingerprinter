"""Export / backup tools (no UI): export one fingerprint to JSON or CSV, back up and restore all of them.

    python fingerprinter.py export NAME [--format json|csv] [--windows] [--out FILE]
    python fingerprinter.py backup [--out FILE.zip]
    python fingerprinter.py restore FILE.zip [--overwrite]
"""
NAME = "export_tools"
DESCRIPTION = "Export a fingerprint to JSON/CSV, back up / restore all fingerprints (commands: export, backup, restore)"
REQUIRES = []
ORDER = 30

import csv
import gzip
import io
import json
import os
import zipfile
from datetime import datetime

from fp.settings import BAN_FILE, EXT, PROFILE_DIR
from fp.store import profile_path, sanitize

BAN_NAME = os.path.basename(BAN_FILE)
MAX_MEMBER_BYTES = 64 * 1024 * 1024


class ExportTools:
    """Service \"export_tools\" (also used by ui_export)."""

    # ---- single fingerprint ----------------------------------------------------------------
    @staticmethod
    def read_raw(name):
        path = profile_path(sanitize(name))
        if not os.path.isfile(path):
            raise FileNotFoundError(f"No fingerprint named '{name}'.")
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)

    @staticmethod
    def summary(data, include_windows=False):
        out = {k: data.get(k) for k in ("name", "created", "updated", "seconds_recorded", "class", "stability", "id",
                                        "embedding", "key_slices", "keys", "summary") if k in data}
        out["window_count"] = len(data.get("windows") or [])
        if include_windows:
            out["feature_names"] = data.get("feature_names")
            out["windows"] = data.get("windows")
            if data.get("window_tags"):
                out["window_tags"] = data["window_tags"]
        return out

    @staticmethod
    def windows_csv(data):
        names = list(data.get("feature_names") or [])
        tags = data.get("window_tags") or []
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["window"] + names + (["tag"] if tags else []))
        for i, row in enumerate(data.get("windows") or []):
            w.writerow([i + 1] + ["" if v is None else v for v in row] + ([tags[i] if i < len(tags) else ""] if tags else []))
        return buf.getvalue()

    def export_profile(self, name, out_path, fmt="json", include_windows=False):
        data = self.read_raw(name)
        if fmt == "csv":
            text = self.windows_csv(data)
        elif fmt == "json":
            text = json.dumps(self.summary(data, include_windows), indent=1)
        else:
            raise ValueError("format must be 'json' or 'csv'")
        with open(out_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return out_path

    # ---- backup / restore ------------------------------------------------------------------
    @staticmethod
    def default_backup_name():
        return f"fingerprint-backup-{datetime.now():%Y%m%d-%H%M%S}.zip"

    def make_backup(self, out_path=None):
        """Zip every .fpr (and the ban list). -> (path, number of files)"""
        out_path = out_path or os.path.join(os.getcwd(), self.default_backup_name())
        files = []
        if os.path.isdir(PROFILE_DIR):
            files = [f for f in sorted(os.listdir(PROFILE_DIR)) if f.endswith(EXT) or f == BAN_NAME]
        if not files:
            raise FileNotFoundError("There are no fingerprints to back up.")
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                zf.write(os.path.join(PROFILE_DIR, f), f)
        return out_path, len(files)

    @staticmethod
    def restore_backup(zip_path, overwrite=False):
        """Restore a backup made by make_backup. -> (restored, skipped_existing, rejected)"""
        restored = skipped = rejected = 0
        os.makedirs(PROFILE_DIR, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                name = info.filename
                flat = name == os.path.basename(name) and name not in ("", ".", "..")   # no folders / path tricks
                wanted = name.endswith(EXT) or name == BAN_NAME
                if info.is_dir() or not flat or not wanted or info.file_size > MAX_MEMBER_BYTES:
                    rejected += 1
                    continue
                data = zf.read(info)
                try:
                    if name == BAN_NAME:
                        json.loads(data.decode("utf-8"))
                    elif data[:2] != b"\x1f\x8b":            # a .fpr is gzip
                        raise ValueError("not a fingerprint file")
                except ValueError:
                    rejected += 1
                    continue
                target = os.path.join(PROFILE_DIR, name)
                if os.path.exists(target) and not overwrite:
                    skipped += 1
                    continue
                with open(target, "wb") as fh:
                    fh.write(data)
                restored += 1
        return restored, skipped, rejected


# ---- commands --------------------------------------------------------------------------------
def _cmd_export(ctx, args):
    tools = ctx.get("export_tools")
    out = args.out or os.path.join(os.getcwd(), f"{sanitize(args.name)}.{args.format}")
    try:
        tools.export_profile(args.name, out, args.format, args.windows)
    except (OSError, ValueError) as e:
        print(f"Export failed: {e}")
        return 1
    print(f"Exported '{args.name}' -> {out}")
    return 0


def _cmd_backup(ctx, args):
    try:
        path, n = ctx.get("export_tools").make_backup(args.out)
    except OSError as e:
        print(f"Backup failed: {e}")
        return 1
    print(f"Backed up {n} file(s) -> {path}")
    return 0


def _cmd_restore(ctx, args):
    try:
        restored, skipped, rejected = ctx.get("export_tools").restore_backup(args.file, args.overwrite)
    except (OSError, zipfile.BadZipFile) as e:
        print(f"Restore failed: {e}")
        return 1
    print(f"Restored {restored}, skipped {skipped} existing (use --overwrite), ignored {rejected} unrelated/invalid file(s).")
    return 0


def register(ctx):
    ctx.provide("export_tools", ExportTools())
    ctx.add_command("export", _cmd_export, help="export one fingerprint to JSON or CSV",
                    setup=lambda p: (p.add_argument("name"), p.add_argument("--format", choices=["json", "csv"], default="json"),
                                     p.add_argument("--windows", action="store_true", help="json: include every window"),
                                     p.add_argument("--out", help="output file")))
    ctx.add_command("backup", _cmd_backup, help="zip all fingerprints",
                    setup=lambda p: p.add_argument("--out", help="output .zip"))
    ctx.add_command("restore", _cmd_restore, help="restore fingerprints from a backup .zip",
                    setup=lambda p: (p.add_argument("file"), p.add_argument("--overwrite", action="store_true")))
