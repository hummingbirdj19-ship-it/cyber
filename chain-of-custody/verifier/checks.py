"""Offline verifier checks for hash chain and Merkle checkpoint integrity."""
import json
import sqlite3
from pathlib import Path
from typing import Any

from sidecar.canon import GENESIS_PREV_HASH, compute_entry_hash
from sidecar.keys import verify_payload
from sidecar.merkle import merkle_root_hex


def _verdict(code: str, seq_from: int, seq_to: int, detail: str) -> dict[str, Any]:
    """Build one verdict object as in CONTRACT.md section 6."""
    return {"code": code, "seq_from": seq_from, "seq_to": seq_to, "detail": detail}


def _recompute_hash(row: sqlite3.Row) -> str | None:
    """Recompute an entry's hash from its stored fields, or None if the row cannot be read."""
    try:
        return compute_entry_hash(
            seq=int(row["seq"]),
            ts=row["ts"],
            source=row["source"],
            actor=row["actor"],
            event_type=row["event_type"],
            details=json.loads(row["details"]),
            prev_hash=row["prev_hash"],
        )
    except (ValueError, TypeError, RecursionError):
        return None


def verify_entries(conn: sqlite3.Connection) -> tuple[int, list[dict[str, Any]]]:
    """Verify log entries for sequence continuity, canonical hashing, and chain linkage."""
    rows = conn.execute("SELECT * FROM entries ORDER BY seq ASC").fetchall()
    verdicts: list[dict[str, Any]] = []
    edited_seqs: set[int] = set()
    expected_seq = 0
    prev_row: sqlite3.Row | None = None

    for row in rows:
        seq = int(row["seq"])

        # SEQ_GAP: anything other than the next expected seq (missing range, or a seq below it such as -1).
        if seq > expected_seq:
            verdicts.append(_verdict("SEQ_GAP", expected_seq, seq - 1,
                                     f"missing sequence numbers from {expected_seq} to {seq - 1}"))
        elif seq < expected_seq:
            verdicts.append(_verdict("SEQ_GAP", seq, seq, f"unexpected seq {seq} (expected {expected_seq})"))
        expected_seq = max(expected_seq, seq + 1)

        # EDIT: stored hash differs from the hash recomputed from the stored fields.
        recomputed = _recompute_hash(row)
        if recomputed != row["hash"]:
            edited_seqs.add(seq)
            detail = ("entry fields cannot be read or re-hashed" if recomputed is None
                      else f"stored hash {row['hash']} differs from recomputed hash {recomputed}")
            verdicts.append(_verdict("EDIT", seq, seq, detail))

        # CHAIN_BREAK: prev_hash does not link to the previous entry (skipped right after an EDIT,
        # so one edit gives one verdict).
        if seq == 0:
            if row["prev_hash"] != GENESIS_PREV_HASH:
                verdicts.append(_verdict("CHAIN_BREAK", 0, 0,
                                         f"entry 0 prev_hash {row['prev_hash']} is not the genesis hash"))
        elif seq > 0 and prev_row is not None and int(prev_row["seq"]) not in edited_seqs:
            if row["prev_hash"] != prev_row["hash"]:
                verdicts.append(_verdict("CHAIN_BREAK", seq, seq,
                                         f"prev_hash {row['prev_hash']} does not match previous entry hash "
                                         f"{prev_row['hash']}"))
        prev_row = row

    return len(rows), verdicts


def _check_one_checkpoint(conn: sqlite3.Connection, public_key_hex: str, row: sqlite3.Row) -> dict[str, Any] | None:
    """Return the verdict for one stored checkpoint, or None if it verifies against the local entries."""
    try:
        tree_size, head_seq = int(row["tree_size"]), int(row["head_seq"])
        payload = {"node_id": row["node_id"], "tree_size": tree_size, "head_seq": head_seq,
                   "head_hash": row["head_hash"], "root_hash": row["root_hash"], "ts": row["ts"]}
    except (ValueError, TypeError):
        return _verdict("BAD_CHECKPOINT_SIGNATURE", 0, 0, "checkpoint row cannot be read")
    if not verify_payload(public_key_hex, row["signature"], payload):
        return _verdict("BAD_CHECKPOINT_SIGNATURE", 0, head_seq,
                        f"checkpoint signature for tree_size {tree_size} is invalid")

    hashes = [r[0] for r in conn.execute(
        "SELECT hash FROM entries WHERE seq >= 0 AND seq < ? ORDER BY seq ASC", (tree_size,))]
    if len(hashes) != tree_size:
        return _verdict("CHECKPOINT_MISMATCH", 0, head_seq,
                        f"signed checkpoint covers {tree_size} entries (seq 0..{head_seq}) "
                        f"but only {len(hashes)} are present")
    try:
        computed_root = merkle_root_hex(hashes)
    except ValueError:
        computed_root = None
    if computed_root != row["root_hash"] or hashes[-1] != row["head_hash"]:
        return _verdict("CHECKPOINT_MISMATCH", 0, head_seq,
                        f"recomputed Merkle root {computed_root} differs from checkpoint root {row['root_hash']}")
    return None


def verify_checkpoints(conn: sqlite3.Connection, public_key_hex: str) -> list[dict[str, Any]]:
    """Verify signatures and recomputed Merkle roots on all stored checkpoints."""
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='checkpoints'")
    if not cur.fetchone():
        return []
    rows = conn.execute("SELECT * FROM checkpoints ORDER BY tree_size ASC").fetchall()
    return [v for v in (_check_one_checkpoint(conn, public_key_hex, r) for r in rows) if v is not None]


def verify_offline(db_path: Path, public_key_path: Path | None = None) -> dict[str, Any]:
    """Execute all offline integrity checks against the database and return report dict."""
    if not db_path.exists():
        raise FileNotFoundError(f"Database file does not exist: {db_path}")
    if public_key_path is not None and not public_key_path.exists():
        # Fail closed: without the key the checkpoints cannot be checked, so "clean" would be a lie.
        raise FileNotFoundError(f"Public key file does not exist: {public_key_path}; cannot verify checkpoints")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")  # one read snapshot for entries and checkpoints
        checked_entries, verdicts = verify_entries(conn)
        if public_key_path is not None:
            pub_hex = public_key_path.read_text(encoding="utf-8").strip()
            verdicts.extend(verify_checkpoints(conn, pub_hex))
    finally:
        conn.close()

    return {
        "clean": len(verdicts) == 0,
        "checked_entries": checked_entries,
        "verdicts": verdicts,
    }
