# Game and Server Workflow

This page explains the runtime flow between the game-side recorder and the server-side review tools.

## 1. Game-side fingerprinting

The game base is designed to run in a headless manner while the actual game is active.

Location:

```text
main/versions/V0.4/bases/game/
```

It only captures input while:

- the game window is the active foreground window
- the focus gate confirms that the game is in focus
- the player is not idle or considered AFK according to the app rules

This is designed to avoid collecting noise from unrelated desktop activity.

### Gameplay behavior

The game base can:

- start a background fingerprinting thread
- observe mouse and keyboard behavior in real time
- save `.fpr` files locally
- optionally upload the data to a server backend

It supports commands like:

```bash
python fingerprinter_game.py --player ID --title "My Game" --process mygame.exe
```

## 2. Local fingerprint storage

The project stores app data in user-specific directories:

- client/server: `%USERPROFILE%\AppData\fingerprint`
- game: `%USERPROFILE%\AppData\fingerprint_game`

Existing fingerprint files are loaded and refined across sessions, so a player profile grows over time rather than being replaced every run.

## 3. Client-side analysis

The client base is the main interface used by operators and users to:

- train new profiles
- compare live behavior against prior profiles
- review suspicious matches
- inspect class or similarity data

This acts as the primary local investigative and training interface.

## 4. Server-side review

The server component is intended for review workflows and aggregated analysis.

It helps staff:

- inspect stored fingerprints
- review suspicious matches
- manage bans or reports based on operator judgment
- track the server backend used by the client and game base

This makes the project more than a single-user tool; it is designed to integrate into shared moderation workflows.

## 5. Evidence, not accusation

The repo makes a strong emphasis on the principle that a match is evidence for review, not final proof of cheating.

This means:

- similar fingerprints suggest risk
- a match must be reviewed by a human
- a profile should not be the sole basis for an automatic ban

## 6. Safe data sources

The integrations module is designed to rely on sanctioned data sources, such as:

- official game state or telemetry APIs
- documented logs
- exported config or local authorized data

It avoids reading memory or invasive actions that would violate fair-play or privacy boundaries.

## Summary

The workflow is:

1. collect behavioral data while the game is active
2. save and refine a local fingerprint profile
3. optionally synchronize with a server
4. review suspicious similarity with a human operator
5. use the result as investigative evidence, not automatic punishment

## Related docs

- [Architecture overview](Architecture.md)
- [Privacy and ethics](Privacy-and-Ethics.md)
- [Modules and extensibility](Modules-and-Extensibility.md)
