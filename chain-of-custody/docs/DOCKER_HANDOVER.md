# Docker handover: run the defence in Docker once

**Goal:** prove the two defence containers (sidecar and witness) build and run, and that the verifier says the log is clean. Jeev ran this on Windows 11 on 2026-10-12 and every step passed. It takes about 30 minutes.

**You need:** Windows 10/11 (or macOS/Linux), Git, Python 3.11 or newer (any newer version works, 3.14 included; the containers use 3.11 regardless). Commands below are for Windows PowerShell; on macOS/Linux use `source .venv/bin/activate` and `cp` instead of `copy`.

**Run each command on its own line and press Enter after each.** Pasting two commands onto one line makes the second one an argument of the first.

---

## 1. Install Docker Desktop
1. Download it from https://www.docker.com/products/docker-desktop/ and install it with the default options (it uses WSL 2).
2. Restart if it asks. Open Docker Desktop and wait until it shows "Engine running".
3. Check it works: `docker run --rm hello-world` prints "Hello from Docker!".

If it does not start:
- "Virtualization not enabled": Task Manager > Performance > CPU must show "Virtualization: Enabled". If it says Disabled, turn on Intel VT-x / AMD SVM in the BIOS.
- "WSL 2 is not installed": open PowerShell as admin, run `wsl --install`, restart.

## 2. Get the code
Clone into a folder **outside OneDrive** (or Dropbox, Google Drive). `data/` holds the private key and the live SQLite database: a sync client uploads the key and can lock the database while the sidecar writes it.
```
mkdir C:\dev -Force
cd C:\dev
git clone https://github.com/JeevikaS-19/cybersec_hackathon.git
cd cybersec_hackathon/chain-of-custody
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 3. Set up the profile, key and witness key file
```
copy config.example.toml config.toml
mkdir data, spool -Force
python -c "from pathlib import Path; from sidecar.keys import load_or_generate_keypair as k; pub = k(Path('data/node1.key'), Path('data/node1.pub'))[1]; Path('data/witness_nodes.toml').write_text('[nodes]\nnode-1 = ' + repr(pub) + '\n', encoding='utf-8'); print(pub)"
type data\witness_nodes.toml
```
The Python command creates the node key pair and writes `data\witness_nodes.toml` with its public key. It uses only single quotes inside, because Windows PowerShell 5.1 breaks nested double quotes. Don't make the file by hand in Notepad: "UTF-8 with BOM" makes the witness fail to start. The `type` command should show:
```
[nodes]
node-1 = '<the same 64 characters the Python command printed>'
```
Do this before step 4. If the file is missing, Docker creates an empty **folder** with that name. If that happens, delete the folder and run the Python command again.

## 4. Build and start
```
docker compose -f defence-compose.yml up -d --build
docker compose -f defence-compose.yml ps
```
Both `chain-of-custody-sidecar` and `chain-of-custody-witness` should be "running". If one is not, run `docker compose -f defence-compose.yml logs` and copy the output. One `Witness unreachable ... Connection refused` line right at start is normal: the sidecar starts a moment before the witness is ready to accept connections and reconnects on the next heartbeat.

## 5. Feed events and verify
```
python -m tools.gen_events --config config.toml --count 200 --rate 20
```
Wait 45 seconds, so the sidecar sends at least one heartbeat. Then:
```
python -m verifier --config config.toml --witness http://localhost:8800 --node-id node-1
```
```
echo $LASTEXITCODE
```
**Expected:** JSON with `"clean": true` and `"checked_entries": 200`, then `0`.

## 6. Three safety checks (copy the output of each)
```
docker inspect chain-of-custody-sidecar --format "{{json .Mounts}}"
```
- The mount with `"Destination":"/app/spool"` must show `"RW":false`.
- No mount may have the name `witness-data` (only the witness may see that volume).

```
docker run --rm chain-of-custody-sidecar:latest find /app -name "*.key" -o -name "*.pub" -o -name "*.db" -o -name config.toml
docker run --rm chain-of-custody-witness:latest find /app -name "*.key" -o -name "*.pub" -o -name "*.db" -o -name config.toml
```
- Both must print **nothing**. If they print a file, a secret was copied into the image.

## 7. Optional: watch it catch something (3 minutes)
```
docker compose -f defence-compose.yml stop sidecar
```
Keep the sidecar stopped for **a full 2 minutes**, then run the verifier and `echo $LASTEXITCODE` from step 5 again. **Expected:** a `SILENCE` verdict and exit code `1`. Only then start it again with `docker compose -f defence-compose.yml start sidecar`.

The witness raises SILENCE after 75 seconds without a heartbeat (2 × 30 s + 15 s). If you restart the sidecar sooner, nothing is raised. That is correct behaviour, not a failure.

## 8. Clean up and report
```
docker compose -f defence-compose.yml down -v
```
`-v` also deletes the witness volume. Without it, the SILENCE alert from step 7 stays stored and the verifier keeps reporting it on the next run. For a fully fresh start, also delete `data\sidecar.db*` and `spool\events.jsonl`.
Send Jeev:
- Did steps 4, 5 and 6 pass? (yes/no each)
- The verifier output from step 5 (and step 7 if you did it)
- The full error text for anything that failed, plus `docker compose -f defence-compose.yml logs` if a container stopped

Do not commit or push anything from this folder. `config.toml`, `data/` and the key are private and already ignored by git.
