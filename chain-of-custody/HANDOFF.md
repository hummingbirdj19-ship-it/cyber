# HANDOFF: Defence side (PS-6 Chain of Custody)

Owner: Jeev. The demo platform is a separate, standalone repo owned by Jaganath. This repo never contains or depends on platform code; the only shared thing is CONTRACT.md.
Stages are the D steps in DEFENCE_STEPS.md (build details live there). This file adds a red-team gate to each step and tracks state between agents.
Rule: a step is DONE only when its "Done when" check (DEFENCE_STEPS.md) AND its red-team gate are ticked. Only Jeev ticks red-team boxes.
Status key: [ ] todo, [~] in progress, [x] done, [!] red team broke it, needs fix

## Current state
- Current step: D0-D7 reviewed and fixed; red-team gates await Jeev's ticks. Next: D8 (Antigravity)
- Last agent: Claude (red team + blue team on D5-D7, see RED_TEAM_D5_D7.md; D0-D4 in RED_TEAM_D0_D4.md)
- Last commit reviewed: none (D0-D7 code and fixes are uncommitted); pytest 136 passed
- Open bugs: none. Deferred to D10: RT-16 (pin requirements), RT-27 (witness request body limit)
- Contract note (RT-24): hb_counter now increases by 1 per send attempt, so the witness can see gaps after failed sends. Witness rules unchanged; CONTRACT section 5 wording "increases by 1 per heartbeat" could be clarified via the change protocol
- Carry into D8: replace the `--witness` stub (exits 2 now); verify local checkpoints against the witness's copy or pinned key (defeats a swapped node1.pub); window between heartbeats is about interval_s (30 s) + 2.5 s send timeout
- Carry into D10: confirm Python 3.11 in the image; body-size limit in front of the witness
- Interface assumptions about the platform: only CONTRACT.md v0.2. RawEvents arrive as JSONL in the spool file at `[paths] spool_path`; fields `source, ts, actor, event_type, details`; checked by `tools/validate_spool.py`.

## Trust model (what the red team may assume)
- The attacker has admin on the platform host: can edit the sidecar SQLite file, the spool file and anything under data/ on that host.
- The attacker does not control the witness machine.
- The platform is trusted for the content of events it writes (actor, ts, details). The defence proves logs were not changed after ingest; it cannot prove the platform told the truth.

---

## D0: Repo and environment
- [x] Build ("Done when" passes)

Red-team gate
- [ ] config.toml, data/, spool/*.jsonl, *.key and CLAUDE.local.md are gitignored and none are tracked
- [ ] Code runs on the stated minimum Python (3.11), not just the venv's version

## D1: Event generator and spool validator (the contract boundary)
- [x] Build

Red-team gate
- [ ] Malformed line, missing field, extra field, wrong type: rejected, never silently accepted
- [ ] Duplicate JSON keys, NaN/Infinity, floats at any depth, non-UTF-8, `\r\n`, blank lines: rejected
- [ ] Event type outside the profile allow-list: rejected when the list is non-empty
- [ ] Spoofed `actor` / `ts` from the platform: confirm this is documented as out of the defence's power (trust model), not silently assumed

## D2: Canonical JSON and hash-chained store
- [x] Build

Red-team gate
- [ ] Two different-looking entries that serialize to the same canonical bytes?
- [ ] Modify one field of an old entry: does the stored hash stop matching? (detection itself is D5)
- [ ] Recompute the whole chain after editing: nothing stops this yet. Expected; D4 checkpoints + D8 witness catch it. Note it, do not fail D2 for it
- [ ] Two writers at once: can the chain fork or skip a seq?

## D3: Ingest from the spool file
- [x] Build (RT-05 to RT-09 fixed)

Red-team gate
- [ ] Crash between storing an entry and saving the spool offset: duplicate or missed event after restart?
- [ ] An append that fails: is the event lost silently?
- [ ] Spool truncated or replaced while running: loud stop, no silent re-ingest
- [ ] Half-written last line: never ingested until complete
- [ ] Forged lines appended to the spool by the attacker: inherent (spool is platform-writable), confirm it is documented

## D4: Merkle tree, keys and signed checkpoints
- [x] Build (checkpoints wired into main.py, RT-10 to RT-15 fixed)

Red-team gate
- [ ] Merkle roots match CONTRACT section 4 vectors for n=1..5, and sizes 1..8 are sane
- [ ] Attacker with write access to the DB but not the key: can they forge a checkpoint?
- [ ] Key storage decided and documented: private key on the platform host is readable by its admin, so local checkpoints alone are not proof. State this
- [ ] Is the key ever logged, printed or committed?
- [ ] Replay or delete an old validly signed checkpoint: detected? (local only: expected no; witness copy in D6/D8)

## D5: Verifier v1 (offline)
- [x] Build (RT-17 to RT-22 fixed)

Red-team gate
- [ ] Edit one field of a middle entry -> EDIT
- [ ] Delete a middle entry -> SEQ_GAP
- [ ] Swap two entries (reorder) -> a sensible code at the right seq
- [ ] Insert a forged entry -> detected
- [ ] One tamper gives one clear first verdict, not a flood
- [ ] Rewrite and re-chain everything after seq K: caught by CHECKPOINT_MISMATCH if a checkpoint covers K
- [ ] Verifier crash or bad DB gives exit 2, never a false "clean"

## D6: Witness service
- [x] Build (RT-23 fixed)

Red-team gate
- [ ] Heartbeat from a node not in nodes_file, or bad signature -> BAD_SIGNATURE
- [ ] Replay an old valid heartbeat -> COUNTER_REPLAY
- [ ] Lower head_seq -> SEQ_ROLLBACK; same head_seq, different hash -> FORK
- [ ] Forged heartbeat vs missed heartbeat: handled differently (BAD_SIGNATURE vs SILENCE)
- [ ] Witness tables are insert-only: no UPDATE/DELETE path in the code
- [ ] Alert flooding: can junk requests bury a real alert?
- [ ] Witness trusts its own clock, not the client's ts

## D7: Heartbeat client and checkpoint upload
- [x] Build (RT-24 to RT-26 fixed; "Done when" checked end-to-end)

Red-team gate
- [ ] Tail truncation: drop the last N entries and keep sending heartbeats -> SEQ_ROLLBACK at the witness
- [ ] Kill the heartbeat thread or the whole sidecar -> SILENCE
- [ ] Sidecar restart: no COUNTER_REPLAY (counter persisted correctly)
- [ ] Witness down: ingest keeps going, no crash, no log spam
- [ ] Shared SQLite connection between ingest and heartbeat threads: safe?

## D8: Verifier v2 (with witness)
- [ ] Build

Red-team gate
- [ ] Delete the last N entries -> TRUNCATION
- [ ] Rewrite the whole chain and re-sign with the stolen local key -> FORK
- [ ] Sidecar stopped past the silence window -> SILENCE
- [ ] Window between heartbeats: how much can be tampered undetected? Write the number down

## D9: Self-test tampering suite
- [ ] Build

Red-team gate
- [ ] Break the verifier on purpose once: the suite goes red

## D10: Docker packaging
- [ ] Build

Red-team gate
- [ ] Spool mounted read-only in the sidecar container
- [ ] No keys, DBs or config.toml baked into images
- [ ] Witness data on its own volume, not reachable from the sidecar container

## Integration checks (platform repo, separate)
- [ ] Check A: real platform events pass `tools.validate_spool`
- [ ] Check B: profile written, real spool feeds the sidecar, verifier clean, zero defence code changes
- [ ] Check C: full red-team run with the teammate against the integrated system; every successful attack logged below with its fix

## Later (out of scope for the base model, see AGENTS.md)
- Per-entry MAC / key ratchet, external anchoring, forensics/evidence report, dashboard, innovation layer.
- Red-team questions to answer when these start: does it add attack surface? Can the attacker write to the anchor store? Does the report itself prove integrity?

---

## Red-team findings log
| Step | Attack | Result | Fix commit |
|------|--------|--------|------------|
| D0 | RT-01 Run on Python 3.11 | BREAKS: `autocommit=` needs 3.12 | fixed, uncommitted |
| D1 | RT-02 Lone surrogate (`\ud800` escape) | BREAKS: passed validator, dropped at ingest | fixed, uncommitted |
| D1 | RT-03 Deeply nested JSON line | BREAKS: validator crash, ingest stalls forever | fixed, uncommitted |
| D1 | RT-04 Duplicate JSON key | WEAKENS: accepted, last wins | fixed, uncommitted |
| D3 | RT-05 Crash between append and offset save | BREAKS: duplicate entry on restart | fixed, uncommitted |
| D3 | RT-06 Append fails (disk full) | BREAKS: event lost | fixed, uncommitted |
| D3 | RT-07 Replace spool with a longer file | BREAKS: forged lines ingested from old offset | fixed, uncommitted |
| D3 | RT-08 Second sidecar on same DB | WEAKENS: duplicates | fixed, uncommitted |
| D4 | RT-10 Run sidecar, wait for checkpoints | BREAKS: none ever created | fixed, uncommitted |
| D4 | RT-11 Delete middle row, then checkpoint | WEAKENS: signs inconsistent tree | fixed, uncommitted |
| D4 | RT-12 Delete .pub or .key | WEAKENS: private key replaced, identity rotated | fixed, uncommitted |
| D4 | RT-13 Non-string signature | WEAKENS: verify crashes | fixed, uncommitted |
| D5 | RT-17 Forged entry with seq -1 | BREAKS: verifier says clean | fixed, uncommitted |
| D5 | RT-18 Invalid JSON in one row's details | WEAKENS: verifier crash hides all verdicts | fixed, uncommitted |
| D5 | RT-19 Re-chain, then delete node1.pub | BREAKS: checkpoint checks skipped, clean | fixed, uncommitted |
| D5 | RT-20 Truncate below a signed local checkpoint | WEAKENS: clean | fixed, uncommitted |
| D5 | RT-21 Run with --witness | WEAKENS: flag ignored, "clean" | fixed (stub until D8), uncommitted |
| D6 | RT-23 Junk heartbeats with random node_ids | WEAKENS: unbounded alert rows | fixed, uncommitted |
| D7 | RT-24 Reply lost / crash after send | WEAKENS: false COUNTER_REPLAY | fixed, uncommitted |
| D7 | RT-25 One DB error in heartbeat thread | WEAKENS: heartbeats stop for good | fixed, uncommitted |
| D7 | RT-26 Witness rejects checkpoints | WEAKENS: silent upload failure | fixed, uncommitted |

Full detail (why it broke, plan, verification): RED_TEAM_D0_D4.md (RT-01 to RT-16, including nitpicks RT-09, RT-14 to RT-16) and RED_TEAM_D5_D7.md (RT-17 to RT-27, including nitpicks RT-22, RT-27)
