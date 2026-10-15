# Modules and Extensibility

The repository is built around a plugin-like module system in the current `V0.4` runtime. This is one of the main architectural strengths of the project.

## Core principle

The client base is intended to run without direct modification. Instead, functionality is added via module files discovered by the loader.

The base exposes an API that modules can hook into through `register(ctx)`.

## Module discovery

The module loader scans for modules in:

- the installed module directory next to the executable/app runtime
- a sibling `modules/` directory when running from the repository checkout
- optional custom directories via launch configuration or environment variables

This makes it possible to add modules without editing the core launcher or base code.

## Module responsibilities

Modules in the repo cover several categories:

### UI modules
Examples include:

- `ui_tk.py`
- `ui_detect.py`
- `ui_class.py`
- `ui_compare.py`
- `ui_server.py`
- `ui_library.py`
- `ui_modules.py`
- `ui_history.py`
- `ui_notes.py`
- `ui_export.py`
- `ui_zoom.py`

These expose tab content, dialogs, header buttons, and related workflow tools.

### Data and integration modules
Examples include:

- `integrations.py` — sanctioned game-state inputs and AFK/context rules
- `server_sync.py` — server synchronization support
- `session_log.py` — session history and commands
- `export_tools.py` — backup, restore, and export helpers

### Support files
Examples include:

- `modules.json` — install component list / visibility controls
- `_template_module.py` — starter code for custom modules

## Register API

A module can declare a `register(ctx)` function that integrates with the host app. The project docs mention the following extension points:

- hooks such as `train.window`, `detect.update`, `profile.saved`, `session.start/stop`
- context providers for `context.sample` and `context.nums`
- services exposed through `ctx.provide(...)`
- UI extension points for tabs and header buttons
- CLI commands registered via `ctx.add_command(...)`

This architecture allows modules to be added without needing to patch the core code.

## Loading rules

The repo docs describe behavior like:

- modules can be enabled or disabled by policy
- missing dependencies can skip a module
- broken imports or runtime failures are ignored rather than crashing the entire app

This makes the system robust when experimenting with optional features.

## Custom module workflow

A typical custom module is created by:

1. copying the template from `_template_module.py`
2. placing it in the `modules/` directory
3. exposing a `register(ctx)` function
4. using extension hooks and services to integrate with the app

## Why it matters

The modularity is central to the design philosophy:

- core fingerprinting logic stays isolated
- UI, integrations, and tools remain optional
- features can be run, disabled, or replaced without editing the base

## Related docs

- [Architecture overview](Architecture.md)
- [Getting started](Getting-Started.md)
- [Game and server workflow](Game-and-Server-Workflow.md)
