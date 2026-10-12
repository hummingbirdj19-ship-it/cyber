"""Initialize profile configuration, Ed25519 key pairs, and witness nodes."""
import os
from pathlib import Path
import re
import sys
import tomllib
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from coc.profile import load_profile
from sidecar.keys import generate_keypair, public_key_hex

DEFAULT_CONFIG_TEMPLATE = """[profile]
name = "example-platform"
event_types = []

[node]
node_id = "node-1"

[paths]
spool_path = "spool/events.jsonl"
db_path = "data/sidecar.db"
private_key_path = "data/node1.key"
public_key_path = "data/node1.pub"

[checkpoint]
every_n = 50
every_s = 60

[heartbeat]
interval_s = 30
witness_url = "http://localhost:8800"

[witness]
db_path = "data/witness.db"
port = 8800
nodes_file = "data/witness_nodes.toml"
"""


def _update_template(
    template: str,
    node_id: str | None = None,
    spool_path: str | None = None,
    witness_url: str | None = None,
) -> str:
    """Substitute user parameters into the config TOML template string."""
    text = template
    if node_id:
        text = re.sub(r'node_id\s*=\s*"[^"]*"', f'node_id = "{node_id}"', text)
    if spool_path:
        text = re.sub(r'spool_path\s*=\s*"[^"]*"', f'spool_path = "{spool_path}"', text)
    if witness_url:
        text = re.sub(r'witness_url\s*=\s*"[^"]*"', f'witness_url = "{witness_url}"', text)
    return text


def run_init(
    node_id: str | None = None,
    spool_path: str | None = None,
    witness_url: str | None = None,
    force: bool = False,
    config_path: Path | str = Path("config.toml"),
    example_config_path: Path | str = Path("config.example.toml"),
) -> int:
    """Run the coc init workflow: generate config.toml, key pair, and witness nodes."""
    cfg_p = Path(config_path)
    ex_p = Path(example_config_path)

    # 1. Inspect existing sidecar DB to protect against silent key rotation / forks
    db_candidate = Path("data/sidecar.db")
    if cfg_p.exists():
        try:
            with open(cfg_p, "rb") as f:
                loaded = tomllib.load(f)
                if "paths" in loaded and "db_path" in loaded["paths"]:
                    db_candidate = Path(loaded["paths"]["db_path"])
        except Exception:
            pass

    if force and db_candidate.exists():
        sys.stderr.write(
            f"Error: Refusing --force because sidecar database '{db_candidate}' exists.\n"
            "Regenerating keys against an existing chain causes witness FORK alerts (RT-12).\n"
            "Remove the database first if you intend a full reset.\n"
        )
        return 1

    # 2. Write or preserve config.toml
    if cfg_p.exists() and not force:
        print(f"[EXISTS] {cfg_p}")
    else:
        template = ex_p.read_text(encoding="utf-8") if ex_p.exists() else DEFAULT_CONFIG_TEMPLATE
        content = _update_template(template, node_id, spool_path, witness_url)
        cfg_p.parent.mkdir(parents=True, exist_ok=True)
        cfg_p.write_text(content, encoding="utf-8")
        print(f"[CREATED] {cfg_p}")

    # 3. Load profile to obtain configured paths
    profile, errors = load_profile(cfg_p)
    if errors or profile is None:
        sys.stderr.write(f"Error: Invalid profile in {cfg_p}:\n")
        for err in errors:
            sys.stderr.write(f"  {err}\n")
        return 1

    actual_node_id = profile["node"]["node_id"]
    priv_path = Path(profile["paths"]["private_key_path"])
    pub_path = Path(profile["paths"]["public_key_path"])
    actual_db_path = Path(profile["paths"]["db_path"])
    nodes_file = Path(profile["witness"]["nodes_file"])
    actual_witness_url = profile["heartbeat"]["witness_url"]

    if force and actual_db_path.exists():
        sys.stderr.write(
            f"Error: Refusing --force because sidecar database '{actual_db_path}' exists.\n"
            "Regenerating keys against an existing chain causes witness FORK alerts (RT-12).\n"
        )
        return 1

    # 4. Handle Ed25519 keypair
    if force:
        if priv_path.exists():
            priv_path.unlink()
        if pub_path.exists():
            pub_path.unlink()

    if priv_path.exists() and pub_path.exists():
        pub_hex = pub_path.read_text(encoding="utf-8").strip()
        print(f"[EXISTS] Key pair at {priv_path} and {pub_path}")
    elif priv_path.exists() and not pub_path.exists():
        pem = priv_path.read_bytes()
        priv_key = serialization.load_pem_private_key(pem, password=None)
        if not isinstance(priv_key, Ed25519PrivateKey):
            sys.stderr.write(f"Error: {priv_path} is not an Ed25519 private key\n")
            return 1
        pub_hex = public_key_hex(priv_key)
        pub_path.parent.mkdir(parents=True, exist_ok=True)
        pub_path.write_text(pub_hex, encoding="utf-8")
        print(f"[CREATED] Restored public key at {pub_path}")
    elif not priv_path.exists() and pub_path.exists() and not force:
        sys.stderr.write(
            f"Error: Private key {priv_path} is missing but {pub_path} exists; "
            "refusing to overwrite identity without --force\n"
        )
        return 1
    else:
        # Generate new key pair
        priv_key, pub_hex = generate_keypair()
        priv_path.parent.mkdir(parents=True, exist_ok=True)
        pub_path.parent.mkdir(parents=True, exist_ok=True)

        pem = priv_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        priv_path.write_bytes(pem)
        if sys.platform != "win32":
            try:
                os.chmod(priv_path, 0o600)
            except OSError:
                pass
        else:
            print("Note: on Windows, file permissions (chmod 600) are not enforced.")
        pub_path.write_text(pub_hex, encoding="utf-8")
        print(f"[CREATED] Key pair generated at {priv_path} and {pub_path}")

    # 5. Handle witness_nodes.toml
    nodes_file.parent.mkdir(parents=True, exist_ok=True)
    if nodes_file.exists() and not force:
        try:
            content = nodes_file.read_text(encoding="utf-8")
            if actual_node_id in content:
                print(f"[EXISTS] {nodes_file}")
            else:
                if "[nodes]" not in content:
                    content += "\n[nodes]\n"
                content += f'{actual_node_id} = "{pub_hex}"\n'
                nodes_file.write_text(content, encoding="utf-8")
                print(f"[UPDATED] Added node '{actual_node_id}' to {nodes_file}")
        except Exception as exc:
            sys.stderr.write(f"Warning: could not read {nodes_file}: {exc}\n")
    else:
        nodes_file.write_text(f'[nodes]\n{actual_node_id} = "{pub_hex}"\n', encoding="utf-8")
        print(f"[CREATED] {nodes_file}")

    # 6. Next steps output
    print("\nNext steps:")
    print("1. On the witness laptop, add this line to witness_nodes.toml under [nodes]:")
    print(f'   {actual_node_id} = "{pub_hex}"')
    print(f"2. Ensure [heartbeat] witness_url points to the witness laptop (currently: {actual_witness_url})")
    print("3. Run 'coc check' (or 'python -m coc check') to verify the setup")

    return 0
