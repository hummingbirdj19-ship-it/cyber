# INTEGRATION: plugging any platform into Chain of Custody

The defence is the same code for every platform. To protect a platform you do two things, both outside the defence code:
1. **Adapters (platform side):** small programs that turn what the platform already leaves behind into RawEvents in a spool file.
2. **Profile (defence side):** one `config.toml` that tells the sidecar where the spool is and how to label the node.

No change to the platform's code. No change to the defence's code.

## 1. Map what each action leaves behind
Do the key actions on the platform (log in, fail a login, create, edit and delete a record, create an account). For each, note where evidence appears:
| source | typical evidence |
|---|---|
| `app` | the app's own log file (logins, errors) |
| `db` | an audit table filled by triggers on the important tables (insert, update, delete with old and new values) |
| `host` | the web server access log (method, path, status) |

Pick event type names. Reuse the recommended names in CONTRACT.md section 2 where they fit; otherwise any lowercase snake_case name is fine.

## 2. Write the adapters
- One adapter per source. Each follows its evidence (log file, audit table) and keeps a small state file so it never repeats or skips.
- **Database:** adding an audit table and triggers is the only allowed change to the platform, and only if the platform has no audit log already. Use a read-only database user for the adapter. Soft deletes (a flag instead of a real delete) still map to a `*_deleted` event.
- **App log:** group multi-line entries (stack traces) by the timestamp at the start of a line. Ignore noise.
- **Web log:** skip static files (css, js, images).
- **One spool writer:** all adapters append through one function or process. Build the full line, write it with one write call, end with `\n`. Never truncate or rewrite the spool.
- Money and other decimals are strings. No floats anywhere in `details`. Synthetic data only in demos.

## 3. Write the profile
Copy `config.example.toml` to `config.toml` and set:
- `[profile] name`: a label for humans.
- `[profile] event_types`: leave empty to accept any snake_case name, or list the platform's names to reject anything else.
- `[node] node_id` and `[paths] spool_path`.

## 4. Acceptance test
```
python -m tools.validate_spool <spool file> --config config.toml
```
Exit code 0 on a long run of real traffic means the platform is integrated. Then start the sidecar and witness and check the verifier reports clean.

## 5. Running alongside the platform (Docker Compose)
The platform's compose file includes `defence-compose.yml` unchanged. Per platform you supply only the spool folder, the profile and the witness's list of trusted node keys.

### Images and services
- Sidecar image: `ghcr.io/jeevikas-19/coc-sidecar:latest` (from `sidecar/Dockerfile`, Python 3.11; published by `.github/workflows/publish-images.yml`, or built locally by `defence-compose.yml` under the same name)
- Witness image: `ghcr.io/jeevikas-19/coc-witness:latest` (from `witness/Dockerfile`, Python 3.11; same)
- Compose file: `defence-compose.yml`. No keys, DBs or `config.toml` go into the images (`.dockerignore`).

### Mounts
- Spool folder (`SPOOL_DIR`, default `./spool`): read-only at `/app/spool` in the sidecar.
- Profile `config.toml`: read-only at `/app/config.toml` in both.
- `./data`: sidecar DB and node keys, at `/app/data` in the sidecar only.
- `witness-data` named volume: the witness DB, at `/app/data` in the witness only.
- `./data/witness_nodes.toml`: read-only at `/etc/coc/witness_nodes.toml` in the witness.

Keep the profile's paths relative and under `spool/` and `data/` (as in `config.example.toml`). Then the same profile works on the host and in the containers. Inside Compose the sidecar reaches the witness at `http://witness:8800`; the compose file passes that with `--witness-url`, so the profile can keep `localhost` for host runs.

### First start
1. Build the images: `docker compose -f defence-compose.yml build`
2. `.\coc.ps1 init` (Windows) or `./coc.sh init` (macOS/Linux). Optional: `--node-id`, `--spool`, `--witness-url`. It writes `config.toml`, the node key pair and `data/witness_nodes.toml`, and prints the line the witness laptop needs. Run twice, it changes nothing; it never overwrites `config.toml`.
3. `docker compose -f defence-compose.yml up -d`
4. `.\coc.ps1 check` (or `./coc.sh check`): one PASS/WARN/FAIL line per layer, exit 0 when nothing failed. It only reads: no DB, spool or key changes, no heartbeat sent. Inside the wrapper a `localhost` witness URL is checked on the host (`host.docker.internal`).
5. On the host: `python -m verifier --config config.toml --witness http://localhost:8800 --node-id node-1`

### Two laptops, no clone (published images)
Each laptop needs Docker and two files from this repo, nothing else. Windows uses `.\coc.ps1`, macOS/Linux `./coc.sh`.

Witness laptop (a folder with `witness-compose.yml` and the wrapper):
1. `coc trust node-1 <public key>`: the key comes from step 1 on the platform laptop. This writes `witness_nodes.toml` correctly (no Notepad, no BOM).
2. `docker compose -f witness-compose.yml up -d`. Set `COC_INTERVAL_S` if the profile's `interval_s` is not 30.
3. Open inbound TCP 8800 in the firewall. Note this laptop's IP address.

Platform laptop (a folder with `sidecar-compose.yml` and the wrapper, next to the platform):
1. `coc init --witness-url http://<witness laptop IP>:8800`. It prints the `coc trust` line for the witness laptop.
2. Point the platform's spool at `spool/` here (or set `SPOOL_DIR`), then `docker compose -f sidecar-compose.yml up -d`.
3. `coc check`: PASS, or WARN until the first heartbeat (30 s).
4. Audit: `coc verify --witness http://<witness laptop IP>:8800 --node-id node-1`. Exit 0 = clean, 1 = tampering, 2 = the check itself failed.

Pin a version with `COC_TAG=sha-<commit>`. A fork publishes under its own owner: set `COC_IMAGE_PREFIX` (compose) and `COC_IMAGE` (wrappers).

### Notes
- Linux hosts: `coc.sh` runs as your user, so `config.toml` and the keys it creates are yours. The compose containers still run as root, so files the sidecar writes in `data/` are root-owned. Run the verifier with sudo, or `chown` the folder, or add `user: "<uid>:<gid>"` to the sidecar service. Docker Desktop (Windows, macOS) is not affected.
- Demo only: here the witness runs on the same host as the platform. A host admin could stop it or edit its volume. In real use, run the witness on a machine the platform's admins cannot reach and point `--witness-url` at it.
- When auditing, pass `--node-id` yourself instead of trusting the `node_id` in the audited host's `config.toml`. If the witness has no records for that node id, the verifier exits 2, never "clean".

## Platforms integrated so far
- Demo bank-style platform (separate repo): in progress.
