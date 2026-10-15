# Architecture Overview

This project is structured around a modular architecture, with the active implementation centered in:

```text
main/versions/V0.4/
```

The design separates the base runtime from optional modules so the system can be customized without editing the core application.

## High-level structure

```text
.
├── main/
│   └── versions/
│       └── V0.4/
│           ├── bases/
│           │   ├── client/
│           │   ├── game/
│           │   └── server/
│           ├── modules/
│           └── tools/
├── standalones/
│   ├── python/
│   └── cpp/
├── installers/
└── README.md
```

## Client base

Location:

```text
main/versions/V0.4/bases/client/
```

This is the main entry point for the active app. It hosts the user-facing runtime and is designed to be split into:

- a stable base runtime
- optional modules added or removed without editing the base

The client supports:

- training a fingerprint profile
- detecting live behavior against saved profiles
- running optional modules for integrations, UI, export, and session history

### Core concept
The project uses a `register(ctx)` plugin architecture. Modules can plug into the app by exposing hooks, services, commands, and UI components without modifying the base.

## Game base

Location:

```text
main/versions/V0.4/bases/game/
```

This is a headless implementation meant to run while the actual game window is focused. It captures input only while the game is active and saves or uploads fingerprint data.

### Design goals
- no visible window
- focus-gated observation
- save locally to a fingerprint file
- optionally upload to a backend server

### Key behavior
The game base only records while the target window is in focus. If focus is lost or the app cannot verify the active window, it falls back to a fail-closed design and records nothing.

## Server base

Location:

```text
main/versions/V0.4/bases/server/
```

The server-side portion is used for review and operational workflows. It allows staff to inspect stored fingerprints and review suspicious matches.

Typical responsibilities include:

- reviewing profiles and matches
- assessing suspicious account behavior
- reading backend state from the same server used by the client/game components
- tracking bans or reports based on manual review

## Modules layer

Location:

```text
main/versions/V0.4/modules/
```

The modules directory contains optional features such as:

- `integrations.py` for sanctioned game-state sources and AFK/context rules
- `server_sync.py` for server synchronization flows
- `session_log.py` for training/detect history
- `export_tools.py` for backup and export helpers
- UI modules like `ui_tk.py`, `ui_detect.py`, `ui_compare.py`, `ui_library.py`, `ui_modules.py`, and related files

This design intentionally keeps extension concerns outside the base runtime so the system can evolve without invasive changes.

## Backward compatibility

The README emphasizes compatibility with:

- existing fingerprint file formats
- app-data directory layout
- prior saved data and app behaviors

The project is designed to keep old data readable while evolving the runtime architecture.

## Why this architecture matters

This structure supports a modular anti-cheat workflow:

- collect input behavior
- compare it against stored profiles
- flag suspicious similarity
- surface evidence for human review

That is the architectural theme throughout the repository.

## Related pages

- [Getting started](Getting-Started.md)
- [Modules and extensibility](Modules-and-Extensibility.md)
- [Game and server workflow](Game-and-Server-Workflow.md)
