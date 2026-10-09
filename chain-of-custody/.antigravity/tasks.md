# Task List: Chain of Custody (base model)

Step details live in DEFENCE_STEPS.md and PLATFORM_STEPS.md. Tick a box only when its "Done when" check passes.

## Sync 0: before splitting
- [ ] Both read CONTRACT.md and agree. Commit it to main.
- [ ] Branches created: `defence`, `platform`

## Defence (Jeev)
- [ ] D0 repo and environment
- [ ] D1 fake event generator and spool validator
- [ ] D2 canonical JSON and hash-chained store
- [ ] D3 ingest from the spool file
- [ ] D4 Merkle tree, keys, signed checkpoints
- [ ] D5 verifier v1 (offline checks)
- [ ] D6 witness service
- [ ] D7 heartbeat client and checkpoint upload
- [ ] D8 verifier v2 (truncation, fork, silence)
- [ ] D9 self-test tampering suite
- [ ] D10 Docker packaging

## Platform (Jaganath)
- [ ] P0 platform running in Docker
- [ ] P1 what each action leaves behind (NOTES.md)
- [ ] P2 fake user data seeded
- [ ] P3 live traffic simulator
- [ ] P4 database change adapter
- [ ] P5 app log adapter
- [ ] P6 web server log adapter ("host" source for now)
- [ ] P7 single spool writer passes the validator
- [ ] P8 compose override with sidecar and witness
- [ ] P9 reset and snapshot scripts
- [ ] P10 attacker access notes for the red team

## Sync points
- [ ] Sync A (end of day 1): samples/events.sample.jsonl replaced with real events
- [ ] Sync B: real spool feeds the real sidecar, verifier reports clean
- [ ] Sync C: full compose stack up, red team gets access

## Later (not now)
- [ ] Key ratchet and per-entry MAC
- [ ] External anchor (timestamp authority or public log)
- [ ] Tamper forensics report
- [ ] Erasure-aware logging
- [ ] Verifier dashboard
