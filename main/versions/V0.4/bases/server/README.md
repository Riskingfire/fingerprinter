# Server base (V0.4) - the anti-cheat viewer

Connects to the same server the client and game bases use and shows what it holds. Standard library only
(Tk + urllib): no numpy, no pynput, runs on any staff PC.

```
fingerprinter_server.py    python fingerprinter_server.py            the viewer window
fpserver/                  config.py (server_config.json)  api.py (REST client)  gui.py (window)  cli.py (commands)
```

**Window:** Players (fingerprints on the server, BANNED marks, header of the selected one, download) - Bans (add / unban) -
Analysis (runs the server's ban-evasion analysis, filter review / high / all, export CSV or JSON, ban after review).

**Commands** (no window needed): `status`, `players`, `show NAME`, `bans`, `ban NAME --reason ...`, `unban NAME`,
`analyze [--level review|high|all] [--json] [--csv FILE]`, `download NAME [-o FILE]`.
Global options `--url`, `--key`, `--config`.

**Connection:** the same `server_config.json` as the client (`{"url": "...", "key": "..."}`). Lookup order: `--config`,
`FP_SERVER_CONFIG`, next to the launcher, then `%USERPROFILE%\AppData\fingerprint\server_config.json` (the client's file) -
so if the client is already set up on that PC there is nothing to enter. Only `https://` (or localhost) is accepted so the
key never travels in clear text. A sleeping Render instance is woken first (up to ~2 minutes).

**Use it as a lead generator, not a judge.** Flags mean "worth a human look"; shared PCs and siblings can look alike.
The key you enter here can ban people, so keep it on staff machines only.

It uses these endpoints (nothing else): `GET /health, /api/status, /api/fingerprints, /api/fingerprints/<name>, /api/banlist`,
`PUT|DELETE /api/banlist/<name>`, `POST /api/analysis`.
