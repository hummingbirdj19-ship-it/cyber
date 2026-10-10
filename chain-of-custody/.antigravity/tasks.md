# Task List: Chain of Custody (base model, defence repo)

Step details live in DEFENCE_STEPS.md. Integration details live in INTEGRATION.md. Tick a box only when its "Done when" check passes.

## Contract
- [x] CONTRACT v0.1 agreed
- [ ] CONTRACT v0.2 (platform-agnostic) approved by platform integrator and merged

## Defence
- [x] D0 repo and environment
- [x] D1 fake event generator and spool validator (profile-aware)
- [x] D2 canonical JSON and hash-chained store
- [x] D3 ingest from the spool file
- [x] D4 Merkle tree, keys, signed checkpoints
- [x] D5 verifier v1 (offline checks)
- [x] D6 witness service
- [x] D7 heartbeat client and checkpoint upload
- [ ] D8 verifier v2 (truncation, fork, silence)
- [ ] D9 self-test tampering suite
- [ ] D10 Docker packaging (standalone defence-compose.yml)

## Integration checks (demo bank-style platform, separate repo)
- [ ] Check A: real platform events pass `tools.validate_spool`
- [ ] Check B: profile written, real spool feeds the sidecar, verifier reports clean, zero defence code changes
- [ ] Check C: full stack up, red team gets access

## Later (not now)
- [ ] Key ratchet and per-entry MAC
- [ ] External anchor (timestamp authority or public log)
- [ ] Tamper forensics report
- [ ] Erasure-aware logging
- [ ] Verifier dashboard
