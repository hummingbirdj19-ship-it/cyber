"""CONTRACT v0.4: dynamic heartbeat interval, the signed next_due_s promise and the witness LATE alert."""
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from coc.profile import load_profile
from sidecar import heartbeat as hbm
from sidecar.keys import generate_keypair
from sidecar.store import append_entry, init_db
from verifier.checks import verify_witness
from witness import store as wstore
from witness.app import create_app, main as witness_main, witness_settings

NODE = "node-1"


def _event(i: int) -> dict[str, Any]:
    """One valid RawEvent."""
    return {"ts": "2026-10-13T10:00:00Z", "source": "app", "actor": f"u{i}", "event_type": "transfer",
            "details": {"amount": str(i)}}


class Clock:
    """A witness clock the test moves by hand."""

    def __init__(self) -> None:
        """Start at a fixed epoch."""
        self.t = 1_000_000.0

    def __call__(self) -> float:
        """Current witness time."""
        return self.t


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Any]:
    """A witness on a hand-moved clock (interval 30 s, grace 10 s) and a sidecar DB with one entry."""
    priv, pub = generate_keypair()
    (tmp_path / "nodes.toml").write_text(f'[nodes]\n"{NODE}" = "{pub}"\n', encoding="utf-8")
    clock = Clock()
    app = create_app(nodes_file=tmp_path / "nodes.toml", db_path=tmp_path / "w.db", interval_s=30,
                     late_grace_s=10, now=clock)
    conn = init_db(tmp_path / "s.db")
    append_entry(conn, _event(0))
    return {"tmp": tmp_path, "priv": priv, "app": app, "clock": clock, "conn": conn, "counter": 0}


def _beat(env: dict[str, Any], next_due_s: Any = None, **override: Any) -> httpx.Response:
    """Send one signed heartbeat with the next counter."""
    env["counter"] += 1
    hb = hbm.build_heartbeat(env["conn"], NODE, env["priv"], env["counter"], next_due_s=next_due_s)
    hb.update(override)
    with TestClient(env["app"]) as c:
        return c.post("/heartbeat", json=hb)


def _sweep_at(env: dict[str, Any], t: float) -> list[str]:
    """Move the witness clock to start + t seconds, run the sweep, return the codes raised."""
    env["clock"].t = 1_000_000.0 + t
    return [a["code"] for a in env["app"].state.sweep_silence()]


# --- the promise on the wire ------------------------------------------------------------------

def test_promise_is_signed_stored_and_returned(env: dict[str, Any]) -> None:
    """next_due_s is part of the signed payload; the witness stores it and lists it."""
    assert _beat(env, next_due_s=2).json() == {"status": "ok"}
    with TestClient(env["app"]) as c:
        rows = c.get("/heartbeats", params={"node_id": NODE}).json()
    assert rows[-1]["next_due_s"] == 2


def test_changing_the_promise_breaks_the_signature(env: dict[str, Any]) -> None:
    """A man in the middle cannot stretch a busy promise into an idle one."""
    env["counter"] += 1
    hb = hbm.build_heartbeat(env["conn"], NODE, env["priv"], env["counter"], next_due_s=2)
    hb["next_due_s"] = 3600
    with TestClient(env["app"]) as c:
        res = c.post("/heartbeat", json=hb).json()
    assert [a["code"] for a in res["alerts"]] == ["BAD_SIGNATURE"]


@pytest.mark.parametrize("bad", [0, -1, 3601, True, "2", 2.5, None])
def test_bad_promise_is_rejected(env: dict[str, Any], bad: Any) -> None:
    """Only an integer from 1 to 3600 is a promise."""
    env["counter"] += 1
    hb = hbm.build_heartbeat(env["conn"], NODE, env["priv"], env["counter"])
    hb["next_due_s"] = bad
    with TestClient(env["app"]) as c:
        r = c.post("/heartbeat", json=hb)
    assert r.status_code == 422 and any("next_due_s" in e for e in r.json()["errors"])


def test_v03_heartbeat_without_promise_still_accepted(env: dict[str, Any]) -> None:
    """An old sidecar keeps working; it just cannot raise LATE."""
    assert _beat(env).json() == {"status": "ok"}
    assert _sweep_at(env, 60) == []      # no promise, no LATE
    assert _sweep_at(env, 76) == ["SILENCE"]


# --- LATE at the witness ----------------------------------------------------------------------

def test_busy_promise_missed_raises_late_long_before_silence(env: dict[str, Any]) -> None:
    """Promise 2 s + grace 10 s: LATE after 12 s, once per streak; SILENCE still at 75 s."""
    _beat(env, next_due_s=2)
    assert _sweep_at(env, 12) == []
    assert _sweep_at(env, 12.5) == ["LATE"]
    assert _sweep_at(env, 30) == []      # once per streak
    assert _sweep_at(env, 76) == ["SILENCE"]


def test_idle_promise_gives_late_only_after_its_own_time(env: dict[str, Any]) -> None:
    """Promise 30 s: nothing at 35 s, LATE after 40 s."""
    _beat(env, next_due_s=30)
    assert _sweep_at(env, 35) == []
    assert _sweep_at(env, 41) == ["LATE"]


def test_late_streak_resets_after_a_new_heartbeat(env: dict[str, Any]) -> None:
    """A heartbeat after LATE ends the streak; the next missed promise raises LATE again."""
    _beat(env, next_due_s=2)
    assert _sweep_at(env, 13) == ["LATE"]
    env["clock"].t = 1_000_000.0 + 20
    _beat(env, next_due_s=2)
    assert _sweep_at(env, 31) == []
    assert _sweep_at(env, 33) == ["LATE"]


def test_promise_kept_raises_nothing(env: dict[str, Any]) -> None:
    """Heartbeats every 2 s with 2 s promises: no alert at all."""
    for t in range(0, 40, 2):
        env["clock"].t = 1_000_000.0 + t
        _beat(env, next_due_s=2)
        assert _sweep_at(env, t + 1) == []


def test_verifier_reports_late(env: dict[str, Any]) -> None:
    """A LATE alert at the witness is a LATE verdict, so verify is not clean."""
    _beat(env, next_due_s=2)
    _sweep_at(env, 13)
    with TestClient(env["app"]) as c:
        records = {ep: c.get(f"/{ep}", params={"node_id": NODE}).json()
                   for ep in ("heartbeats", "checkpoints", "alerts")}
    env["conn"].row_factory = sqlite3.Row
    assert [v["code"] for v in verify_witness(env["conn"], records)] == ["LATE"]


def test_witness_db_from_before_v04_is_upgraded(tmp_path: Path) -> None:
    """An existing witness.db gains the next_due_s column; old rows read as no promise."""
    db = tmp_path / "w.db"
    old = sqlite3.connect(db)
    old.execute("CREATE TABLE heartbeats (id INTEGER PRIMARY KEY AUTOINCREMENT, node_id TEXT NOT NULL, "
                "hb_counter INTEGER NOT NULL, head_seq INTEGER NOT NULL, head_hash TEXT NOT NULL, "
                "tree_size INTEGER NOT NULL, ts TEXT NOT NULL, signature TEXT NOT NULL, fresh INTEGER NOT NULL, "
                "received_at TEXT NOT NULL, received_epoch REAL NOT NULL)")
    old.execute("INSERT INTO heartbeats (node_id, hb_counter, head_seq, head_hash, tree_size, ts, signature, "
                "fresh, received_at, received_epoch) VALUES ('node-1', 1, 0, 'h', 1, 't', 's', 1, 't', 5.0)")
    old.commit()
    old.close()
    conn = wstore.connect(db)
    assert wstore.last_fresh_heartbeat(conn, "node-1") == {"received_epoch": 5.0, "next_due_s": None}


# --- the sidecar ------------------------------------------------------------------------------

def test_old_witness_gets_plain_heartbeats(env: dict[str, Any], caplog: pytest.LogCaptureFixture) -> None:
    """A v0.3 witness rejects next_due_s with 422: the sidecar resends without it, same counter, and remembers."""
    sent: list[dict[str, Any]] = []

    def old_witness(req: httpx.Request) -> httpx.Response:
        """Answer like a witness from before v0.4."""
        body = __import__("json").loads(req.content)
        sent.append(body)
        if "next_due_s" in body:
            return httpx.Response(422, json={"status": "invalid", "errors": ["unexpected field 'next_due_s'"]})
        return httpx.Response(200, json={"status": "ok"})

    client = httpx.Client(transport=httpx.MockTransport(old_witness))
    compat = hbm.WitnessCompat()
    with caplog.at_level("WARNING", logger="sidecar.heartbeat"):
        assert hbm.pulse_once(env["conn"], client, NODE, env["priv"], "http://w", 0, next_due_s=2, compat=compat)[0]
        assert hbm.pulse_once(env["conn"], client, NODE, env["priv"], "http://w", 0, next_due_s=2, compat=compat)[0]
    hb_posts = [b for b in sent if "hb_counter" in b]
    assert [("next_due_s" in b, b["hb_counter"]) for b in hb_posts] == [(True, 1), (False, 1), (False, 2)]
    assert compat.no_next_due and sum("older than CONTRACT v0.4" in r.message for r in caplog.records) == 1


def _run_loop(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, busy: float, interval: float,
              script: Any) -> list[tuple[float, Any]]:
    """Run the heartbeat loop with pulse_once recorded; `script(append)` drives the spool. Returns (t, promise)."""
    beats: list[tuple[float, Any]] = []

    def fake_pulse(*args: Any, **kwargs: Any) -> tuple[bool, int]:
        """Record when a beat was sent and what it promised."""
        beats.append((time.monotonic(), kwargs.get("next_due_s")))
        return True, 0

    monkeypatch.setattr(hbm, "pulse_once", fake_pulse)
    writer = init_db(env["tmp"] / "s.db")
    stop = threading.Event()
    th = hbm.start_heartbeat_thread(env["tmp"] / "s.db", NODE, env["priv"], "http://w", interval, stop,
                                    busy_interval_s=busy)
    deadline = time.monotonic() + 10  # building httpx.Client() can be slow on Windows
    while not beats and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        script(lambda i: append_entry(writer, _event(i)))
    finally:
        stop.set()
        th.join(timeout=3)
        writer.close()
    return beats


def test_loop_beats_fast_while_busy_then_settles_to_idle(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Busy: a beat per busy interval, promising it. Quiet: one closing beat promising idle, then silence."""
    marks: dict[str, float] = {}

    def script(append: Any) -> None:
        """Busy for 1.2 s, quiet for 1.5 s."""
        time.sleep(0.3)  # let the start-up beats settle
        marks["busy"] = time.monotonic()
        for i in range(12):
            append(i + 1)
            time.sleep(0.1)
        marks["quiet"] = time.monotonic()
        time.sleep(1.5)

    beats = _run_loop(env, monkeypatch, busy=0.25, interval=4.0, script=script)
    busy = [p for t, p in beats if marks["busy"] < t <= marks["quiet"]]
    quiet = [p for t, p in beats if t > marks["quiet"]]
    assert len(busy) >= 3 and set(busy) == {1}      # ceil(0.25 s) promised each time
    assert quiet[-1] == 4 and quiet.count(4) == 1   # one closing beat promising the idle interval


def test_first_event_after_quiet_is_reported_within_busy_interval(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """New entries do not wait for the next idle beat."""
    marks: dict[str, float] = {}

    def script(append: Any) -> None:
        """Quiet long enough to go idle, then one event."""
        time.sleep(0.8)
        marks["event"] = time.monotonic()
        append(1)
        time.sleep(0.6)

    beats = _run_loop(env, monkeypatch, busy=0.2, interval=5.0, script=script)
    after = [t for t, _ in beats if t > marks["event"]]
    assert after and after[0] - marks["event"] < 0.45


def test_busy_interval_zero_is_the_v03_behaviour(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """busy_interval_s = 0: a beat every interval, no promise on the wire."""
    beats = _run_loop(env, monkeypatch, busy=0, interval=0.1, script=lambda append: time.sleep(0.5))
    assert len(beats) >= 3 and {p for _, p in beats} == {None}


# --- profile ----------------------------------------------------------------------------------

@pytest.mark.parametrize("hb, wit, error", [
    ("busy_interval_s = 30", "", "must be less than interval_s"),
    ("busy_interval_s = -1", "", "must be 0 (off) or greater"),
    ("busy_interval_s = 1.5", "", "busy_interval_s"),
    ("", "late_grace_s = 0", "late_grace_s: must be at least 1"),
])
def test_profile_rejects_bad_timing(tmp_path: Path, hb: str, wit: str, error: str) -> None:
    """One clear error for each bad value."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[profile]\nname = "t"\n[node]\nnode_id = "node-1"\n[heartbeat]\ninterval_s = 30\n{hb}\n[witness]\n{wit}\n',
                   encoding="utf-8")
    _, errors = load_profile(cfg)
    assert any(error in e for e in errors), errors


def test_profile_defaults(tmp_path: Path) -> None:
    """Dynamic heartbeats are on by default: 2 s while busy, 10 s grace."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('[profile]\nname = "t"\n[node]\nnode_id = "node-1"\n', encoding="utf-8")
    profile, errors = load_profile(cfg)
    assert not errors and profile is not None
    assert (profile["heartbeat"]["busy_interval_s"], profile["witness"]["late_grace_s"]) == (2, 10)


# --- RT-50: the compose file must keep starting older witness images ----------------------------

V03_WITNESS_FLAGS = {"--config", "--host", "--nodes-file", "--interval-s", "--db-path", "--port"}


def test_witness_compose_uses_only_flags_older_images_know() -> None:
    """A v0.3 image exits on an unknown flag, so new settings go in through the environment."""
    text = (Path(__file__).resolve().parent.parent / "witness-compose.yml").read_text(encoding="utf-8")
    command = text[text.index("command:"):text.index("]", text.index("command:"))]
    flags = set(re.findall(r'"(--[a-z-]+)"', command))
    assert flags and flags <= V03_WITNESS_FLAGS, flags - V03_WITNESS_FLAGS
    assert "COC_LATE_GRACE_S=${COC_LATE_GRACE_S:-10}" in text


def test_late_grace_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Flag, else COC_LATE_GRACE_S, else the profile, else 10."""
    cfg = tmp_path / "c.toml"
    cfg.write_text("[witness]\nlate_grace_s = 7\n", encoding="utf-8")
    monkeypatch.delenv("COC_LATE_GRACE_S", raising=False)
    assert witness_settings()["late_grace_s"] == 10.0
    assert witness_settings(cfg)["late_grace_s"] == 7.0
    monkeypatch.setenv("COC_LATE_GRACE_S", "20")
    assert witness_settings(cfg)["late_grace_s"] == 20.0
    assert witness_settings(cfg, late_grace_s=3)["late_grace_s"] == 3.0


@pytest.mark.parametrize("value", ["ten", "0", "-5"])
def test_bad_late_grace_env_stops_the_witness_with_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], value: str
) -> None:
    """Exit 2 with a clear message, no traceback."""
    _, pub = generate_keypair()
    nodes = tmp_path / "nodes.toml"
    nodes.write_text(f'[nodes]\n"{NODE}" = "{pub}"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COC_LATE_GRACE_S", value)
    assert witness_main(["--nodes-file", str(nodes), "--db-path", str(tmp_path / "w.db")]) == 2
    err = capsys.readouterr().err
    assert "late_grace_s" in err.lower() and "Traceback" not in err
