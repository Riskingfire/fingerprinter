# Fingerprinter — Changelog

This document records the release notes for the Python and C++ builds of Fingerprinter. The implementation language is stated for each release because the Python application is packaged as a Windows `.exe`, while some legacy releases also include separate C++ builds.

## v0.4 — Python

**Release type:** Python application with a modular client + server + game architecture

### Added

- Modular base-plus-modules design for the client runtime, allowing features to be added or removed without editing the base code.
- A structured `V0.4` application layout under `main/versions/V0.4` with separate `bases/client`, `bases/game`, and `bases/server` runtime packages.
- Client-side module loading via a `register(ctx)` pattern, with optional UI, export, session history, server-sync, and integration capabilities.
- Headless game-side fingerprinting that records mouse and keyboard behavior only while the target game window has focus.
- In-game state integrations using sanctioned data sources only (official game telemetry, local SDK/API output, logs, or exported config data).
- AFK detection and context-tag features derived from game state, allowing input windows to be filtered or annotated based on in-game context.
- Server-side viewer commands for status, player inspection, ban management, and analysis.

### Improved

- Stronger emphasis on backward compatibility for saved fingerprint data and application-data layout.
- A cleaner separation between the base runtime and feature modules, without forcing all functionality into a monolithic single-file application.
- More explicit support for training, detecting, and reviewing fingerprint windows with optional modules instead of making everything built-in.
- Improved safety boundaries around integrations: raw payloads are kept in memory only, and only derived tags / numeric context are persisted.

### Data and packaging

- The client and server use persistent app-data under `%USERPROFILE%\AppData\fingerprint`.
- The game base stores game fingerprints separately under `%USERPROFILE%\AppData\fingerprint_game`.
- Module discovery works from the installed folder or from a repo checkout without manual configuration, supporting a flexible deployment model.

### Interpretation and limitations

- Fingerprints remain behavioral evidence, not definitive proof of identity.
- Similarity and integrity findings should be reviewed by a human before taking action.
- A single match or integrity flag should not be treated as conclusive evidence of cheating.

---

## v0.3 — Python

**Release type:** Python application packaged as a Windows `.exe`

### Added

- Integrity status in Detect mode: `CLEAR`, `REVIEW`, or `HIGH`, with supporting evidence.
- Checks for suspicious fixed-period timing and replay-like input sequences.
- Session evidence reporting to make integrity findings easier to review.
- Optional mouse-device cadence signatures stored with fingerprint profiles.
- Duplicate-looking profile search for identifying groups of unusually similar fingerprints without needing a ban list.
- Training-integrity checks for saved profiles.

### Improved

- Right-click behavior analysis now includes both the share of time the button is held and the rate of right-click presses.
- Fingerprint class coding uses more general pointer/right-click terminology instead of assuming the behavior represents aiming or ADS.
- Integrity checks provide context for manual review rather than automatically treating a flag as a verdict.

### Data and packaging

- Distributed as a Windows `.exe` built from Python.
- Local application data remains separate from the executable under `%USERPROFILE%\AppData\fingerprint`.

### Interpretation and limitations

- Integrity flags are evidence for human review, not proof of cheating.
- Similar fingerprints do not, by themselves, prove that two accounts belong to the same person.
- A fingerprint match should not be used as the sole basis for an automatic ban.

---

## v0.2 — C++

**Release type:** C++ Windows executable

### Release notes

- C++ implementation distributed as a Windows `.exe`.
- Application data is kept separately from the executable so it can persist between launches.
- Fingerprint profiles and ban-list data are maintained as separate application data.

### Build distinction

This is the C++ implementation. It is separate from the Python version of v0.2.

---

## v0.2 — Python

**Release type:** Python application packaged as a Windows `.exe`

### Release notes

- Python implementation packaged as a Windows executable, so users can launch the application without running the Python source manually.
- Persistent application data is stored under `%USERPROFILE%\AppData\fingerprint`, separately from the executable.
- Fingerprint profiles are saved between launches.
- Ban-list data is stored separately from the executable.
- Supports training or refining behavioral fingerprints from recorded keyboard and mouse input and comparing live input against selected profiles.

### Build distinction

This is the Python implementation packaged into an `.exe`; it is not the C++ build.

---

## General notes

- Fingerprints describe measured input behavior. They are not definitive proof of a player's identity.
- Similarity and integrity results should be interpreted with context and reviewed by a person.
- Do not treat a single match or integrity flag as conclusive evidence of cheating.
