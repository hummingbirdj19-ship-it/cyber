# Chain of Custody

Tamper-evident logging that plugs into an existing platform with a sidecar and one config file. Hackurity 2026, problem statement 6. Base model.

## Idea in three lines
1. The sidecar reads what the platform already writes (app logs, database changes, web server log) and chains it with SHA-256.
2. Signed checkpoints and 30-second heartbeats go to a witness on another machine that the attacker cannot edit.
3. A verifier compares the local log with the witness and names the attack: edit, deletion, reorder, truncation or fork.

## Read in this order
1. CONTRACT.md  (interfaces, the one thing both sides share)
2. DEFENCE_STEPS.md (Jeev) or PLATFORM_STEPS.md (Jaganath)
3. AGENTS.md and CLAUDE.md are for the coding agent. You do not need to read them.

## Using this with Antigravity or Claude
Open this folder as the workspace. Start a chat with:

> Read AGENTS.md, CLAUDE.md, CONTRACT.md and DEFENCE_STEPS.md (or PLATFORM_STEPS.md). We are on Step N. Explain what this step needs and why, then wait for me to write it.

When stuck, paste: the step number, the exact error, and what you expected.

## Quickstart (filled in as steps are finished)
- Run the defence locally: see DEFENCE_STEPS.md, Step 3 and Step 7
- Run the platform: see PLATFORM_STEPS.md, Step 0
- Run both together: see PLATFORM_STEPS.md, Step 8
