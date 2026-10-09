# PLATFORM STEPS (Jaganath): base model

You build: victim/ (the platform, fake data, adapters that write the spool). You do not touch sidecar/, witness/, verifier/. Read CONTRACT.md first, especially section 2 (RawEvent). Every step has: Build, Done when, Hiccups.

## How to use this file with Claude or Antigravity
Open the repo as the workspace and start each step with:

> Read AGENTS.md, CLAUDE.md, CONTRACT.md and PLATFORM_STEPS.md. We are on Step N. Explain what it needs and why, then help me do it.

(The coaching rule in CLAUDE.md is Jeev's preference. Edit that section for how you want to work.)

## Working separately: how this stays compatible
- Your only output toward the defence is the spool file `spool/events.jsonl` in the RawEvent format. Nothing else is shared.
- Jeev will give you `python -m tools.validate_spool <path>` after her Step D1 (day 1). That tool is your acceptance test. Until it exists, check lines by eye against `samples/events.sample.jsonl`.
- Sync A (end of day 1): replace `samples/events.sample.jsonl` with 20 or more events produced by real actions on the real platform. Tell Jeev. Any difference from the contract goes through the change protocol.
- Commit often to branch `platform`. Merge to main only at sync points.

---

## P0: Platform running in Docker
Build:
- `victim/docker-compose.yml` with Firefly III and its database, from the project's official Docker setup. Run it unmodified.
- If it will not run on your machine after a reasonable try, switch to Formance Ledger (also Docker Compose with Postgres) and note that in `victim/NOTES.md`.

Done when: the web UI opens in the browser, you can create an account and a transaction, and `docker compose down` then `up` keeps the data.

Hiccups:
- Firefly III needs an app key (32 characters) in its env file. Missing key is the most common first failure.
- Port already in use: change the host port, not the container port.
- Check which database the compose file uses (MariaDB or MySQL or Postgres). The adapter in P4 depends on it.
- Apple silicon or low RAM: close other containers, and check the image supports your architecture.

## P1: What does each action leave behind?
Build:
- Do these actions in the UI: log in, fail a login, create an account, create a transaction, edit its amount, delete it.
- For each, note in `victim/NOTES.md` where evidence appears: app log file (path), database table and columns, web server access log line.

Done when: NOTES.md has a table of action vs source, and you know which of the three sources is richest.

Hiccups:
- The app log may be quiet for normal actions. That is fine: the database source will carry the transactions.
- Find the app's log folder inside the container (for Laravel apps it is under `storage/logs`). Mount it as a volume so you can read it from outside.

## P2: Fake user data
Build:
- A script `victim/seed/seed.py` that fills the platform with synthetic users, accounts, categories and about 300 transactions over past weeks. Use the platform's REST API with a personal access token, or its CSV import. A fake-data library (such as Faker) is fine.
- Indian-style fake names and INR amounts are fine. No real data, ever.

Done when: the UI shows realistic data, and the seed script can be re-run on a fresh database.

Hiccups:
- API token: create it in the platform's profile settings. Do not commit it. Use an env file.
- Seeding fires the database triggers from P4. Either seed before enabling them or let seed events flow into the log. Decide and note it.

## P3: Live traffic simulator
Build:
- `victim/seed/traffic.py`: creates, edits and deletes a few transactions per minute, and does the odd failed login, so there is always live activity. Rate is a command-line option.

Done when: leaving it for 10 minutes visibly changes the data and produces log activity.

Hiccups:
- Respect the API rate limits. Add small random delays.
- Keep it stoppable with Ctrl+C and safe to start twice.

## P4: Database change adapter (source = `db`)
Build:
- In the platform database, add an audit table and triggers on the transactions and accounts tables (works on MySQL, MariaDB and Postgres) that record every insert, update and delete with old and new values.
- `victim/adapters/db_adapter.py`: poll the audit table, turn each new row into a RawEvent (`transaction_created`, `transaction_updated`, `transaction_deleted`, `account_created`), mark progress in a small state file so it never repeats.
- Use a database user that can read the audit table only.

Done when: creating, editing and deleting a transaction in the UI each produce the right RawEvent within a few seconds, with amounts as strings.

Hiccups:
- Triggers differ between MySQL/MariaDB and Postgres. Check which you have from P0 before writing them.
- The app's own tables may use soft deletes (a flag instead of a real delete). Capture the update that sets the flag and name it `transaction_deleted` in the RawEvent.
- Do not modify the platform's own tables or code. Adding an audit table and triggers is the only change, and it is part of the integration story.

## P5: App log adapter (source = `app`)
Build:
- `victim/adapters/app_adapter.py`: follow the app log file and turn useful lines (login success, login failed, errors) into RawEvents. Ignore noise.

Done when: logging in, and failing a login on purpose, give `login_success` and `login_failed` events with the right actor.

Hiccups:
- Lines may be multi-line (stack traces). Group by the timestamp at the start of a line.
- If the app log is too thin, say so in NOTES.md and rely on P4 and P6.

## P6: Web server log adapter (source = `host`)
Build:
- `victim/adapters/web_adapter.py`: follow the web server access log and emit `http_request` events (method, path, status).

Done when: browsing the app produces `http_request` events.

Hiccups:
- Docker on Windows or Mac has no real host auth log. For now the web server access log is the `host` source. Real host auth and shell logs are later work.
- Skip static files (css, js, images) to keep volume down.

## P7: One spool writer
Build:
- `victim/adapters/spool.py`: the single place that appends to `spool/events.jsonl`. Each line is written with one write call and a trailing newline. All three adapters use it (a lock or a small queue so two adapters never interleave).

Done when: after 10 minutes of traffic the spool holds events from all three sources and `python -m tools.validate_spool spool/events.jsonl` passes with zero errors.

Hiccups:
- Interleaved or half lines: always build the full line first, then write once.
- Never truncate or rewrite the spool. The sidecar tracks a byte offset.

## P8: Compose override with the defence
Build:
- `victim/docker-compose.override.yml` that adds the `sidecar` and `witness` services from Jeev's `defence-compose.yml` (she gives you the image names after her D10), mounts the spool folder, and shares one data folder.
- A short `victim/README.md`: how to start everything with one command.

Done when: `docker compose up` brings up the platform, the adapters, the sidecar and the witness, and Jeev's verifier on the host reports clean after a few minutes of traffic.

Hiccups:
- Inside Compose, services reach each other by service name (`witness`, not `localhost`).
- Order: the sidecar must start fine even if the platform is not ready yet. Use restart policies instead of fragile startup ordering.
- Clock: the containers share the host clock. If you add a second machine for the witness, sync it with NTP.

## P9: Reset and snapshot scripts
Build:
- `victim/scripts/reset.sh`: stop everything, wipe the platform volumes, the spool and the sidecar data, reseed, start again.
- `victim/scripts/snapshot.sh` and `restore.sh`: save and restore a known-good state, so the red team can undo an attack in one command.

Done when: reset and restore each work from a messy state in under a few minutes.

Hiccups:
- Do not wipe `data/*.key` by accident if the witness has already registered that public key. Document which files each script touches.

## P10: Attacker access notes
Build:
- `victim/ATTACKER_ACCESS.md` for the red team: how to get an admin shell on the victim, where the spool, sidecar database and platform files live, how to restore with P9. Assume breach: the attacker starts with admin access.

Done when: someone who has not seen the setup can get a shell and find every file in 10 minutes.

---

## Done with the base model when
- P0 to P10 pass their checks and the validator passes on a long run.
- `docker compose up` brings up the full stack from a clean machine.
- Sync C is done.

## Later (do not start yet)
Real host auth and shell logs; more adapters for other platforms; reverse proxy request log; richer fake data.
