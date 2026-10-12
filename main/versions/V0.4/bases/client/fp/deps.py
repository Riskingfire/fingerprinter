"""Friendly dependency check for the launcher (runs before anything heavy is imported)."""
import sys

NEEDED = "pip install pynput numpy"


def check():
    """-> None if numpy and pynput import fine, else an error message string."""
    missing = None
    try:
        import numpy  # noqa: F401
    except ImportError as e:
        missing = e.name or "numpy"
    if missing is None:
        try:
            import pynput  # noqa: F401
        except ImportError as e:
            missing = e.name or "pynput"
        except Exception as e:                       # e.g. no display server on Linux
            return f"pynput could not start: {type(e).__name__}: {e}"
    if missing:
        return f"Missing dependency: {missing}\n\nInstall with:\n    {NEEDED}"
    return None


def fail_with_dialog(msg):
    """Print the message and, if tkinter happens to exist, show it in a dialog too. Exits."""
    print(msg)
    try:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror("Missing dependency", msg)
    except Exception:
        pass
    sys.exit(1)
