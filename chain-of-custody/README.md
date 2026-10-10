# Chain of Custody

Standalone, platform-agnostic tamper-evident logging. It sits over any existing platform with a sidecar and one profile file. No change to the platform and no platform-specific code. Hackurity 2026, problem statement 6. Base model.

## Idea in three lines
1. The platform's adapters write what it already records (app logs, database changes, web server log) as RawEvents to a spool file. The sidecar chains them with SHA-256.
2. Signed checkpoints and 30-second heartbeats go to a witness on another machine that the attacker cannot edit.
3. A verifier compares the local log with the witness and names the attack: edit, deletion, reorder, truncation or fork.

## Same code, different profile
One `config.toml` is one platform profile: node id, spool path and an optional event-type allow-list. Protecting a new platform means writing its adapters and a profile, never changing this code. See INTEGRATION.md.

## Read in this order
1. CONTRACT.md (the interface every platform follows)
2. INTEGRATION.md (how to plug a platform in)
3. DEFENCE_STEPS.md (how the defence is built)
4. AGENTS.md and CLAUDE.md are for the coding agent. You do not need to read them.

## Using this with Antigravity or Claude
Open this folder as the workspace. Start a chat with:

> Read AGENTS.md, CLAUDE.md, CONTRACT.md and DEFENCE_STEPS.md. We are on Step N. Explain what this step needs and why, then wait for me to write it.

When stuck, paste: the step number, the exact error, and what you expected.

## Quickstart (filled in as steps are finished)
- Set up: `python -m venv .venv`, `pip install -r requirements.txt`, copy `config.example.toml` to `config.toml`
- Run the defence locally: see DEFENCE_STEPS.md, Step 3 and Step 7
- Plug in a platform: see INTEGRATION.md

## Known behaviour
- After a legitimate full reset, the new chain looks like a fork to the witness. That is correct for the base model.
