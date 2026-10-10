# Project: Chain of Custody (tamper-evident logging, BASE MODEL)

## Goal
Hackurity 2026, problem statement 6, 7-day build. A standalone, platform-agnostic tamper-evident logging system. It sits over any existing platform through a sidecar and one profile (`config.toml`), with no change to the platform and no platform-specific code in the defence. Integration is the main selling point: the profile's collectors read the evidence the platform already leaves (log files, an audit table) and write RawEvents to a spool file (docs/CONTRACT.md sections 2 and 7), and the same defence code protects it. Integrating a platform means writing a profile, not code. If an attacker with admin access edits, deletes, reorders or truncates the logs, our verifier detects it and names the attack type. This file covers the BASE MODEL only. Improvements are listed under "Out of scope" and come later.

## Tech Stack
- Language: Python 3.11+
- Witness service: FastAPI + uvicorn; heartbeat client: httpx
- Storage: SQLite (stdlib sqlite3) for the sidecar log and the witness store
- Crypto: SHA-256 (hashlib) and Ed25519 (the `cryptography` package). Never write custom crypto.
- Tests: pytest
- Packaging: Docker Compose. Docker only for the hackathon (no pip package); the `coc` wrapper script (`coc.ps1` / `coc.sh`) runs the CLI inside the image.
- Setup and checks: the `coc` CLI (`coc init`, `coc check`, later `coc collect`)
- Demo platform: a standalone bank-style platform (JP Morgan-like clone) in its own repo. It is only the first integration target; nothing in this repo depends on it.

## Repos and Ownership
- This repo (defence): sidecar/, witness/, verifier/, collectors/, coc/, tests/, tools/. Owner: Jeev. Collectors (generic, profile-configured) live here since CONTRACT v0.3.
- Platform repo (separate, standalone): the demo platform. It leaves evidence the collectors read (logs, an audit table); it writes its own spool adapters only if no collector fits. Owner: Jaganath.
- Red team (third person): attack scripts, later phase
- Shared: docs/CONTRACT.md only. The master copy lives here; platform repos pin a version. Change protocol is inside it.
- Integrating a new platform means writing a new profile (with `[[collectors]]`). Platform-side adapters only when no collector fits. It never means changing defence code.

## Project Structure
chain-of-custody/
├── AGENTS.md
├── CLAUDE.md
├── .cursorrules
├── README.md
├── docs/
│   ├── CONTRACT.md          # the interface any platform follows (read this first)
│   ├── DEFENCE_STEPS.md     # build flow
│   ├── INTEGRATION.md       # how any platform plugs in (adapters + profile)
│   ├── HANDOFF.md           # state between agents, red-team gates, findings log
│   └── red-team/            # full red-team reports
├── config.example.toml      # profile template
├── witness_nodes.example.toml
├── defence-compose.yml
├── .antigravity/tasks.md
├── samples/events.sample.jsonl
├── spool/                   # runtime: events.jsonl written by platform adapters (gitignored)
├── data/                    # runtime: sqlite files and keys (gitignored)
├── sidecar/                 # canon.py store.py ingest.py merkle.py keys.py checkpoint.py heartbeat.py main.py
├── witness/                 # app.py checks.py store.py
├── coc/                     # CLI: cli.py init.py check.py profile.py (profile schema shared with the sidecar)
├── collectors/              # generic collectors: json_lines, access_log, db_audit (step I3)
├── verifier/                # __main__.py checks.py
├── tools/                   # gen_events.py validate_spool.py
└── tests/

## Coding Conventions
- snake_case for functions and files, type hints on public functions, a one-line docstring on every function
- No secrets, keys or real personal data in the repo. All platform data is synthetic.
- Timestamps are UTC ISO 8601 with a trailing Z.
- Hash and signature inputs always use the canonical JSON defined in CONTRACT.md.
- One responsibility per module. Keep modules under about 200 lines.
- Platform-agnostic: no platform names, table names, URLs or event-type special cases in code. Anything platform-dependent goes in the profile (`config.toml`).

## Agent Behaviour Rules
- Read docs/CONTRACT.md before touching anything that crosses the sidecar/platform boundary.
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
