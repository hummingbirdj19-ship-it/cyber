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

## 5. Running alongside the platform (after D10)
The platform's compose file includes `defence-compose.yml` unchanged and mounts its spool folder and its profile. Inside Compose, the witness is `http://witness:8800`. Image names will be listed here once they build.

## Platforms integrated so far
- Demo bank-style platform (separate repo): in progress.
