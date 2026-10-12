# HANDOFF: Defence side (PS-6 Chain of Custody)

Owner: Jeev. The demo platform is a separate, standalone repo owned by Jaganath. This repo never contains or depends on platform code; the only shared thing is CONTRACT.md.
Stages are the D steps in DEFENCE_STEPS.md (build details live there). This file adds a red-team gate to each step and tracks state between agents.
Rule: a step is DONE only when its "Done when" check (DEFENCE_STEPS.md) AND its red-team gate are ticked. Only Jeev ticks red-team boxes.
Status key: [ ] todo, [~] in progress, [x] done, [!] red team broke it, needs fix

## Current state
- Current step: I2 built on branch `step-i2`, verified locally; not merged. Waiting on Jeev: merge + push to main publishes the images to GHCR (public, the repo is public). After the first publish: check the Actions run and pull-scan the images. I0 still waits for Jaganath's OK, which blocks I3
- Last agent: Claude (I2: split compose files, published image names, publish workflow, config-less witness, `coc trust`, `coc verify`, one localhost rule for sidecar/check/verify. Two-laptop run simulated in Docker from two empty folders: check PASS, verify clean 200/200)
- Layout: README, AGENTS.md, CLAUDE.md stay at the root (agents load them there); .antigravity/tasks.md stays where Antigravity reads it
- Last commit reviewed: step-i2 (I2); pytest 197 passed on Windows; on Python 3.11 in the image all pass except the workflow test, which skips there (only chain-of-custody/ is copied in)
- Open bugs: none known. Run pytest from chain-of-custody/: from the repo root 7 older packaging tests fail (they read files by relative path; not a code bug). Not yet verified: Linux file ownership of sidecar-written files (RT-38), coc.sh on a real Linux host (syntax-checked only)
- Contract notes RT-24 (hb_counter per send attempt) and RT-28 (verifier reports witness FORK / SEQ_ROLLBACK alerts): folded into the CONTRACT v0.3 draft
- New CLI flags (deployment overrides, profile keys unchanged): verifier `--node-id`, sidecar `--witness-url`, witness `--nodes-file`
- Window between heartbeats: newest interval_s + 2.5 s of events (32.5 s at default) can be removed or rewritten with no witness verdict
- Interface assumptions about the platform: only CONTRACT.md v0.2. RawEvents arrive as JSONL in the spool file at `[paths] spool_path`; fields `source, ts, actor, event_type, details`; checked by `tools/validate_spool.py`.

## Trust model (what the red team may assume)
- The attacker has admin on the platform host: can edit the sidecar SQLite file, the spool file and anything under data/ on that host.
- That includes the node's private key (data/node1.key): the host admin can read it and re-sign a rewritten chain, so local checkpoints alone are not proof. The witness copy (D6-D8) is what catches that.
- The attacker does not control the witness machine.
- The platform is trusted for the content of events it writes (actor, ts, details). The defence proves logs were not changed after ingest; it cannot prove the platform told the truth.

---

## D0: Repo and environment
- [x] Build ("Done when" passes)

Red-team gate
- [x] config.toml, data/, spool/*.jsonl, *.key and CLAUDE.local.md are gitignored and none are tracked
- [x] Code runs on the stated minimum Python (3.11), not just the venv's version

## D1: Event generator and spool validator (the contract boundary)
- [x] Build

Red-team gate
- [x] Malformed line, missing field, extra field, wrong type: rejected, never silently accepted
- [x] Duplicate JSON keys, NaN/Infinity, floats at any depth, non-UTF-8, `\r\n`, blank lines: rejected
- [x] Event type outside the profile allow-list: rejected when the list is non-empty
- [x] Spoofed `actor` / `ts` from the platform: confirm this is documented as out of the defence's power (trust model), not silently assumed

## D2: Canonical JSON and hash-chained store
- [x] Build

Red-team gate
- [x] Two different-looking entries that serialize to the same canonical bytes?
- [x] Modify one field of an old entry: does the stored hash stop matching? (detection itself is D5)
- [x] Recompute the whole chain after editing: nothing stops this yet. Expected; D4 checkpoints + D8 witness catch it. Note it, do not fail D2 for it
- [x] Two writers at once: can the chain fork or skip a seq?

## D3: Ingest from the spool file
- [x] Build (RT-05 to RT-09 fixed)

Red-team gate
- [x] Crash between storing an entry and saving the spool offset: duplicate or missed event after restart?
- [x] An append that fails: is the event lost silently?
- [x] Spool truncated or replaced while running: loud stop, no silent re-ingest
- [x] Half-written last line: never ingested until complete
- [x] Forged lines appended to the spool by the attacker: inherent (spool is platform-writable), confirm it is documented

## D4: Merkle tree, keys and signed checkpoints
- [x] Build (checkpoints wired into main.py, RT-10 to RT-15 fixed)

Red-team gate
- [x] Merkle roots match CONTRACT section 4 vectors for n=1..5, and sizes 1..8 are sane
- [x] Attacker with write access to the DB but not the key: can they forge a checkpoint?
- [x] Key storage decided and documented: private key on the platform host is readable by its admin, so local checkpoints alone are not proof. State this
- [x] Is the key ever logged, printed or committed?
- [x] Replay or delete an old validly signed checkpoint: detected? (local only: expected no; witness copy in D6/D8)

## D5: Verifier v1 (offline)
- [x] Build (RT-17 to RT-22 fixed)

Red-team gate
- [x] Edit one field of a middle entry -> EDIT
- [x] Delete a middle entry -> SEQ_GAP
- [x] Swap two entries (reorder) -> a sensible code at the right seq
- [x] Insert a forged entry -> detected
- [x] One tamper gives one clear first verdict, not a flood
- [x] Rewrite and re-chain everything after seq K: caught by CHECKPOINT_MISMATCH if a checkpoint covers K
- [x] Verifier crash or bad DB gives exit 2, never a false "clean"

## D6: Witness service
- [x] Build (RT-23 fixed)

Red-team gate
- [x] Heartbeat from a node not in nodes_file, or bad signature -> BAD_SIGNATURE
- [x] Replay an old valid heartbeat -> COUNTER_REPLAY
- [x] Lower head_seq -> SEQ_ROLLBACK; same head_seq, different hash -> FORK
- [x] Forged heartbeat vs missed heartbeat: handled differently (BAD_SIGNATURE vs SILENCE)
- [x] Witness tables are insert-only: no UPDATE/DELETE path in the code
- [x] Alert flooding: can junk requests bury a real alert?
- [x] Witness trusts its own clock, not the client's ts

## D7: Heartbeat client and checkpoint upload
- [x] Build (RT-24 to RT-26 fixed; "Done when" checked end-to-end)

Red-team gate
- [x] Tail truncation: drop the last N entries and keep sending heartbeats -> SEQ_ROLLBACK at the witness
- [x] Kill the heartbeat thread or the whole sidecar -> SILENCE
- [x] Sidecar restart: no COUNTER_REPLAY (counter persisted correctly)
- [x] Witness down: ingest keeps going, no crash, no log spam
- [x] Shared SQLite connection between ingest and heartbeat threads: safe?

## D8: Verifier v2 (with witness)
- [x] Build (RT-28 to RT-30, RT-36 fixed)

Red-team gate
- [x] Delete the last N entries -> TRUNCATION
- [x] Rewrite the whole chain and re-sign with the stolen local key -> FORK
- [x] Sidecar stopped past the silence window -> SILENCE
- [x] Window between heartbeats: how much can be tampered undetected? Write the number down

## D9: Self-test tampering suite
- [x] Build (RT-37 nitpick fixed)

Red-team gate
- [x] Break the verifier on purpose once: the suite goes red

## D10: Docker packaging
- [x] Build (RT-31 to RT-34, RT-16, RT-27 fixed; run 2026-10-12 on Jeev's machine, Docker Desktop on Windows 11, clone at C:\dev outside OneDrive: both images build, both containers up, heartbeats 200 OK every 30 s, 200 events -> verifier `clean: true`, exit 0)

Red-team gate (checked 2026-10-12, run of DOCKER_HANDOVER.md)
- [x] Spool mounted read-only in the sidecar container (`/app/spool` `"RW":false`; config.toml also read-only)
- [x] No keys, DBs or config.toml baked into images (`find /app` prints nothing in either image)
- [x] Witness data on its own volume, not reachable from the sidecar container (sidecar mounts only config.toml, data/, spool/)
- Also seen: sidecar stopped 3 min -> witness SILENCE 75 s after the last heartbeat, verifier `SILENCE`, exit 1. A stop shorter than 75 s raises nothing, as specified. The alert stays in the `witness-data` volume, so the verifier reports it until `down -v`

## Integration checks (platform repo, separate)
- [ ] Check A: real platform events pass `tools.validate_spool`
- [ ] Check B: profile written, real spool feeds the sidecar, verifier clean, zero defence code changes
- [ ] Check C: full red-team run with the teammate against the integrated system; every successful attack logged below with its fix

## Integration-ready steps (I0 to I4, build details in DEFENCE_STEPS.md)
Decisions (Jeev, 2026-10-10): collectors move into the defence (CONTRACT v0.3); Docker only, no pip; `coc` wrapper script runs the CLI in the image; new `coc/` package approved; witness runs on a second laptop.

## I0: Contract and ownership
- [x] AGENTS.md updated, CONTRACT v0.3 drafted on `contract-change-collectors`
- [x] Merged to main (2026-10-10, Jeev's call: this repo is defence-only, so main does not wait for the platform)
- [ ] Jaganath approves v0.2 + v0.3 and updates his pinned copy; then drop "DRAFT" from the CONTRACT title

## I1: `coc init` and `coc check`
- [x] Build (Antigravity 3c33680, red-team fixes 5662697, merged to main 2026-10-12)

Red-team gate
- [x] Fresh clone: `coc init` then `coc check` pass with no manual edits (through `coc.ps1` in an empty folder, witness on the host at localhost: exit 0, spool WARN only)
- [x] Broken profile (typo'd key, wrong type, missing key file, TOML syntax error): one clear error each, no stack trace (also typo'd section and float interval; gaps in RT-42, RT-45)
- [x] Witness laptop off: `coc check` fails in about 5 s with the network checklist, never hangs (4.4 s on Windows)
- [x] `coc init` run twice changes nothing; `--force` is refused while the sidecar DB exists (and --force keeps config.toml, RT-44 fixed)
- [x] After `coc check`, the DB, spool, offsets and keys are byte-identical and the witness has no new rows (`check` only GETs; it does leave empty `sidecar.db-wal`/`-shm` files, RT-46)
- [x] The private key never appears in any output, log or error message
- [x] Witness refuses to start when `nodes_file` is missing or a folder

## I2: Split compose and publish images
- [~] Build (branch `step-i2`; everything but the first real publish is done and checked locally, 2026-10-12)

Red-team gate
- [~] Published images: no keys, DBs, config.toml or witness_nodes.toml inside (local builds under the published names: scan clean, and the same scan finds a planted key and config.toml; repeat on the pulled images after the first publish)
- [x] Witness laptop runs from an empty folder with only witness-compose.yml, the wrapper and witness_nodes.toml; platform laptop with only sidecar-compose.yml and the wrapper (simulated on one machine, two folders: `coc check` PASS with heartbeats, `coc verify` clean 200/200, exit 0)
- [x] Profile witness_url left at localhost inside a container: the sidecar and `coc check` agree (both use `sidecar.heartbeat.resolve_witness_url`; seen in the sidecar log and check output, and tested)
- [x] `coc trust`: bad node id, bad key, BOM, existing different key: refused with one clear error, file unchanged (tests in tests/test_i2.py)
- [x] Witness interval differs from the sidecar's: documented. Window = 2 * witness interval + 15 s. Sidecar slower than the window (interval_s > 2 * W + 15): SILENCE between every heartbeat, false alarms. Sidecar faster than W: still works, but a stopped sidecar is noticed only after the witness's longer window. Keep COC_INTERVAL_S equal to the profile's interval_s
- [x] Workflow: only `packages: write` + `contents: read`, no personal token, runs only on main and by hand (tested: only GITHUB_TOKEN, no pull_request trigger)

## I3: Collectors (waits for I0 approval)
- [ ] Build (detail before starting)

## I4: Attack harness against the hacker repo
- [ ] Build (detail before starting; needs the hacker repo link)
- [ ] Record the hacker repo commit tested; every attack that gets through goes in the findings log with a fix and a regression test

### Open questions
- [x] Distribution: Docker only (pip after the hackathon, if the project continues)
- [x] Generic collectors in the defence: yes, via CONTRACT v0.3
- [x] Witness for the demo: second laptop (same hotspot, firewall open on 8800, clocks synced)
- [ ] Platform repo link (Jeev gives it once I1/I2 are in place)
- [ ] Hacker repo link
- [x] Platform database: PostgreSQL 15 (`postgres` driver added to the v0.3 draft). Logs: morgan combined, errors only, stdout only; nginx access log to stdout only
- [ ] Jaganath's replies and platform changes: docs/PLATFORM_HANDOVER.md

## Later (out of scope for the base model, see AGENTS.md)
- Per-entry MAC / key ratchet, external anchoring, forensics/evidence report, dashboard, innovation layer.
- Red-team questions to answer when these start: does it add attack surface? Can the attacker write to the anchor store? Does the report itself prove integrity?

---

## Red-team findings log
| Step | Attack | Result | Fix commit |
|------|--------|--------|------------|
| D0 | RT-01 Run on Python 3.11 | BREAKS: `autocommit=` needs 3.12 | 4960b95 |
| D1 | RT-02 Lone surrogate (`\ud800` escape) | BREAKS: passed validator, dropped at ingest | 4960b95 |
| D1 | RT-03 Deeply nested JSON line | BREAKS: validator crash, ingest stalls forever | 4960b95 |
| D1 | RT-04 Duplicate JSON key | WEAKENS: accepted, last wins | 4960b95 |
| D3 | RT-05 Crash between append and offset save | BREAKS: duplicate entry on restart | 4960b95 |
| D3 | RT-06 Append fails (disk full) | BREAKS: event lost | 4960b95 |
| D3 | RT-07 Replace spool with a longer file | BREAKS: forged lines ingested from old offset | 4960b95 |
| D3 | RT-08 Second sidecar on same DB | WEAKENS: duplicates | 4960b95 |
| D4 | RT-10 Run sidecar, wait for checkpoints | BREAKS: none ever created | 4960b95 |
| D4 | RT-11 Delete middle row, then checkpoint | WEAKENS: signs inconsistent tree | 4960b95 |
| D4 | RT-12 Delete .pub or .key | WEAKENS: private key replaced, identity rotated | 4960b95 |
| D4 | RT-13 Non-string signature | WEAKENS: verify crashes | 4960b95 |
| D5 | RT-17 Forged entry with seq -1 | BREAKS: verifier says clean | 4960b95 |
| D5 | RT-18 Invalid JSON in one row's details | WEAKENS: verifier crash hides all verdicts | 4960b95 |
| D5 | RT-19 Re-chain, then delete node1.pub | BREAKS: checkpoint checks skipped, clean | 4960b95 |
| D5 | RT-20 Truncate below a signed local checkpoint | WEAKENS: clean | 4960b95 |
| D5 | RT-21 Run with --witness | WEAKENS: flag ignored, "clean" | 4960b95 (stub until D8) |
| D6 | RT-23 Junk heartbeats with random node_ids | WEAKENS: unbounded alert rows | 4960b95 |
| D7 | RT-24 Reply lost / crash after send | WEAKENS: false COUNTER_REPLAY | 4960b95 |
| D7 | RT-25 One DB error in heartbeat thread | WEAKENS: heartbeats stop for good | 4960b95 |
| D7 | RT-26 Witness rejects checkpoints | WEAKENS: silent upload failure | 4960b95 |
| D8 | RT-28 Stolen key: rewrite, re-sign, feed forged head to witness | BREAKS: verifier with witness says clean | 29141c8 |
| D8 | RT-29 Edit node_id in config to an unseen id | BREAKS: verifier with witness says clean | 29141c8 |
| D8 | RT-30 Verify while sidecar is live | WEAKENS: false TRUNCATION | 29141c8 |
| D10 | RT-31 compose up: spool mount path | BREAKS: nothing ingested, silently | 29141c8 |
| D10 | RT-32 compose up: witness nodes_file | BREAKS: witness cannot start | 29141c8 |
| D10 | RT-33 compose up: witness_url localhost | BREAKS: no heartbeats, SILENCE | 29141c8 |
| D10 | RT-34 No .dockerignore | WEAKENS: secrets sent to builder | 29141c8 |
| D0/D6 | RT-16 unpinned requirements, RT-27 no body limit (carried) | WEAKENS / NITPICK | 29141c8 |
| D10 | RT-35 Witness on the same host in demo compose | WEAKENS: host admin can reach it | accepted for demo, documented |
| D10 | RT-38 Root-owned data/ on Linux | WEAKENS: host verifier may not open DB | documented, untested |
| I1 | RT-39 Run `coc.ps1` outside the repo folder | BREAKS: `No module named coc` (image code is in /app, wrapper runs in /work); inside the repo it runs the mounted source, not the image. Fix: `ENV PYTHONPATH=/app` in sidecar/Dockerfile | 5662697 |
| I1 | RT-40 `coc check` through the wrapper, witness on the same host | BREAKS: `localhost:8800` is the container itself, always "unreachable" (confirmed with a listener on host 8800; `host.docker.internal` reached it) | 5662697 |
| I1 | RT-41 `coc init` from the repo root (wrapper copies at `cybersec_hackathon/coc.ps1`, `coc.sh`) | WEAKENS: writes config.toml and data/node1.key where chain-of-custody/.gitignore does not apply; `git add .` commits the private key. Fix: delete the root copies | 5662697 |
| I1 | RT-42 `coc init` with node-1 when node-10 is in witness_nodes.toml, or after a new key pair | WEAKENS: substring check reports [EXISTS]; node-1 never added / stale key kept, witness rejects every heartbeat. Fix: parse with tomllib and compare the key | 5662697 |
| I1 | RT-43 `node_id = "node 1"` (space) | WEAKENS: profile and init accept it, witness NODE_ID_RE rejects it later. Fix: same regex in coc/profile.py | 5662697 |
| I1 | RT-44 `coc init --force` with no DB | WEAKENS: overwrites config.toml with the template, custom profile lost silently. Fix: --force regenerates keys only | 5662697 |
| I1 | RT-45 Corrupt private key / `--witness-url` with `\1` / injected `--node-id` | NITPICK: init tracebacks (PEM error, re.error); injected node-id leaves a broken config.toml so later inits fail. Fix: validate flags before writing, lambda in re.sub, catch key load | 5662697 |
| I1 | RT-46 `coc check` on a closed WAL DB | NITPICK: creates empty sidecar.db-wal/-shm (main DB byte-identical; the verifier does the same) | accepted |
| I1 | RT-47 coc.sh on Linux runs the container as root | WEAKENS: config.toml and keys root-owned on the host (RT-38 again). Fix: `--user "$(id -u):$(id -g)"` | 5662697 |
| I1 | RT-48 `test_unreachable_witness_fails_quickly_with_checklist` ran `coc init` in the working directory | WEAKENS: every test run wrote a key pair and witness_nodes.toml into chain-of-custody/data, or into the repo root (not ignored there) when run from the root | 5662697 (test chdirs to tmp_path; root .gitignore added) |
| I1 | RT-49 `--witness-url` with a control character (`\x01`) | NITPICK: passed the URL check and was written into config.toml | 5662697 |

Full detail (why it broke, plan, verification), all in docs/red-team/: RED_TEAM_D0_D4.md (RT-01 to RT-16, including nitpicks RT-09, RT-14 to RT-16) RED_TEAM_D5_D7.md (RT-17 to RT-27, including nitpicks RT-22, RT-27) and RED_TEAM_D8_D10.md (RT-28 to RT-38, plus RT-16 and RT-27 closed)
