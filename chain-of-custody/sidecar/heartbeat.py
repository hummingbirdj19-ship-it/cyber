"""Heartbeat client and checkpoint uploader for witness service."""
import logging
import math
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx

from sidecar.canon import GENESIS_PREV_HASH
from sidecar.keys import sign_payload
from sidecar.store import get_connection, get_last_entry, get_state, set_state

logger = logging.getLogger(__name__)
DEFAULT_TIMEOUT_S = 2.5
CONTAINER_MARKER = Path("/.dockerenv")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
NEXT_DUE_FIELD = "next_due_s"


@dataclass
class WitnessCompat:
    """What this sidecar learned about the witness: an older witness rejects next_due_s."""
    no_next_due: bool = False


def resolve_witness_url(witness_url: str) -> tuple[str, str | None]:
    """In a container, point a localhost witness URL at the host machine; return (url, note or None)."""
    parts = urlsplit(witness_url)
    if not CONTAINER_MARKER.exists() or parts.hostname not in LOCAL_HOSTS:
        return witness_url, None
    netloc = "host.docker.internal" + (f":{parts.port}" if parts.port else "")
    url = urlunsplit(parts._replace(netloc=netloc))
    return url, (f"running in a container, where {parts.hostname} is the container itself; "
                 f"using the host at {url} instead")


def get_persisted_counter(conn: sqlite3.Connection) -> int:
    """Return the persisted heartbeat counter from store, defaulting to 0."""
    val = get_state(conn, "hb_counter")
    return int(val) if val is not None and val.isdigit() else 0


def set_persisted_counter(conn: sqlite3.Connection, counter: int) -> None:
    """Persist the heartbeat counter in the ingest_state store."""
    set_state(conn, "hb_counter", str(counter))


def get_uploaded_tree_size(conn: sqlite3.Connection) -> int:
    """Return the tree size of the latest checkpoint successfully uploaded to witness."""
    val = get_state(conn, "checkpoint_uploaded_tree_size")
    return int(val) if val is not None and val.isdigit() else 0


def set_uploaded_tree_size(conn: sqlite3.Connection, tree_size: int) -> None:
    """Persist the latest uploaded checkpoint tree size."""
    set_state(conn, "checkpoint_uploaded_tree_size", str(tree_size))


def current_head_seq(conn: sqlite3.Connection) -> int:
    """Return the newest entry's seq, or -1 for an empty log."""
    last_entry = get_last_entry(conn)
    return -1 if last_entry is None else int(last_entry["seq"])


def build_heartbeat(
    conn: sqlite3.Connection, node_id: str, private_key: Ed25519PrivateKey, counter: int, ts: str | None = None,
    next_due_s: int | None = None,
) -> dict[str, Any]:
    """Build and sign a heartbeat from the database head; next_due_s promises the next one (signed too)."""
    last_entry = get_last_entry(conn)
    if last_entry is None:
        head_seq, head_hash, tree_size = -1, GENESIS_PREV_HASH, 0
    else:
        head_seq, head_hash = int(last_entry["seq"]), str(last_entry["hash"])
        tree_size = head_seq + 1

    if ts is None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    payload = {
        "node_id": node_id, "hb_counter": counter, "head_seq": head_seq,
        "head_hash": head_hash, "tree_size": tree_size, "ts": ts,
    }
    if next_due_s is not None:
        payload[NEXT_DUE_FIELD] = next_due_s
    payload["signature"] = sign_payload(private_key, payload)
    return payload


def send_heartbeat(
    client: httpx.Client, witness_url: str, payload: dict[str, Any], timeout_s: float = DEFAULT_TIMEOUT_S
) -> tuple[bool, dict[str, Any] | None]:
    """POST signed heartbeat to witness; returns (success, response_json)."""
    resp = client.post(f"{witness_url.rstrip('/')}/heartbeat", json=payload, timeout=timeout_s)
    if resp.status_code in (200, 422):
        return (resp.status_code == 200, resp.json())
    return (False, None)


def upload_pending_checkpoints(
    conn: sqlite3.Connection, client: httpx.Client, witness_url: str, timeout_s: float = DEFAULT_TIMEOUT_S
) -> int:
    """Upload all un-uploaded signed checkpoints to witness in ascending order."""
    uploaded_size = get_uploaded_tree_size(conn)
    cur = conn.execute(
        "SELECT tree_size, head_seq, head_hash, root_hash, ts, signature, node_id "
        "FROM checkpoints WHERE tree_size > ? ORDER BY tree_size ASC",
        (uploaded_size,),
    )
    count, url = 0, f"{witness_url.rstrip('/')}/checkpoint"
    for r in cur.fetchall():
        payload = {
            "node_id": r["node_id"], "tree_size": int(r["tree_size"]), "head_seq": int(r["head_seq"]),
            "head_hash": r["head_hash"], "root_hash": r["root_hash"], "ts": r["ts"], "signature": r["signature"],
        }
        resp = client.post(url, json=payload, timeout=timeout_s)
        if resp.status_code == 200 and resp.json().get("status") == "stored":
            set_uploaded_tree_size(conn, int(r["tree_size"]))
            count += 1
        else:
            logger.warning("Witness did not store checkpoint tree_size=%d (HTTP %d): %.200s",
                           int(r["tree_size"]), resp.status_code, resp.text)
            break
    return count


def _rejects_next_due(res: dict[str, Any] | None) -> bool:
    """True if the witness answered 422 because it does not know next_due_s (a witness from before v0.4)."""
    errors = res.get("errors") if isinstance(res, dict) else None
    return isinstance(errors, list) and f"unexpected field '{NEXT_DUE_FIELD}'" in errors


def pulse_once(
    conn: sqlite3.Connection,
    client: httpx.Client,
    node_id: str,
    private_key: Ed25519PrivateKey,
    witness_url: str,
    failure_streak: int,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    next_due_s: int | None = None,
    compat: WitnessCompat | None = None,
) -> tuple[bool, int]:
    """Execute one heartbeat pulse and checkpoint upload attempt, returning success and streak."""
    compat = compat if compat is not None else WitnessCompat()
    # Every attempt gets a new counter, persisted before sending. A lost reply or a crash after
    # sending can then never make the next pulse reuse a counter the witness already stored
    # (which would be a false COUNTER_REPLAY). Failed sends leave gaps; the witness allows them.
    counter = get_persisted_counter(conn) + 1
    set_persisted_counter(conn, counter)

    promise = None if compat.no_next_due else next_due_s
    payload = build_heartbeat(conn, node_id, private_key, counter, next_due_s=promise)
    try:
        ok, res = send_heartbeat(client, witness_url, payload, timeout_s=timeout_s)
        if not ok and promise is not None and _rejects_next_due(res):
            # The witness stored nothing (422), so the same counter is still unused. Keep heartbeats
            # flowing; that witness cannot raise LATE until it is upgraded.
            compat.no_next_due = True
            logger.warning("Witness at %s is older than CONTRACT v0.4 (no next_due_s): sending plain "
                           "heartbeats; upgrade the witness to get LATE alerts", witness_url)
            payload = build_heartbeat(conn, node_id, private_key, counter)
            ok, res = send_heartbeat(client, witness_url, payload, timeout_s=timeout_s)
        if ok:
            if failure_streak > 0:
                logger.info("Witness reconnected at %s (resumed at hb_counter %d)", witness_url, counter)
            try:
                upload_pending_checkpoints(conn, client, witness_url, timeout_s=timeout_s)
            except Exception as upload_err:
                logger.warning("Checkpoint upload error: %s", upload_err)
            return (True, 0)
        new_streak = failure_streak + 1
        if new_streak == 1:
            logger.warning("Witness returned error at %s: %s", witness_url, res)
        return (False, new_streak)
    except Exception as err:
        new_streak = failure_streak + 1
        if new_streak == 1:
            logger.warning("Witness unreachable at %s: %s", witness_url, err)
        return (False, new_streak)


def run_heartbeat_loop(
    db_path: Path | str,
    node_id: str,
    private_key: Ed25519PrivateKey,
    witness_url: str,
    interval_s: float,
    stop_event: threading.Event,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    busy_interval_s: float = 0,
) -> None:
    """Send heartbeats until stop_event is set.

    busy_interval_s = 0: one heartbeat every interval_s (the v0.3 behaviour, no next_due_s).
    busy_interval_s > 0: while new entries arrive, one heartbeat every busy_interval_s promising the
    next within busy_interval_s; once the head stops moving, one every interval_s promising interval_s.
    New entries after a quiet spell are reported within busy_interval_s, not at the next idle beat.
    """
    conn = get_connection(db_path)
    client = httpx.Client()
    compat = WitnessCompat()
    dynamic = busy_interval_s > 0
    streak = 0
    last_attempt: float | None = None  # monotonic time of the last send attempt
    promised = 0.0                       # seconds the last attempt promised until the next one
    last_head: int | None = None         # head_seq the last attempt reported
    try:
        while not stop_event.is_set():
            try:
                head = current_head_seq(conn)
                moved = head != last_head
                due = last_attempt is None or time.monotonic() - last_attempt >= promised
                if not dynamic or moved or due:
                    promise = busy_interval_s if dynamic and moved else interval_s
                    last_attempt, promised, last_head = time.monotonic(), promise, head
                    _, streak = pulse_once(conn, client, node_id, private_key, witness_url, streak,
                                           timeout_s=timeout_s, compat=compat,
                                           next_due_s=math.ceil(promise) if dynamic else None)
            except Exception as exc:  # e.g. "database is locked": one bad pulse must not end heartbeats
                streak += 1
                if streak == 1:
                    logger.error("Heartbeat pulse failed (will retry every %ss): %s", interval_s, exc)
            if not dynamic:
                wait = interval_s
            else:
                left = promised - (time.monotonic() - last_attempt) if last_attempt is not None else 0.0
                wait = max(0.05, min(busy_interval_s, left))
            stop_event.wait(timeout=wait)
    finally:
        client.close()
        conn.close()


def start_heartbeat_thread(
    db_path: Path | str,
    node_id: str,
    private_key: Ed25519PrivateKey,
    witness_url: str,
    interval_s: float,
    stop_event: threading.Event,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    busy_interval_s: float = 0,
) -> threading.Thread:
    """Start and return a background thread running the heartbeat loop."""
    th = threading.Thread(
        target=run_heartbeat_loop,
        args=(db_path, node_id, private_key, witness_url, interval_s, stop_event, timeout_s, busy_interval_s),
        daemon=True,
        name="sidecar-heartbeat",
    )
    th.start()
    return th
