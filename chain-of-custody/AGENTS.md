# Project: Chain of Custody (tamper-evident logging, BASE MODEL)

## Goal
Hackurity 2026, problem statement 6, 7-day build. A tamper-evident logging system that plugs into an existing platform (Firefly III, an open-source finance app) through a sidecar and a config file, with no change to the app. If an attacker with admin access edits, deletes, reorders or truncates the logs, our verifier detects it and names the attack type. This file covers the BASE MODEL only. Improvements are listed under "Out of scope" and come later.

## Tech Stack
- Language: Python 3.11+
- Witness service: FastAPI + uvicorn; heartbeat client: httpx
- Storage: SQLite (stdlib sqlite3) for the sidecar log and the witness store
- Crypto: SHA-256 (hashlib) and Ed25519 (the `cryptography` package). Never write custom crypto.
- Tests: pytest
- Packaging: Docker Compose
- Victim platform: Firefly III run unmodified (fallback: Formance Ledger)

## Roles and Ownership
- Jeev: sidecar/, witness/, verifier/, tests/, tools/
- Jaganath: victim/ (platform, fake data, adapters that write the spool file)
- Red team (third person): attack scripts, later phase
- Shared: CONTRACT.md. It is the only thing both sides depend on. Change protocol is inside it.
- Never edit the other person's folders. Work on separate branches: `defence` and `platform`.

## Project Structure
chain-of-custody/
├── AGENTS.md
├── CLAUDE.md
├── .cursorrules
├── README.md
├── CONTRACT.md              # interfaces both sides follow (read this first)
├── DEFENCE_STEPS.md         # Jeev's build flow
├── PLATFORM_STEPS.md        # Jaganath's build flow
├── config.example.toml
├── .antigravity/tasks.md
├── samples/events.sample.jsonl
├── spool/                   # runtime: events.jsonl written by adapters (gitignored)
├── data/                    # runtime: sqlite files and keys (gitignored)
├── sidecar/                 # canon.py store.py ingest.py merkle.py keys.py checkpoint.py heartbeat.py main.py
├── witness/                 # app.py checks.py store.py
├── verifier/                # __main__.py checks.py
├── tools/                   # gen_events.py validate_spool.py
├── tests/
└── victim/                  # docker-compose.yml, seed/, adapters/, scripts/, NOTES.md

## Coding Conventions
- snake_case for functions and files, type hints on public functions, a one-line docstring on every function
- No secrets, keys or real personal data in the repo. All platform data is synthetic.
- Timestamps are UTC ISO 8601 with a trailing Z.
- Hash and signature inputs always use the canonical JSON defined in CONTRACT.md.
- One responsibility per module. Keep modules under about 200 lines.

## Agent Behaviour Rules
- Read CONTRACT.md before touching anything that crosses the sidecar/platform boundary.
- Work on one step at a time from the step file. State which step you are on.
- Plan before writing. Run the tests for the step before calling it done.
- Do not delete files without asking. Do not change CONTRACT.md without the change protocol.
- Prefer simple, readable code over clever code.
- When something fails, explain the cause first, then the options.

## Out of Scope (do NOT build in the base model)
- Forward-secure key ratchet and per-entry MAC
- External anchors (timestamp authority, public transparency log)
- Tamper forensics report beyond the plain verdict list
- Erasure / redaction support
- Dashboard or web UI for the verifier
- Multi-node support, log rotation handling, performance tuning
- "No log, no run" interception
