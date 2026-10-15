# Fingerprinter Wiki

Welcome to the Fingerprinter wiki for the `Riskingfire/fingerprinter` repository.

This project is a behavioral fingerprinting toolkit designed to identify suspicious account reuse or shared-device behavior in games and anti-cheat workflows. It records mouse and keyboard behavior, builds player profiles, compares them against saved fingerprints, and gives operators a way to review matches with human judgment.

## Project at a glance

- Purpose: detect suspicious account reuse or shared-device behavior
- Main language: Python (with some C++ legacy components)
- Current architecture: `main/versions/V0.4`
- Core components:
  - Client runtime
  - Game-side recorder
  - Server viewer
  - Modular extension system
- Safety note: matches are evidence for review, not automatic proof of cheating

## Repository layout

```text
.
├── CHANGELOG.md
├── LICENSE
├── README.md
├── app.ico
├── installers/
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
│   ├── cpp/
│   └── python/
└── wiki/
```

## Start here

- [Getting started](Getting-Started.md)
- [Architecture overview](Architecture.md)
- [Modules and extensibility](Modules-and-Extensibility.md)
- [Game and server workflow](Game-and-Server-Workflow.md)
- [Privacy and ethics](Privacy-and-Ethics.md)

## Quick facts

### Current implementation
The active project architecture is centered in:

```text
main/versions/V0.4/
```

This folder contains the current base + modules pattern used across the client, game, and server components.

### Build types
- `main/versions/V0.4` — current modular Python implementation
- `standalones/python` — older standalone Python versions
- `standalones/cpp` — legacy C++ standalone build
- `installers/` — packaging and installer assets

## Important principle

Fingerprinter is intended as a decision-support tool. It helps build a behavioral profile and highlights suspicious similarity, but it does not replace human review. The repo explicitly discourages using a single match as definitive evidence for a ban or punitive action.

## Related docs

- [README.md](../README.md)
- [Client README](../main/versions/V0.4/bases/client/README.md)
- [Game README](../main/versions/V0.4/bases/game/README.md)
- [Server docs in the project structure](../main/versions/V0.4/bases/server/README.md)

---

This wiki is meant to reflect the repository as it exists today, with emphasis on the `V0.4` modular architecture and its designed-safe operational model.
