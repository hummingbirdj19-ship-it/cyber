"""I1 tests: coc init, coc check, profile schema, and witness nodes_file enforcement."""
import io
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from coc.check import run_check
from coc.cli import main as cli_main
from coc.init import run_init
from coc.profile import load_profile, validate_profile
from witness import checks as witness_checks
from witness.app import create_app
from witness.app import main as witness_main


def test_fresh_clone_coc_init_and_check_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On a fresh clone, coc init and coc check pass with no manual edits (spool warning allowed)."""
    monkeypatch.chdir(tmp_path)

    # Setup fresh clone with config.example.toml
    example_path = Path("config.example.toml")
    example_path.write_text(
        """[profile]
name = "demo-platform"
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
witness_url = "http://testserver"

[witness]
db_path = "data/witness.db"
port = 8800
nodes_file = "data/witness_nodes.toml"
""",
        encoding="utf-8",
    )

    # Run coc init
    init_res = run_init(
        config_path=Path("config.toml"),
        example_config_path=example_path,
    )
    assert init_res == 0

    assert Path("config.toml").exists()
    assert Path("data/node1.key").exists()
    assert Path("data/node1.pub").exists()
    assert Path("data/witness_nodes.toml").exists()

    # Create witness app using the generated config and witness_nodes.toml
    app = create_app(Path("config.toml"))
    with TestClient(app) as client:
        # Run coc check against running witness
        check_res = run_check(config_path=Path("config.toml"), client=client)
        assert check_res == 0


def test_broken_profile_clear_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A broken profile gives one clear error each for a typo'd key, wrong type, missing key file, TOML syntax error."""
    base_cfg = """[profile]
name = "test"
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

    # 1. Typo'd key: intervl_s in [heartbeat]
    typo_cfg = tmp_path / "typo.toml"
    typo_cfg.write_text(base_cfg.replace("interval_s = 30", "intervl_s = 30"), encoding="utf-8")
    res_typo = run_check(config_path=typo_cfg)
    assert res_typo == 1
    out_typo = capsys.readouterr().out
    assert "heartbeat.intervl_s: unknown key" in out_typo

    # 2. Wrong type: interval_s is a string instead of an integer
    type_cfg = tmp_path / "type.toml"
    type_cfg.write_text(base_cfg.replace("interval_s = 30", 'interval_s = "30"'), encoding="utf-8")
    res_type = run_check(config_path=type_cfg)
    assert res_type == 1
    out_type = capsys.readouterr().out
    assert "heartbeat.interval_s: expected integer, got string" in out_type

    # 3. Missing key file: config valid, but private key file does not exist
    missing_key_cfg = tmp_path / "missing_key.toml"
    missing_key_cfg.write_text(
        base_cfg.replace('private_key_path = "data/node1.key"', f'private_key_path = "{(tmp_path / "absent.key").as_posix()}"')
        .replace('public_key_path = "data/node1.pub"', f'public_key_path = "{(tmp_path / "absent.pub").as_posix()}"'),
        encoding="utf-8",
    )
    res_key = run_check(config_path=missing_key_cfg)
    assert res_key == 1
    out_key = capsys.readouterr().out
    assert "private key file missing:" in out_key

    # 4. TOML syntax error
    syntax_cfg = tmp_path / "syntax.toml"
    syntax_cfg.write_text("[profile\nname = 'broken'", encoding="utf-8")
    res_syntax = run_check(config_path=syntax_cfg)
    assert res_syntax == 1
    out_syntax = capsys.readouterr().out
    assert "TOML syntax error" in out_syntax


def test_unreachable_witness_fails_quickly_with_checklist(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """With the witness laptop off or unreachable, coc check fails within about 5 s with the network checklist."""
    # Create valid keys and config pointing to unreachable port
    priv_file = tmp_path / "node1.key"
    pub_file = tmp_path / "node1.pub"
    run_init(
        config_path=tmp_path / "config.toml",
        example_config_path=tmp_path / "config.example.toml",
    )

    # Point witness_url to an unused local port
    cfg_text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    cfg_text = cfg_text.replace("http://localhost:8800", "http://127.0.0.1:59999")
    (tmp_path / "config.toml").write_text(cfg_text, encoding="utf-8")

    start_time = time.monotonic()
    res = run_check(config_path=tmp_path / "config.toml", timeout=3.0)
    elapsed = time.monotonic() - start_time

    assert res == 1
    assert elapsed < 5.0, f"Check took too long: {elapsed:.2f}s (must fail within about 5 s)"

    out = capsys.readouterr().out
    assert "FAIL  witness: unreachable" in out
    assert "Network checklist:" in out
    assert "witness laptop's IP" in out
    assert "8800" in out
    assert "firewall" in out
    assert "same network or hotspot" in out


def test_witness_refuses_missing_or_directory_nodes_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Witness refuses to start with clear error if nodes_file is missing or a directory."""
    # 1. Missing nodes_file
    missing_nodes = tmp_path / "nonexistent_nodes.toml"
    with pytest.raises(FileNotFoundError, match="nodes_file .* is missing"):
        witness_checks.load_nodes(missing_nodes)

    cfg_missing = tmp_path / "cfg_missing.toml"
    cfg_missing.write_text(
        f'[heartbeat]\ninterval_s = 30\n[witness]\ndb_path = "w.db"\nport = 8800\nnodes_file = "{missing_nodes.as_posix()}"\n',
        encoding="utf-8",
    )
    with pytest.raises(FileNotFoundError, match="nodes_file .* is missing"):
        create_app(cfg_missing)

    ret = witness_main(["--config", str(cfg_missing)])
    assert ret == 2
    err = capsys.readouterr().err
    assert "is missing" in err

    # 2. Directory nodes_file (created by Docker when file is missing on host)
    dir_nodes = tmp_path / "dir_nodes.toml"
    dir_nodes.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ValueError, match="is a directory, not a file"):
        witness_checks.load_nodes(dir_nodes)

    cfg_dir = tmp_path / "cfg_dir.toml"
    cfg_dir.write_text(
        f'[heartbeat]\ninterval_s = 30\n[witness]\ndb_path = "w.db"\nport = 8800\nnodes_file = "{dir_nodes.as_posix()}"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="is a directory, not a file"):
        create_app(cfg_dir)

    ret_dir = witness_main(["--config", str(cfg_dir)])
    assert ret_dir == 2
    err_dir = capsys.readouterr().err
    assert "is a directory, not a file" in err_dir


def test_coc_init_flags_idempotency_and_force_db_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test coc init flags, second run idempotence, and --force refusal when sidecar DB exists."""
    monkeypatch.chdir(tmp_path)

    # 1. Init with custom flags
    ret = run_init(
        node_id="custom-node-42",
        spool_path="custom_spool/events.jsonl",
        witness_url="http://custom-witness:8800",
        config_path=Path("config.toml"),
    )
    assert ret == 0

    cfg_content = Path("config.toml").read_text(encoding="utf-8")
    assert 'node_id = "custom-node-42"' in cfg_content
    assert 'spool_path = "custom_spool/events.jsonl"' in cfg_content
    assert 'witness_url = "http://custom-witness:8800"' in cfg_content

    nodes_content = Path("data/witness_nodes.toml").read_text(encoding="utf-8")
    assert "custom-node-42" in nodes_content

    pub_key_1 = Path("data/node1.pub").read_text(encoding="utf-8").strip()

    # 2. Second run without --force leaves files untouched
    ret_second = run_init(config_path=Path("config.toml"))
    assert ret_second == 0
    pub_key_2 = Path("data/node1.pub").read_text(encoding="utf-8").strip()
    assert pub_key_1 == pub_key_2
    out = capsys.readouterr().out
    assert "[EXISTS]" in out

    # 3. Create sidecar DB and verify --force is refused
    db_file = Path("data/sidecar.db")
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db_file.write_text("fake db", encoding="utf-8")

    ret_force_blocked = run_init(force=True, config_path=Path("config.toml"))
    assert ret_force_blocked == 1
    err = capsys.readouterr().err
    assert "Refusing --force because sidecar database" in err
    assert "RT-12" in err

    # 4. Remove sidecar DB and verify --force regenerates keys
    db_file.unlink()
    ret_force_ok = run_init(force=True, config_path=Path("config.toml"))
    assert ret_force_ok == 0
    pub_key_3 = Path("data/node1.pub").read_text(encoding="utf-8").strip()
    assert pub_key_3 != pub_key_1  # newly generated keys


def test_private_key_never_printed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Ensure private key PEM or raw bytes are never leaked to stdout or stderr during init."""
    monkeypatch.chdir(tmp_path)
    run_init(config_path=Path("config.toml"))

    priv_pem = Path("data/node1.key").read_text(encoding="utf-8")
    priv_lines = [line.strip() for line in priv_pem.splitlines() if "PRIVATE KEY" not in line and line.strip()]

    captured = capsys.readouterr()
    stdout = captured.out
    stderr = captured.err

    assert "BEGIN PRIVATE KEY" not in stdout
    assert "BEGIN PRIVATE KEY" not in stderr
    for chunk in priv_lines:
        assert chunk not in stdout
        assert chunk not in stderr


def test_coc_cli_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure coc CLI subcommands init and check dispatch correctly."""
    monkeypatch.chdir(tmp_path)
    res_init = cli_main(["init", "--node-id", "test-node"])
    assert res_init == 0
    assert Path("config.toml").exists()

    # Stub witness check with mock or invalid
    res_check = cli_main(["check", "--config", "config.toml"])
    # Fails at witness check layer since no witness is running
    assert res_check in (0, 1)
