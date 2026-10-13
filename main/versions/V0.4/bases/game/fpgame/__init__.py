"""Play Fingerprint - GAME base (V0.4): headless, no UI. Fingerprints the player only while the game window has focus.

    from fpgame import GameFingerprinter, FocusGate, WindowMatch
See session.py for the full description and README.md for the runner (python fingerprinter_game.py).
"""
from .focus import FocusGate, WindowMatch, foreground
from .session import GameFingerprinter

__version__ = "0.4"
__all__ = ["GameFingerprinter", "FocusGate", "WindowMatch", "foreground"]
