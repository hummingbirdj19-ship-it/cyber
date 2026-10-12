"""Spool vs record: show what changed in the spool file after the sidecar recorded it.

The hash chain is the proof and is never touched here. This only re-reads the spool and compares
each tracked line, field by field, with the entry recorded from it (table spool_lines), so an edit to
the raw evidence is reported with the original value the record still holds. Read-only.
"""
import json
from pathlib import Path
import sqlite3
from typing import Any

from tools.validate_spool import parse_line

EVENT_FIELDS = ("source", "ts", "actor", "event_type")
RUN_HINT = 3  # this many changed lines in a row at the end suggests lines were inserted or removed


def _flatten(value: Any, path: str) -> dict[str, Any]:
    """Map dotted paths to leaf values (lists are compared whole)."""
    if isinstance(value, dict) and value:
        out: dict[str, Any] = {}
        for key, sub in value.items():
            out.update(_flatten(sub, f"{path}.{key}"))
        return out
    return {path: value}


def field_changes(recorded: dict[str, Any], current: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    """Return (field path, recorded value, current value) for every field that differs."""
    old: dict[str, Any] = {f: recorded[f] for f in EVENT_FIELDS}
    new: dict[str, Any] = {f: current.get(f) for f in EVENT_FIELDS}
    old.update(_flatten(recorded["details"], "details"))
    new.update(_flatten(current.get("details"), "details"))
    missing = object()
    changes = []
    for path in sorted(set(old) | set(new)):
        before, after = old.get(path, missing), new.get(path, missing)
        if before != after or type(before) is not type(after):
            changes.append((path, None if before is missing else before, None if after is missing else after))
    return changes


def compare_spool(db_path: Path, spool_path: Path) -> dict[str, Any]:
    """Compare every tracked spool line with its recorded entry; never writes anything."""
    report: dict[str, Any] = {"tracked": 0, "untracked": 0, "changed": [], "removed_lines": 0, "hint": None}
    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'spool_lines'").fetchone()
        total = int(conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0])
        if not has_table:
            report["untracked"] = total
            return report
        rows = conn.execute(
            "SELECT e.seq, e.ts, e.source, e.actor, e.event_type, e.details, s.line_no "
            "FROM entries e JOIN spool_lines s ON s.seq = e.seq ORDER BY e.seq").fetchall()
        consumed = conn.execute("SELECT value FROM ingest_state WHERE key = 'spool_line_no'").fetchone()
    finally:
        conn.close()

    report["tracked"], report["untracked"] = len(rows), total - len(rows)
    data = spool_path.read_bytes() if spool_path.is_file() else b""
    lines = data.split(b"\n")[:-1]  # complete lines only; a half-written last line is not compared
    if consumed is not None and len(lines) < int(consumed[0]):
        report["removed_lines"] = int(consumed[0]) - len(lines)

    for row in rows:
        recorded = {f: row[f] for f in EVENT_FIELDS}
        recorded["details"] = json.loads(row["details"])
        item: dict[str, Any] = {"seq": row["seq"], "line_no": row["line_no"]}
        if row["line_no"] > len(lines):
            report["changed"].append({**item, "problem": "line is gone from the spool"})
            continue
        try:
            text = lines[row["line_no"] - 1].decode("utf-8")
        except UnicodeDecodeError:
            report["changed"].append({**item, "problem": "line is no longer valid UTF-8"})
            continue
        current, errors = parse_line(text)
        if current is None:
            report["changed"].append({**item, "problem": f"line no longer reads as an event ({errors[0]})"})
            continue
        changes = field_changes(recorded, current)
        if changes:
            report["changed"].append({**item, "fields": changes})

    # A run of changed lines that reaches the newest tracked line usually means a line was inserted or
    # removed (everything after it shifts), not that each of those lines was edited.
    changed_seqs = {c["seq"] for c in report["changed"]}
    run = 0
    while run < len(rows) and rows[len(rows) - 1 - run]["seq"] in changed_seqs:
        run += 1
    if run >= RUN_HINT:
        start = rows[len(rows) - run]["line_no"]
        report["hint"] = (f"every line from {start} on differs: lines were probably inserted or removed "
                          "around there, which shifts everything after it")
    return report


def describe(item: dict[str, Any]) -> str:
    """One human-readable line for a changed spool line."""
    where = f"line {item['line_no']} (record seq {item['seq']})"
    if "problem" in item:
        return f"{where}: {item['problem']}"
    parts = [f"{path} recorded {json.dumps(before)}, spool now {json.dumps(after)}"
             for path, before, after in item["fields"]]
    return f"{where}: " + "; ".join(parts)
