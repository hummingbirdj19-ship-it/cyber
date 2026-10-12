# Chain of Custody

**Setup guide, demo script and integration reference: https://jeevikas-19.github.io/cybersec_hackathon/** (source: `site/index.html`)

Standalone, platform-agnostic tamper-evident logging. It sits over any existing platform with a sidecar and one profile file. No change to the platform and no platform-specific code. Hackurity 2026, problem statement 6. Base model.

## Idea in three lines
1. The platform's adapters write what it already records (app logs, database changes, web server log) as RawEvents to a spool file. The sidecar chains them with SHA-256.
2. Signed checkpoints and 30-second heartbeats go to a witness on another machine that the attacker cannot edit.
3. A verifier compares the local log with the witness and names the attack: edit, deletion, reorder, truncation or fork.

## Same code, different profile
One `config.toml` is one platform profile: node id, spool path and an optional event-type allow-list. Protecting a new platform means writing its adapters and a profile, never changing this code. See [docs/INTEGRATION.md](docs/INTEGRATION.md).

## Read in this order
1. [docs/CONTRACT.md](docs/CONTRACT.md) (the interface every platform follows)
2. [docs/INTEGRATION.md](docs/INTEGRATION.md) (how to plug a platform in)
3. [docs/DEFENCE_STEPS.md](docs/DEFENCE_STEPS.md) (how the defence is built)
4. [docs/HANDOFF.md](docs/HANDOFF.md) (current state, red-team gates, findings log, next plan)
5. AGENTS.md and CLAUDE.md are for the coding agent. You do not need to read them.

## Repo layout
```
chain-of-custody/
├── README.md, AGENTS.md, CLAUDE.md   # stay at the root: agents load them from here
├── docs/
│   ├── CONTRACT.md                   # the only interface with any platform
│   ├── INTEGRATION.md                # adapters + profile + Docker
│   ├── DEFENCE_STEPS.md              # build steps D0-D10
│   ├── HANDOFF.md                    # state between agents, red-team gates, findings log
│   └── red-team/                     # full red-team reports (RT-01 to RT-38)
├── sidecar/                          # ingest, hash chain, Merkle, checkpoints, heartbeat
├── witness/                          # witness service (runs on another machine)
├── verifier/                         # python -m verifier
├── tools/                            # gen_events, validate_spool
├── tests/                            # pytest, incl. red-team regression tests
├── samples/                          # sample spool lines (CONTRACT test vectors)
├── config.example.toml               # profile template -> copy to config.toml
├── witness_nodes.example.toml        # witness trusted-key file template
├── defence-compose.yml               # sidecar + witness containers
├── requirements.txt
├── .antigravity/tasks.md             # Antigravity's task list (Antigravity reads it there)
├── spool/  data/                     # runtime only, gitignored
```

## Using this with Antigravity or Claude
Open this folder as the workspace. Start a chat with:

> Read AGENTS.md, CLAUDE.md, docs/CONTRACT.md and docs/DEFENCE_STEPS.md. We are on Step N. Explain what this step needs and why, then wait for me to write it.

When stuck, paste: the step number, the exact error, and what you expected.

## Quickstart
- **Set up**: `python -m venv .venv`, `pip install -r requirements.txt`, copy `config.example.toml` to `config.toml`
- **Run witness service**: `python -m witness.app --config config.toml`
- **Run sidecar ingest**: `python -m sidecar.main --config config.toml`
- **Verify log integrity**: `python -m verifier --config config.toml [--witness http://localhost:8800 --node-id node-1]`
- **Set up and check with Docker only**: `.\coc.ps1 init` then `.\coc.ps1 check` (`./coc.sh` on macOS/Linux; build the images first)
- **Run with Docker**: `docker compose -f defence-compose.yml up -d`
- **Run tampering self-test**: `python -m tests.test_tampering`
- **Plug in a platform**: see [docs/INTEGRATION.md](docs/INTEGRATION.md)

## Known behaviour
- After a legitimate full reset, the new chain looks like a fork to the witness. That is correct for the base model.
