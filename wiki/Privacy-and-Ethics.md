# Privacy and Ethics

This repository is explicitly built around a review-oriented, evidence-first philosophy. That matters for both technical and operational use.

## Core principle

Fingerprinter is not marketed as a definitive cheating detector. Its purpose is to collect and compare behavioral evidence so a human operator can decide whether a match is suspicious.

## Important warnings from the project

The project documentation states that:

- similar fingerprints do not prove shared identity
- a match is not proof of cheating
- automatic enforcement should not rely on a single match alone
- operator judgment remains necessary

This is a strong ethical guardrail in the repo's design.

## What it captures

The project captures behavior such as:

- mouse movement timings
- keyboard timing and usage patterns
- game-focused session behavior
- contextual tags and operating conditions

This is behavioral data, not direct process memory inspection or hidden invasive monitoring.

## Safe integration pattern

The `integrations` module is designed to focus on approved data sources only, such as:

- official game state or telemetry
- documented APIs or exported logs
- local, authorized config files

It explicitly avoids:

- scraping screen content
- reading process memory
- injecting code into protected components
- using anti-cheat-protected sources without authorization

## Data handling notes

The repository stores fingerprint data under user-specific directories such as:

- `%USERPROFILE%\AppData\fingerprint`
- `%USERPROFILE%\AppData\fingerprint_game`

This design keeps the data local to the user context and supports review-oriented workflows.

## Operational guidance

If this project is used in an actual anti-cheat environment, the safest pattern is:

- treat matches as investigative signals
- require human review
- log the reasoning behind each action
- avoid using a single fingerprint score as final evidence
- communicate clearly to affected players about what the system records

## Ethics summary

Responsible use of Fingerprinter means treating it as a behavioral risk signal, not as deterministic proof. The repository's design and documentation both encourage a cautious, human-reviewed workflow.

## Related docs

- [Getting started](Getting-Started.md)
- [Game and server workflow](Game-and-Server-Workflow.md)
- [Architecture overview](Architecture.md)
