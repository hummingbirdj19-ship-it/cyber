"""Profile schema validation and loading for config.toml profiles."""
from pathlib import Path
import re
import tomllib
from typing import Any

TOP_LEVEL_SECTIONS = {"profile", "node", "paths", "checkpoint", "heartbeat", "witness", "collectors"}
NAME_RE = re.compile(r"^[a-z0-9_-]+$")
# Same rule as witness/checks.py NODE_ID_RE (the sidecar image has no witness package); a test keeps them equal.
NODE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def type_name(val: Any) -> str:
    """Return a human-friendly type name for error messages."""
    if isinstance(val, bool):
        return "boolean"
    if isinstance(val, int):
        return "integer"
    if isinstance(val, float):
        return "float"
    if isinstance(val, str):
        return "string"
    if isinstance(val, list):
        return "list"
    if isinstance(val, dict):
        return "table"
    return type(val).__name__


def _check_type(val: Any, expected: str, path: str, errors: list[str]) -> bool:
    """Validate val against expected type string; record error and return False on mismatch."""
    actual = type_name(val)
    if expected == "string" and not isinstance(val, str):
        errors.append(f"{path}: expected string, got {actual}")
        return False
    if expected == "integer" and (not isinstance(val, int) or isinstance(val, bool)):
        errors.append(f"{path}: expected integer, got {actual}")
        return False
    if expected == "boolean" and not isinstance(val, bool):
        errors.append(f"{path}: expected boolean, got {actual}")
        return False
    if expected == "list" and not isinstance(val, list):
        errors.append(f"{path}: expected list, got {actual}")
        return False
    if expected == "table" and not isinstance(val, dict):
        errors.append(f"{path}: expected table, got {actual}")
        return False
    if expected == "list of strings":
        if not isinstance(val, list):
            errors.append(f"{path}: expected list of strings, got {actual}")
            return False
        if not all(isinstance(x, str) for x in val):
            errors.append(f"{path}: expected list of strings, got list containing non-string items")
            return False
    return True


def validate_profile(data: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate a parsed profile dict against the schema and return (profile_dict, errors)."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return None, ["profile root must be a table"]

    for k in data:
        if k not in TOP_LEVEL_SECTIONS:
            errors.append(f"{k}: unknown key")

    # [profile]
    prof = data.get("profile")
    profile_out: dict[str, Any] = {"name": "", "event_types": []}
    if prof is None:
        errors.append("profile: missing required section")
    elif not isinstance(prof, dict):
        errors.append(f"profile: expected table, got {type_name(prof)}")
    else:
        for k in prof:
            if k not in ("name", "event_types"):
                errors.append(f"profile.{k}: unknown key")
        if "name" not in prof:
            errors.append("profile.name: missing required key")
        elif _check_type(prof["name"], "string", "profile.name", errors):
            profile_out["name"] = prof["name"]
        if "event_types" in prof:
            if _check_type(prof["event_types"], "list of strings", "profile.event_types", errors):
                profile_out["event_types"] = prof["event_types"]

    # [node]
    node = data.get("node")
    node_out: dict[str, Any] = {"node_id": ""}
    if node is None:
        errors.append("node: missing required section")
    elif not isinstance(node, dict):
        errors.append(f"node: expected table, got {type_name(node)}")
    else:
        for k in node:
            if k != "node_id":
                errors.append(f"node.{k}: unknown key")
        if "node_id" not in node:
            errors.append("node.node_id: missing required key")
        elif _check_type(node["node_id"], "string", "node.node_id", errors):
            if not NODE_ID_RE.fullmatch(node["node_id"]):
                errors.append(f"node.node_id: must match {NODE_ID_RE.pattern} (the witness rejects anything else)")
            else:
                node_out["node_id"] = node["node_id"]

    # [paths]
    paths = data.get("paths", {})
    paths_out: dict[str, Any] = {
        "spool_path": "spool/events.jsonl",
        "db_path": "data/sidecar.db",
        "private_key_path": "data/node1.key",
        "public_key_path": "data/node1.pub",
        "collector_state_dir": "data/collectors",
    }
    if not isinstance(paths, dict):
        errors.append(f"paths: expected table, got {type_name(paths)}")
    else:
        for k, v in paths.items():
            if k not in paths_out:
                errors.append(f"paths.{k}: unknown key")
            elif _check_type(v, "string", f"paths.{k}", errors):
                paths_out[k] = v

    # [checkpoint]
    cp = data.get("checkpoint", {})
    cp_out: dict[str, Any] = {"every_n": 50, "every_s": 60}
    if not isinstance(cp, dict):
        errors.append(f"checkpoint: expected table, got {type_name(cp)}")
    else:
        for k, v in cp.items():
            if k not in cp_out:
                errors.append(f"checkpoint.{k}: unknown key")
            elif _check_type(v, "integer", f"checkpoint.{k}", errors):
                if v <= 0:
                    errors.append(f"checkpoint.{k}: must be greater than 0")
                else:
                    cp_out[k] = v

    # [heartbeat]
    hb = data.get("heartbeat", {})
    hb_out: dict[str, Any] = {"interval_s": 30, "busy_interval_s": 2, "witness_url": "http://localhost:8800"}
    if not isinstance(hb, dict):
        errors.append(f"heartbeat: expected table, got {type_name(hb)}")
    else:
        for k, v in hb.items():
            if k not in hb_out:
                errors.append(f"heartbeat.{k}: unknown key")
            elif k == "interval_s":
                if _check_type(v, "integer", "heartbeat.interval_s", errors):
                    if v <= 0:
                        errors.append("heartbeat.interval_s: must be greater than 0")
                    else:
                        hb_out[k] = v
            elif k == "busy_interval_s":
                if _check_type(v, "integer", "heartbeat.busy_interval_s", errors):
                    if v < 0:
                        errors.append("heartbeat.busy_interval_s: must be 0 (off) or greater")
                    else:
                        hb_out[k] = v
            elif k == "witness_url":
                if _check_type(v, "string", "heartbeat.witness_url", errors):
                    if not re.fullmatch(r"https?://\S+", v):
                        errors.append("heartbeat.witness_url: must look like http://<host>:<port>")
                    else:
                        hb_out[k] = v

    if 0 < hb_out["busy_interval_s"] and hb_out["busy_interval_s"] >= hb_out["interval_s"]:
        errors.append("heartbeat.busy_interval_s: must be less than interval_s (or 0 to turn it off)")

    # [witness]
    wit = data.get("witness", {})
    wit_out: dict[str, Any] = {
        "db_path": "data/witness.db",
        "port": 8800,
        "nodes_file": "data/witness_nodes.toml",
        "late_grace_s": 10,
    }
    if not isinstance(wit, dict):
        errors.append(f"witness: expected table, got {type_name(wit)}")
    else:
        for k, v in wit.items():
            if k not in wit_out:
                errors.append(f"witness.{k}: unknown key")
            elif k == "late_grace_s":
                if _check_type(v, "integer", "witness.late_grace_s", errors):
                    if v < 1:
                        errors.append("witness.late_grace_s: must be at least 1")
                    else:
                        wit_out[k] = v
            elif k == "port":
                if _check_type(v, "integer", "witness.port", errors):
                    if not 1 <= v <= 65535:
                        errors.append("witness.port: must be between 1 and 65535")
                    else:
                        wit_out[k] = v
            else:
                if _check_type(v, "string", f"witness.{k}", errors):
                    wit_out[k] = v

    # [[collectors]]
    collectors = data.get("collectors", [])
    collectors_out: list[dict[str, Any]] = []
    if not isinstance(collectors, list):
        errors.append(f"collectors: expected list of tables, got {type_name(collectors)}")
    else:
        for i, c in enumerate(collectors):
            prefix = f"collectors[{i}]"
            if not isinstance(c, dict):
                errors.append(f"{prefix}: expected table, got {type_name(c)}")
                continue
            for req in ("name", "type", "source"):
                if req not in c:
                    errors.append(f"{prefix}.{req}: missing required key")
            c_name = c.get("name")
            if isinstance(c_name, str) and not NAME_RE.fullmatch(c_name):
                errors.append(f"{prefix}.name: must match ^[a-z0-9_-]+$")
            elif isinstance(c_name, str) and any(o.get("name") == c_name for o in collectors_out):
                errors.append(f"{prefix}.name: '{c_name}' is used by another collector (names must be unique)")
            c_type = c.get("type")
            if isinstance(c_type, str) and c_type not in ("json_lines", "access_log", "db_audit"):
                errors.append(f"{prefix}.type: must be one of json_lines, access_log, db_audit")
            c_source = c.get("source")
            if isinstance(c_source, str) and c_source not in ("app", "db", "host"):
                errors.append(f"{prefix}.source: must be one of app, db, host")
            collectors_out.append(c)

    if errors:
        return None, errors

    return {
        "profile": profile_out,
        "node": node_out,
        "paths": paths_out,
        "checkpoint": cp_out,
        "heartbeat": hb_out,
        "witness": wit_out,
        "collectors": collectors_out,
    }, []


def load_profile(path: Path | str) -> tuple[dict[str, Any] | None, list[str]]:
    """Load and validate config.toml; return (parsed_profile, errors)."""
    p = Path(path)
    if not p.exists():
        return None, [f"file not found: {p}"]
    try:
        with open(p, "rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        return None, [f"TOML syntax error: {exc}"]
    except OSError as exc:
        return None, [f"error reading {p}: {exc}"]
    return validate_profile(raw)
