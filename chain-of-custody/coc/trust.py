"""coc trust: add a node's public key to the witness's trusted-node file (witness laptop)."""
from pathlib import Path
import re
import sys

from coc.init import ensure_witness_node
from coc.profile import NODE_ID_RE

PUBLIC_KEY_RE = re.compile(r"[0-9a-f]{64}")  # raw Ed25519 public key as lowercase hex (node1.pub)


def run_trust(node_id: str, public_key: str, nodes_file: Path | str, replace: bool = False) -> int:
    """Trust node_id with public_key in nodes_file; exit 0 on success, 1 on a refused input."""
    errors = []
    if not NODE_ID_RE.fullmatch(node_id):
        errors.append(f"node id '{node_id}' must match {NODE_ID_RE.pattern}")
    key = public_key.strip().lower()
    if not PUBLIC_KEY_RE.fullmatch(key):
        errors.append("public key must be 64 hex characters (the contents of the node's .pub file)")
    if errors:
        for err in errors:
            sys.stderr.write(f"Error: {err}\n")
        return 1
    path = Path(nodes_file)
    if path.is_file() and path.read_bytes().startswith(b"\xef\xbb\xbf"):
        sys.stderr.write(f"Error: {path} starts with a byte-order mark (saved as 'UTF-8 with BOM'); "
                         "the witness cannot read it. Delete the file and run coc trust again\n")
        return 1
    status, error = ensure_witness_node(path, node_id, key, replace=replace)
    if error:
        sys.stderr.write(f"Error: {error}\n")
        return 1
    print(f"[{status}] {path}: {node_id} is trusted")
    if status != "EXISTS":
        print("Restart the witness to load it: docker compose -f witness-compose.yml restart witness")
    return 0
