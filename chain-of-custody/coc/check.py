"""System readiness and configuration checker for Chain of Custody."""
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx

from coc.profile import load_profile
from coc.spooldiff import compare_spool, describe
from sidecar.heartbeat import resolve_witness_url
from sidecar.keys import public_key_hex
from tools.validate_spool import validate_file


MAX_CHANGES_SHOWN = 10


def _check_spool_vs_record(db_path: Path, spool_path: Path) -> bool:
    """Print the spool-vs-record layer; return True if the spool changed after it was recorded."""
    if not db_path.exists():
        print("PASS  spool vs record: skipped (nothing recorded yet)")
        return False
    try:
        report = compare_spool(db_path, spool_path)
    except (sqlite3.Error, OSError, ValueError) as exc:
        print(f"WARN  spool vs record: cannot compare ({type(exc).__name__}: {exc}); run 'coc verify'")
        return False
    if report["tracked"] == 0:
        if report["untracked"]:
            print(f"WARN  spool vs record: {report['untracked']} entries were recorded before line tracking; "
                  "only newer entries can be compared")
        else:
            print("PASS  spool vs record: skipped (nothing recorded yet)")
        return False
    changed = report["changed"]
    if not changed and not report["removed_lines"]:
        note = f" ({report['untracked']} older entries not tracked)" if report["untracked"] else ""
        print(f"PASS  spool vs record: all {report['tracked']} recorded lines unchanged in {spool_path}{note}")
        return False
    print(f"FAIL  spool vs record: {spool_path} was changed after it was recorded "
          f"({len(changed)} line(s) differ). The record keeps the original values; "
          "run 'coc verify' to confirm the record itself is intact")
    for item in changed[:MAX_CHANGES_SHOWN]:
        print(f"      {describe(item)}")
    if len(changed) > MAX_CHANGES_SHOWN:
        print(f"      ... and {len(changed) - MAX_CHANGES_SHOWN} more")
    if report["removed_lines"]:
        print(f"      {report['removed_lines']} line(s) were removed from the spool")
    if report["hint"]:
        print(f"      Note: {report['hint']}")
    return True


def _check_sqlite_ro(db_path: Path) -> str | None:
    """Verify that an existing SQLite file can be opened read-only."""
    if not db_path.exists():
        return None
    try:
        uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        conn.close()
        return None
    except Exception as exc:
        return f"database '{db_path}' cannot be opened read-only: {exc}"


def run_check(
    config_path: Path | str = Path("config.toml"),
    client: httpx.Client | None = None,
    timeout: float = 3.0,
) -> int:
    """Execute coc check across profile, paths, spool, witness, and collectors layers."""
    try:
        cfg_p = Path(config_path)

        # -------------------------------------------------------------
        # Layer 1: Profile
        # -------------------------------------------------------------
        profile, errors = load_profile(cfg_p)
        if errors or profile is None:
            for err in errors:
                print(f"FAIL  profile: {err}")
            return 1
        print(f"PASS  profile: '{cfg_p}' is valid")

        # -------------------------------------------------------------
        # Layer 2: Paths
        # -------------------------------------------------------------
        layer2_failed = False
        priv_path = Path(profile["paths"]["private_key_path"])
        pub_path = Path(profile["paths"]["public_key_path"])
        db_path = Path(profile["paths"]["db_path"])
        spool_path = Path(profile["paths"]["spool_path"])

        # Key files existence
        if not priv_path.exists():
            print(f"FAIL  paths: private key file missing: {priv_path}")
            layer2_failed = True
        if not pub_path.exists():
            print(f"FAIL  paths: public key file missing: {pub_path}")
            layer2_failed = True

        # Key files match
        if priv_path.exists() and pub_path.exists():
            try:
                pem = priv_path.read_bytes()
                priv_key = serialization.load_pem_private_key(pem, password=None)
                if not isinstance(priv_key, Ed25519PrivateKey):
                    print(f"FAIL  paths: private key {priv_path} is not an Ed25519 key")
                    layer2_failed = True
                else:
                    derived_pub = public_key_hex(priv_key)
                    stored_pub = pub_path.read_text(encoding="utf-8").strip()
                    if derived_pub != stored_pub:
                        print(f"FAIL  paths: public key {pub_path} does not match private key {priv_path}")
                        layer2_failed = True
            except Exception as exc:
                print(f"FAIL  paths: could not verify key pair: {exc}")
                layer2_failed = True

        # Data directory writable
        data_dir = db_path.parent
        if data_dir.exists():
            if not os.access(data_dir, os.W_OK):
                print(f"FAIL  paths: data directory '{data_dir}' is not writable")
                layer2_failed = True
        else:
            # Check parent directory
            parent_dir = data_dir.parent if str(data_dir.parent) != "." else Path(".")
            if not os.access(parent_dir, os.W_OK):
                print(f"FAIL  paths: cannot create data directory '{data_dir}' (parent not writable)")
                layer2_failed = True

        # Database read-only check if DB already exists
        db_err = _check_sqlite_ro(db_path)
        if db_err:
            print(f"FAIL  paths: {db_err}")
            layer2_failed = True

        # Spool file readability & status
        spool_empty_or_missing = False
        if not spool_path.exists():
            print(f"WARN  paths: spool file '{spool_path}' does not exist (platform has not written yet)")
            spool_empty_or_missing = True
        elif not os.access(spool_path, os.R_OK):
            print(f"FAIL  paths: spool file '{spool_path}' is not readable")
            layer2_failed = True
        elif spool_path.stat().st_size == 0:
            print(f"WARN  paths: spool file '{spool_path}' is empty (platform has not written yet)")
            spool_empty_or_missing = True

        if layer2_failed:
            return 1

        print("PASS  paths: key files match and data directory is writable")

        # -------------------------------------------------------------
        # Layer 2b: Spool vs record (does not stop the later layers)
        # -------------------------------------------------------------
        record_failed = _check_spool_vs_record(db_path, spool_path)

        # -------------------------------------------------------------
        # Layer 3: Spool
        # -------------------------------------------------------------
        if spool_empty_or_missing:
            print("PASS  spool: skipped (spool file does not exist or is empty)")
        else:
            allowed_types = set(profile["profile"]["event_types"]) or None
            try:
                total, spool_errors = validate_file(spool_path, allowed_types)
                if spool_errors:
                    print(f"FAIL  spool: {len(spool_errors)} error(s) found in {spool_path}")
                    for num, msg in spool_errors:
                        print(f"      line {num}: {msg}")
                    return 1
                print(f"PASS  spool: {total} line(s) valid in {spool_path}")
            except Exception as exc:
                print(f"FAIL  spool: error validating {spool_path}: {exc}")
                return 1

        # -------------------------------------------------------------
        # Layer 4: Witness
        # -------------------------------------------------------------
        node_id = profile["node"]["node_id"]
        # Same rule the sidecar uses, so this check never passes while heartbeats cannot get through.
        witness_url, note = resolve_witness_url(profile["heartbeat"]["witness_url"].rstrip("/"))
        if note:
            print(f"NOTE  witness: {note}")
        hb_url = f"{witness_url}/heartbeats"

        try:
            if client is not None:
                resp = client.get(hb_url, params={"node_id": node_id})
            else:
                with httpx.Client(timeout=timeout) as cl:
                    resp = cl.get(hb_url, params={"node_id": node_id})
        except Exception as exc:
            print(f"FAIL  witness: unreachable at {witness_url} ({type(exc).__name__}: {exc})")
            print("      Network checklist:")
            print("      - The witness laptop's IP address and port (default 8800)")
            print("      - Port 8800 is allowed through the firewall")
            print("      - Both laptops are connected to the same network or hotspot")
            print("      - The witness service is running")
            return 1

        try:
            data = resp.json() if resp.status_code == 200 else None
        except ValueError:
            data = None
        if not isinstance(data, list):
            print(f"FAIL  witness: {witness_url} answered HTTP {resp.status_code}, but not like a witness "
                  "(wrong host or port?)")
            return 1
        if not data:
            print(
                f"WARN  witness: reachable at {witness_url}, but no heartbeats yet for node '{node_id}'\n"
                "      Reminder: the node's key must be in the witness's witness_nodes.toml"
            )
        else:
            print(f"PASS  witness: reachable at {witness_url} ({len(data)} heartbeat(s) recorded for '{node_id}')")

        # -------------------------------------------------------------
        # Layer 5: Collectors
        # -------------------------------------------------------------
        print("PASS  collectors: placeholder (no collectors configured or I3 pending)")

        return 1 if record_failed else 0

    except Exception as exc:
        sys.stderr.write(f"Crash: {type(exc).__name__}: {exc}\n")
        return 2
