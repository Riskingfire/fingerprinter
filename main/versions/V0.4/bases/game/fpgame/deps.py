"""Friendly dependency check (numpy + pynput) before the heavy imports."""


def check():
    """-> None if everything imports, else a message."""
    try:
        import numpy  # noqa: F401
    except ImportError:
        return "Missing dependency: numpy\n\nInstall with:\n    pip install pynput numpy"
    try:
        import pynput  # noqa: F401
    except ImportError:
        return "Missing dependency: pynput\n\nInstall with:\n    pip install pynput numpy"
    except Exception as e:                       # e.g. no display server on Linux
        return f"pynput could not start: {type(e).__name__}: {e}"
    return None
