"""Initialize profile configuration, Ed25519 key pairs, and witness nodes."""
import json
from pathlib import Path
import re
import sys
import tomllib
from typing import Any

from coc.profile import NODE_ID_RE, load_profile, validate_profile
from sidecar.keys import generate_keypair, load_or_generate_keypair, save_keypair

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

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _flag_errors(node_id: str | None, spool_path: str | None, witness_url: str | None) -> list[str]:
    """Check the command line flags before anything is written."""
    errors: list[str] = []
    if node_id is not None and not NODE_ID_RE.fullmatch(node_id):
        errors.append(f"--node-id: '{node_id}' must match {NODE_ID_RE.pattern} (the witness rejects anything else)")
    if spool_path is not None and (not spool_path or _CONTROL_RE.search(spool_path)):
        errors.append("--spool: must be a non-empty path without control characters")
    if witness_url is not None and (
        not re.fullmatch(r"https?://[^\s\"'\\]+", witness_url) or _CONTROL_RE.search(witness_url)
    ):
        errors.append(f"--witness-url: '{witness_url}' must look like http://<host>:<port>")
    return errors


def _update_template(
    template: str,
    node_id: str | None = None,
    spool_path: str | None = None,
    witness_url: str | None = None,
) -> str:
    """Substitute user parameters into the config TOML template string as quoted TOML strings."""
    text = template
    for key, value in (("node_id", node_id), ("spool_path", spool_path), ("witness_url", witness_url)):
        if value:
            # json.dumps gives a valid TOML basic string; a lambda keeps backslashes literal in re.sub.
            line = f"{key} = {json.dumps(value, ensure_ascii=False)}"
            text = re.sub(rf"^{key}\s*=\s*\"[^\"]*\"", lambda _m: line, text, flags=re.MULTILINE)
    return text


def _fail(lines: list[str]) -> int:
    """Print error lines to stderr and return exit code 1."""
    for line in lines:
        sys.stderr.write(f"Error: {line}\n")
    return 1


def _ensure_witness_node(nodes_file: Path, node_id: str, pub_hex: str, replace: bool) -> tuple[str, str | None]:
    """Make witness_nodes.toml trust node_id with pub_hex; return (status, error)."""
    entry = f"{node_id} = {json.dumps(pub_hex)}"
    if not nodes_file.exists():
        nodes_file.parent.mkdir(parents=True, exist_ok=True)
        nodes_file.write_text(f"[nodes]\n{entry}\n", encoding="utf-8")
        return "CREATED", None
    if nodes_file.is_dir():
        return "", f"{nodes_file} is a folder, not a file (Docker creates one when the file is missing); delete it and run init again"
    text = nodes_file.read_text(encoding="utf-8")
    try:
        data: dict[str, Any] = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return "", f"{nodes_file} is not valid TOML ({exc}); fix or delete it"
    nodes = data.get("nodes", {})
    if set(data) - {"nodes"} or not isinstance(nodes, dict):
        return "", f"{nodes_file} must contain only a [nodes] table; fix it by hand"
    current = nodes.get(node_id)
    if current == pub_hex:
        return "EXISTS", None
    if current is not None and not replace:
        return "", (
            f"{nodes_file} trusts a different key for '{node_id}' than {pub_hex[:12]}...; "
            "the witness would reject every heartbeat. Remove that line and run init again"
        )
    if current is not None:
        pattern = re.compile(rf"^\s*{re.escape(node_id)}\s*=.*$", re.MULTILINE)
        new_text, count = pattern.subn(lambda _m: entry, text, count=1)
        if count != 1:
            return "", f"could not find the '{node_id}' line in {nodes_file}; replace its key by hand"
        nodes_file.write_text(new_text, encoding="utf-8")
        return "UPDATED", None
    body = text if not text or text.endswith("\n") else text + "\n"
    if "nodes" not in data:
        body += "[nodes]\n"
    nodes_file.write_text(body + entry + "\n", encoding="utf-8")  # [nodes] is the only table, so this lands in it
    return "UPDATED", None


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

    flag_errors = _flag_errors(node_id, spool_path, witness_url)
    if flag_errors:
        return _fail(flag_errors)

    # 1. The profile: an existing config.toml is never overwritten, not even with --force.
    new_config: str | None = None
    if cfg_p.exists():
        profile, errors = load_profile(cfg_p)
        if errors or profile is None:
            return _fail([f"invalid profile in {cfg_p}:"] + [f"  {e}" for e in errors])
        if node_id or spool_path or witness_url:
            print(f"WARN  {cfg_p} exists, so --node-id/--spool/--witness-url were not applied; edit it by hand")
    else:
        template = ex_p.read_text(encoding="utf-8") if ex_p.exists() else DEFAULT_CONFIG_TEMPLATE
        new_config = _update_template(template, node_id, spool_path, witness_url)
        try:
            profile, errors = validate_profile(tomllib.loads(new_config))
        except tomllib.TOMLDecodeError as exc:
            profile, errors = None, [f"TOML syntax error in {ex_p}: {exc}"]
        if errors or profile is None:
            return _fail(["the generated profile is invalid:"] + [f"  {e}" for e in errors])

    actual_node_id = profile["node"]["node_id"]
    priv_path = Path(profile["paths"]["private_key_path"])
    pub_path = Path(profile["paths"]["public_key_path"])
    db_path = Path(profile["paths"]["db_path"])
    nodes_file = Path(profile["witness"]["nodes_file"])

    # 2. Refuse --force before touching anything while a chain exists (RT-12).
    if force and db_path.exists():
        return _fail([
            f"Refusing --force because sidecar database '{db_path}' exists.",
            "Regenerating keys against an existing chain causes witness FORK alerts (RT-12).",
            "Remove the database first if you intend a full reset.",
        ])

    if new_config is not None:
        cfg_p.parent.mkdir(parents=True, exist_ok=True)
        cfg_p.write_text(new_config, encoding="utf-8")
        print(f"[CREATED] {cfg_p}")
    else:
        print(f"[EXISTS] {cfg_p}")

    # 3. The node key pair (save_keypair never overwrites and creates the key owner-only).
    try:
        if force:
            for p in (priv_path, pub_path):
                p.unlink(missing_ok=True)
            priv_key, pub_hex = generate_keypair()
            save_keypair(priv_key, pub_hex, priv_path, pub_path)
            print(f"[CREATED] New key pair at {priv_path} and {pub_path} (--force)")
        else:
            had_priv, had_pub = priv_path.exists(), pub_path.exists()
            _, pub_hex = load_or_generate_keypair(priv_path, pub_path)
            if had_priv and had_pub:
                print(f"[EXISTS] Key pair at {priv_path} and {pub_path}")
            elif had_priv:
                print(f"[CREATED] Restored public key at {pub_path}")
            else:
                print(f"[CREATED] Key pair at {priv_path} and {pub_path}")
    except (ValueError, TypeError, FileNotFoundError, OSError) as exc:
        return _fail([f"node key: {exc}"])
    if sys.platform == "win32":
        print("Note: on Windows, file permissions are not enforced; keep the data folder private.")

    # 4. The witness trust entry for this node.
    status, error = _ensure_witness_node(nodes_file, actual_node_id, pub_hex, replace=force)
    if error:
        return _fail([error])
    print(f"[{status}] {nodes_file}")

    # 5. Next steps (the public key only; the private key is never printed).
    print("\nNext steps:")
    print("1. On the witness laptop, add this line under [nodes] in its witness_nodes.toml:")
    print(f'   {actual_node_id} = "{pub_hex}"')
    print(f"2. Make sure [heartbeat] witness_url points to the witness laptop (now: {profile['heartbeat']['witness_url']})")
    print("3. Run 'coc check'")
    return 0
