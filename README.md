# Fingerprinter

A behavioral fingerprinting toolkit for identifying suspicious account reuse or shared-device behavior in games and anti-cheat workflows.

This repository contains a modular Python implementation for training and comparing player input fingerprints, a server-side viewer for reviewing suspicious matches, a game-side headless base for recording fingerprints in-game, and a small set of legacy C++ / Python standalone builds. The project is designed for Windows desktop use and emphasizes a privacy-conscious, opt-in workflow: it records timing and interaction patterns, then compares them across sessions rather than relying on identity or hardware IDs alone.

## What the project does

- Records mouse and keyboard behavior samples
- Trains a fingerprint profile from repeated gameplay sessions
- Compares live input against saved profiles
- Flags suspicious similarity or integrity issues for human review
- Supports an optional server that stores, compares, and bans or reports suspicious players
- Supports modular extension via `modules/` and a pluggable architecture

Important: fingerprint matches are evidence for review, not definitive proof of cheating. The repo explicitly warns against using a single match as conclusive evidence and recommends human review before action.

## Repository structure

```text
.
├── CHANGELOG.md                  # Release notes for Python and C++ builds
├── LICENSE.md                   # Project license
├── app.ico                      # App icon
├── installers/                  # Installer scripts / packaged variants
├── main/
│   └── versions/
│       └── V0.4/
│           ├── bases/
│           │   ├── client/        # Main Python client launcher and runtime
│           │   ├── game/          # Headless in-game fingerprinting base
│           │   └── server/        # Server-side viewer and management UI
│           ├── modules/          # Optional modules (UI, integrations, export, history, etc.)
│           └── tools/            # Support tooling, manifest generation
├── standalones/
│   ├── python/                 # Older Python standalone implementations
│   └── cpp/                    # Legacy C++ standalone build
└── README.md                   # This file
```

## Current architecture

The current implementation is centered on `main/versions/V0.4`.

### Client base
The client base lives under:

```text
main/versions/V0.4/bases/client/
```

This is the main app and is designed to be split into:

- a base runtime that can run by itself
- optional modules that can be added or removed without modifying the base

The README for the client base describes a modular loader that scans for modules next to the app and loads them through a `register(ctx)` API. This is a key architectural concept in the project.

### Game base
The game-side fingerprinting base is under:

```text
main/versions/V0.4/bases/game/
```

This is a headless implementation meant to run while the actual game window is in focus. It captures input only while the game is active, saves a fingerprint file locally, and optionally uploads it to a server using the same server backend the client uses.

### Server base
The server viewer is under:

```text
main/versions/V0.4/bases/server/
```

This is a staff-facing viewer used to inspect stored fingerprints, review bans, run analysis, and fetch player data from the same server backend used by the client and game bases.

### Modules
The modular extension layer is under:

```text
main/versions/V0.4/modules/
```

This includes modules such as:

- `integrations.py` for game-state inputs and AFK/context rules
- `session_log.py` for history and session recording
- `export_tools.py` for export/backup/restore commands
- `ui_tk.py`, `ui_detect.py`, `ui_class.py`, `ui_compare.py`, and related files for the desktop UI
- `ui_server.py`, `ui_library.py`, `ui_history.py`, `ui_modules.py`, etc.

The README states that the base and modules are intentionally decoupled: nothing in the core base has to be edited to add or remove functionality.

## Quick start

### Run the V0.4 client

From the repository root:

```bash
cd main/versions/V0.4/bases/client
python fingerprinter.py
```

This launches the application with the default module set.

### Run without modules

```bash
cd main/versions/V0.4/bases/client
python fingerprinter.py --no-modules list
```

### Train a profile from the command line

```bash
cd main/versions/V0.4/bases/client
python fingerprinter.py --no-modules train Me
```

### Run live detection

```bash
cd main/versions/V0.4/bases/client
python fingerprinter.py --no-modules detect
```

### Run the server viewer

```bash
cd main/versions/V0.4/bases/server
python fingerprinter_server.py
```

### Run the game-side base

```bash
cd main/versions/V0.4/bases/game
python fingerprinter_game.py --player ID --title "My Game" --process mygame.exe
```

## Important design notes

### Privacy and evidence
The repo clearly frames this as a tool for review rather than automatic enforcement:

- Similar fingerprints do not prove that two accounts belong to the same person.
- Integrity flags are evidence for manual review, not proof of cheating.
- A fingerprint match should not be used as the sole basis for an automatic ban.

### Safe handling of game state
The `integrations.py` module explicitly focuses on sanctioned data sources only such as:

- official game state or telemetry sources
- official APIs/SDKs
- documented log files
- locally exported game config data

It avoids reading process memory, injecting into game processes, scraping the screen, or touching anti-cheat-protected components.

### Data location
The client and server use app-data storage under `%USERPROFILE%\AppData\fingerprint` by default. The game base uses a separate folder, `%USERPROFILE%\AppData\fingerprint_game`.

## Versioning and compatibility

This repo contains multiple versions and historical builds:

- `main/versions/V0.4` — current modular implementation
- `standalones/python` — older single-file Python builds
- `standalones/cpp` — legacy C++ build
- `installers/` — packaging / installer files

The project README notes a strong emphasis on backward compatibility for saved fingerprint data and app-data layout, while still evolving the runtime architecture.

## Why this repository exists

The project is designed for anti-cheat operations where a player’s input behavior can be used as a signal to detect shared accounts, repeated behavior patterns, or suspicious similarity between sessions. It is not a general-purpose authentication system; it is a behavior-analysis and review tool intended to supplement broader anti-cheat systems and human investigation.

## License

This project is distributed under the license in `LICENSE.md`.

## Contributing

This repository is organized around versioned bases and optional modules. If you want to contribute:

1. Start with a specific versioned base (`main/versions/V0.4`)
2. Follow the modular extension pattern via `register(ctx)`
3. Keep compatibility in mind for app-data and fingerprint file formats
4. Preserve the project’s "evidence, not accusation" philosophy

## More context

For release history, see `CHANGELOG.md`.

For the deeper client architecture and module system, see:

- `main/versions/V0.4/bases/client/README.md`
- `main/versions/V0.4/bases/server/README.md`
- `main/versions/V0.4/bases/game/README.md`
- `main/versions/V0.4/modules/integrations.py`

These documents and modules provide the clearest introduction to how the system is intended to operate.
