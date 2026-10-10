# Task List: Chain of Custody (base model, defence repo)

Step details live in docs/DEFENCE_STEPS.md. Integration details live in docs/INTEGRATION.md. Tick a box only when its "Done when" check passes.

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
- [x] D8 verifier v2 (truncation, fork, silence)
- [x] D9 self-test tampering suite
- [x] D10 Docker packaging (standalone defence-compose.yml)

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

## Integration-ready (docs/DEFENCE_STEPS.md, I steps)
- [ ] I0 CONTRACT v0.3 approved by platform integrator (no code)
- [ ] I1 `coc init` and `coc check` (new `coc/` package, wrapper scripts, witness nodes_file check)
- [ ] I2 split compose files and published images
- [ ] I3 collectors (after I0 approval)
- [ ] I4 attack harness against the hacker repo
