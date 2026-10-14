# Game base (V0.4) - fingerprint the player from inside the game client

Headless: no window, no UI. It fingerprints the player **only while the game window has focus**, saves the
`.fpr` locally and uploads it to the same server the client uses (the one the server viewer reads).

```
fingerprinter_game.py      runner:  python fingerprinter_game.py --player ID --title "My Game" --process mygame.exe
game_config.example.json   copy to game_config.json (command-line options override it)
fpgame/                    the package (import it from your own launcher instead, if you prefer)
  focus.py      FocusGate + WindowMatch   is the game in front?       (Windows / macOS / Linux-X11, stdlib only)
  recorder.py   hooks exist only while focused
  session.py    GameFingerprinter         windows -> profile -> save -> upload
  upload.py     GET /health, PUT /api/fingerprints/<name>
  settings / features / classify / embedding / keybinds / profile / store / context
                verbatim copies of the client's core, so the .fpr files are identical to the client's
```
Needs `pip install pynput numpy`.

## How the focus gate works
* **Focused:** mouse and keyboard hooks are installed and a 15 s window is analysed every 7.5 s.
* **Not focused:** the hooks are **removed** and everything buffered is thrown away. Nothing is observed.
* A window is kept only if the game had focus for the **whole** of it (the first one comes 15 s after focus returns),
  and the player was not idle (same AFK rule as the client).
* Fails closed: if the foreground window cannot be read (no `xdotool`/`xprop` on Linux, Wayland, an error...) it counts as
  "not focused" and records nothing. The runner prints a warning.

Tell it which window is the game with `--title` (part of the window title), `--process` (e.g. `mygame.exe`) and/or `--pid`.
Not sure what to use? `python fingerprinter_game.py --probe` prints the window in front, once a second.

## Inside your own game process
```python
from fpgame import GameFingerprinter, FocusGate, WindowMatch
fp = GameFingerprinter("player123", focus=FocusGate(WindowMatch(titles=["My Game"])),
                       server={"url": "https://....onrender.com", "key": "..."})
fp.start()                      # background thread
fp.set_context(tag="ranked")    # optional: tag the game state, or set_context(afk=True) in menus / spectating
fp.stop()                       # removes hooks, saves, uploads
```
No window matcher? `fp = GameFingerprinter("player123")` and call `fp.set_focused(True/False)` from your engine's focus event.

## Files
Fingerprints go to `%USERPROFILE%\AppData\fingerprint_game\fingerprints\<player>.fpr` (or `data_dir`). An existing file for the
same player is loaded and refined, so the fingerprint grows across sessions. Saves happen after the first window, every
5 minutes, whenever focus is lost and on exit; uploads at most every 15 minutes and only when the file changed
(a file that could not be uploaded is retried next run).

## Please read
* **The API key.** ~~The server has one key.~~ [Updated server](https://github.com/Riskingfire/fingerprintserver) Has multiple keys with different permissions Anything that holds it can read and change the ban list, so do not ship it in a
  game build. Upload through your own backend, or put an upload-only key on the server.
* **Tell players.** It records mouse and keyboard timing and (unless `track_keys` is false / `--no-keys`) which keys are used,
  including keys pressed while typing in game chat. Mention it in your privacy notice.
