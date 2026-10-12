"""Spool vs record: coc check shows what changed in the spool after the sidecar recorded it."""
import json
from pathlib import Path
import sqlite3

import httpx
import pytest

from coc.check import run_check
from coc.init import run_init
from coc.spooldiff import compare_spool, describe
from sidecar.ingest import IngestStopError, ingest_batch
from sidecar.store import init_db
from verifier.checks import verify_offline


def _event(i: int, amount: str = "5000") -> dict:
    """A bank-style event; the defence code never looks inside details."""
    return {"source": "db", "ts": f"2026-10-12T10:00:{i:02d}Z", "actor": f"teller{i}",
            "event_type": "transaction_updated", "details": {"id": i, "new": {"amount": amount}}}


def _write(spool: Path, lines: list[str]) -> None:
    """Write spool lines with \\n endings."""
    spool.parent.mkdir(parents=True, exist_ok=True)
    spool.write_bytes("".join(line + "\n" for line in lines).encode("utf-8"))


def _lines(n: int) -> list[str]:
    """n valid spool lines."""
    return [json.dumps(_event(i)) for i in range(n)]


def _record(tmp_path: Path, lines: list[str]) -> tuple[Path, Path]:
    """Write a spool, ingest it, return (db, spool)."""
    db, spool = tmp_path / "data" / "sidecar.db", tmp_path / "spool" / "events.jsonl"
    _write(spool, lines)
    conn = init_db(db)
    ingest_batch(conn, spool)
    conn.close()
    return db, spool


def _edit_line(spool: Path, line_no: int, old: str, new: str) -> None:
    """Replace text inside one spool line (1-based), keeping every other byte."""
    lines = spool.read_bytes().split(b"\n")
    assert old.encode() in lines[line_no - 1]
    lines[line_no - 1] = lines[line_no - 1].replace(old.encode(), new.encode())
    spool.write_bytes(b"\n".join(lines))


def test_each_entry_remembers_its_spool_line_including_skipped_lines(tmp_path: Path) -> None:
    """A skipped (invalid) line still counts, so line numbers match what an editor shows."""
    db, _ = _record(tmp_path, [json.dumps(_event(0)), "not json", json.dumps(_event(2))])
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT seq, line_no FROM spool_lines ORDER BY seq").fetchall() == [(0, 1), (1, 3)]


def test_unchanged_spool_reports_nothing(tmp_path: Path) -> None:
    """Nothing edited: every tracked line matches."""
    db, spool = _record(tmp_path, _lines(5))
    report = compare_spool(db, spool)
    assert (report["tracked"], report["untracked"], report["changed"], report["removed_lines"]) == (5, 0, [], 0)


def test_edit_to_an_older_line_is_named_and_the_record_keeps_the_original(tmp_path: Path) -> None:
    """The case the sidecar's own last-line check misses: same length, older line."""
    db, spool = _record(tmp_path, _lines(5))
    _edit_line(spool, 2, '"5000"', '"9000"')
    report = compare_spool(db, spool)
    assert [c["line_no"] for c in report["changed"]] == [2]
    assert describe(report["changed"][0]) == \
        'line 2 (record seq 1): details.new.amount recorded "5000", spool now "9000"'
    assert verify_offline(db)["clean"] is True  # the chain itself was never touched


def test_edit_to_the_newest_line_is_named_and_ingest_still_stops(tmp_path: Path) -> None:
    """The teammate's attack (5000 -> 5): reported here, and the sidecar still refuses to go on."""
    db, spool = _record(tmp_path, _lines(3))
    _edit_line(spool, 3, '"5000"', '"5"')
    assert "details.new.amount recorded \"5000\", spool now \"5\"" in describe(compare_spool(db, spool)["changed"][0])
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    with pytest.raises(IngestStopError):
        ingest_batch(conn, spool)
    conn.close()


@pytest.mark.parametrize("field, old, new", [("actor", '"teller1"', '"mallory"'),
                                             ("ts", "10:00:01Z", "09:00:01Z"),
                                             ("event_type", "transaction_updated", "transaction_created")])
def test_every_event_field_is_compared(tmp_path: Path, field: str, old: str, new: str) -> None:
    """Not just amounts: who, when and what kind of event."""
    db, spool = _record(tmp_path, _lines(3))
    _edit_line(spool, 2, old, new)
    fields = compare_spool(db, spool)["changed"][0]["fields"]
    assert [f[0] for f in fields] == [field]


def test_added_or_removed_details_key_is_reported(tmp_path: Path) -> None:
    """A key that appears or disappears counts as a change."""
    db, spool = _record(tmp_path, _lines(3))
    _edit_line(spool, 1, '"id": 0, ', "")
    assert compare_spool(db, spool)["changed"][0]["fields"] == [("details.id", 0, None)]


def test_a_type_change_with_an_equal_value_is_reported(tmp_path: Path) -> None:
    """In Python True == 1, so the comparison must look at types too."""
    db, spool = _record(tmp_path, _lines(3))
    _edit_line(spool, 2, '"id": 1', '"id": true')
    assert compare_spool(db, spool)["changed"][0]["fields"] == [("details.id", 1, True)]


def test_line_that_no_longer_parses_is_reported(tmp_path: Path) -> None:
    """Breaking a line is a change too."""
    db, spool = _record(tmp_path, _lines(3))
    _edit_line(spool, 2, '"source": "db"', '"source": 1.5')
    assert "no longer reads as an event" in describe(compare_spool(db, spool)["changed"][0])


def test_removed_lines_are_counted(tmp_path: Path) -> None:
    """Truncating the spool: the missing recorded lines are named."""
    db, spool = _record(tmp_path, _lines(5))
    _write(spool, _lines(3))
    report = compare_spool(db, spool)
    assert report["removed_lines"] == 2
    assert [c["problem"] for c in report["changed"]] == ["line is gone from the spool"] * 2


def test_inserted_line_gives_a_hint_instead_of_a_wall_of_edits(tmp_path: Path) -> None:
    """An inserted line shifts everything after it; say so."""
    db, spool = _record(tmp_path, _lines(6))
    lines = _lines(6)
    _write(spool, lines[:2] + [json.dumps(_event(99))] + lines[2:])
    report = compare_spool(db, spool)
    assert len(report["changed"]) == 4 and "from 3 on" in report["hint"]


def test_database_from_before_line_tracking(tmp_path: Path) -> None:
    """Old entries are untracked; new ones get the right line numbers after the upgrade."""
    db, spool = _record(tmp_path, _lines(3))
    with sqlite3.connect(db) as c:  # make it look like a DB written before this feature
        c.execute("DROP TABLE spool_lines")
        c.execute("DELETE FROM ingest_state WHERE key = 'spool_line_no'")
    assert compare_spool(db, spool)["untracked"] == 3
    _write(spool, _lines(5))
    conn = init_db(db)  # the sidecar's start recreates the table
    ingest_batch(conn, spool)
    conn.close()
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT seq, line_no FROM spool_lines ORDER BY seq").fetchall() == [(3, 4), (4, 5)]
    report = compare_spool(db, spool)
    assert (report["tracked"], report["untracked"], report["changed"]) == (2, 3, [])


def _check_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> httpx.Client:
    """A coc init folder with 5 recorded lines and a witness that answers with no heartbeats."""
    monkeypatch.chdir(tmp_path)
    assert run_init(config_path=Path("config.toml")) == 0
    _record(tmp_path, _lines(5))
    return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=[])))


def test_coc_check_passes_when_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An untouched spool is one PASS line and does not fail the check."""
    client = _check_folder(tmp_path, monkeypatch)
    assert run_check(config_path=Path("config.toml"), client=client) == 0
    assert "PASS  spool vs record: all 5 recorded lines unchanged" in capsys.readouterr().out


def test_coc_check_fails_and_names_the_change_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A spool edit is a FAIL with the exact change, later layers still run, and nothing is written."""
    client = _check_folder(tmp_path, monkeypatch)
    spool, db = Path("spool/events.jsonl"), Path("data/sidecar.db")
    _edit_line(spool, 4, '"5000"', '"5"')
    before = (db.read_bytes(), spool.read_bytes())
    assert run_check(config_path=Path("config.toml"), client=client) == 1
    out = capsys.readouterr().out
    assert "FAIL  spool vs record:" in out and "The record keeps the original values" in out
    assert 'line 4 (record seq 3): details.new.amount recorded "5000", spool now "5"' in out
    assert "witness:" in out  # the later layers still ran
    assert (db.read_bytes(), spool.read_bytes()) == before


def test_coc_check_warns_on_an_unreadable_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A DB without the tables gives one WARN line, not a crash."""
    client = _check_folder(tmp_path, monkeypatch)
    db = Path("data/sidecar.db")
    db.unlink()
    sqlite3.connect(db).close()  # an empty SQLite file
    run_check(config_path=Path("config.toml"), client=client)
    assert "WARN  spool vs record: cannot compare" in capsys.readouterr().out
