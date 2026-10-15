# Getting Started

This page covers the quickest way to understand how to run and inspect the project.

## What this project does

Fingerprinter collects and compares input behavior such as:

- mouse timing and movement patterns
- keyboard timing and key usage
- session-based behavioral signatures
- suspicious similarity between accounts or devices

The tool is designed to support anti-cheat and moderation review workflows by identifying risk signals that should be manually assessed.

## Repository entry points

The project is organized around the current base in:

```text
main/versions/V0.4/
```

The repo contains three major runtime domains:

1. Client base
   - UI and player-facing workflow
   - profile training and live detection
   - local fingerprint data

2. Game base
   - headless recorder while the game is focused
   - saving `.fpr` files and optional upload

3. Server base
   - staff-side review interface and backend sync
   - inspection of saved fingerprints and suspicious matches

## Quick start

### Run the current client

```bash
cd main/versions/V0.4/bases/client
python fingerprinter.py
```

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

## Prerequisites

The repository includes Python-based components and a few runtime dependencies. Based on the project docs:

- Python 3.x
- `pynput` for game-side input capture
- `numpy` for analysis and computation
- optional modules may require more dependencies depending on the selected build

## Where data is stored

The project uses per-user app-data storage:

- Client/server data: `%USERPROFILE%\AppData\fingerprint`
- Game base data: `%USERPROFILE%\AppData\fingerprint_game`

This keeps fingerprint data separated from the game and server process states.

## Important caution

The project warns that matches are not conclusive proof of cheating. A high similarity score is an investigative signal, not a final verdict.

Proceed with caution if you plan to integrate it into moderation or enforcement flows.

## Next steps

- Read the [Architecture overview](Architecture.md)
- Learn about the [module system](Modules-and-Extensibility.md)
- Review the [privacy and ethics guidance](Privacy-and-Ethics.md)
