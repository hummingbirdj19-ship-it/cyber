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


def test_unreachable_witness_fails_quickly_with_checklist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With the witness laptop off or unreachable, coc check fails within about 5 s with the network checklist."""
    monkeypatch.chdir(tmp_path)  # the profile's key paths are relative; never write keys into the repo
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


# ---------------------------------------------------------------------------
# Red-team regressions for I1 (RT-39 to RT-45, RT-47; see docs/HANDOFF.md)
# ---------------------------------------------------------------------------
REPO = Path(__file__).resolve().parents[1]


def _init_ok(**kwargs: object) -> None:
    """Run coc init in the current folder and require success."""
    assert run_init(config_path=Path("config.toml"), **kwargs) == 0  # type: ignore[arg-type]


def test_rt39_rt47_wrappers_use_image_code_and_host_access() -> None:
    """The wrappers import coc from the image, reach the host, and (coc.sh) keep file ownership."""
    assert "PYTHONPATH=/app" in (REPO / "sidecar" / "Dockerfile").read_text(encoding="utf-8")
    ps1 = (REPO / "coc.ps1").read_text(encoding="utf-8")
    sh = (REPO / "coc.sh").read_text(encoding="utf-8")
    for script in (ps1, sh):
        assert "python -P -m coc" in script
        assert "host.docker.internal:host-gateway" in script
    assert '--user "$(id -u):$(id -g)"' in sh


def test_rt41_no_wrapper_copies_outside_the_project() -> None:
    """Wrappers live next to defence-compose.yml only; a root copy writes keys outside .gitignore."""
    assert not (REPO.parent / "coc.ps1").exists()
    assert not (REPO.parent / "coc.sh").exists()


def test_rt40_localhost_means_the_host_inside_a_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """In a container, localhost witness URLs are checked at host.docker.internal; elsewhere unchanged."""
    from coc import check as check_mod

    monkeypatch.setattr(check_mod, "CONTAINER_MARKER", tmp_path / "absent")
    assert check_mod._witness_target("http://localhost:8800") == ("http://localhost:8800", None)
    marker = tmp_path / ".dockerenv"
    marker.write_text("", encoding="utf-8")
    monkeypatch.setattr(check_mod, "CONTAINER_MARKER", marker)
    for url in ("http://localhost:8800", "http://127.0.0.1:8800"):
        target, note = check_mod._witness_target(url)
        assert target == "http://host.docker.internal:8800" and note
    assert check_mod._witness_target("http://192.168.1.20:8800") == ("http://192.168.1.20:8800", None)


def test_check_fails_clearly_when_something_else_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A web server that is not a witness is a clear FAIL, not 'unreachable' or a crash."""
    import httpx

    monkeypatch.chdir(tmp_path)
    _init_ok()
    html = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, text="<html></html>")))
    assert run_check(config_path=Path("config.toml"), client=html) == 1
    assert "not like a witness" in capsys.readouterr().out


def test_rt42_witness_nodes_file_is_parsed_not_searched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """node-10 does not count as node-1, and a stale key is refused (or replaced with --force)."""
    monkeypatch.chdir(tmp_path)
    nodes = Path("data/witness_nodes.toml")
    nodes.parent.mkdir()
    nodes.write_text('[nodes]\nnode-10 = "' + "a" * 64 + '"\n', encoding="utf-8")
    _init_ok()
    pub = Path("data/node1.pub").read_text(encoding="utf-8")
    trusted = witness_checks.load_nodes(nodes)
    assert trusted == {"node-10": "a" * 64, "node-1": pub}

    nodes.write_text('[nodes]\nnode-1 = "' + "b" * 64 + '"\n', encoding="utf-8")
    capsys.readouterr()
    assert run_init(config_path=Path("config.toml")) == 1
    assert "trusts a different key for 'node-1'" in capsys.readouterr().err
    assert witness_checks.load_nodes(nodes) == {"node-1": "b" * 64}  # left alone

    assert run_init(force=True, config_path=Path("config.toml")) == 0
    assert witness_checks.load_nodes(nodes) == {"node-1": Path("data/node1.pub").read_text(encoding="utf-8")}


def test_rt43_node_id_follows_the_witness_rule() -> None:
    """The profile accepts exactly the node ids the witness accepts."""
    from coc.profile import NODE_ID_RE

    assert NODE_ID_RE.pattern == witness_checks.NODE_ID_RE.pattern
    base = {"profile": {"name": "t"}, "node": {"node_id": "node-1"}}
    assert validate_profile(base)[1] == []
    for bad in ("node 1", "node-1\n", "", "x" * 65):
        _, errors = validate_profile({**base, "node": {"node_id": bad}})
        assert errors and errors[0].startswith("node.node_id: must match"), bad


def test_rt44_force_keeps_an_existing_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--force regenerates the key pair but never rewrites config.toml."""
    monkeypatch.chdir(tmp_path)
    _init_ok()
    cfg = Path("config.toml")
    custom = cfg.read_text(encoding="utf-8").replace("example-platform", "my-bank")
    cfg.write_text(custom, encoding="utf-8")
    old_pub = Path("data/node1.pub").read_text(encoding="utf-8")
    _init_ok(force=True)
    assert cfg.read_text(encoding="utf-8") == custom
    assert Path("data/node1.pub").read_text(encoding="utf-8") != old_pub


@pytest.mark.parametrize(
    "flags",
    [
        {"node_id": 'x"\n[evil]\na="b'},
        {"node_id": "node 1"},
        {"witness_url": r"http://10.0.0.5:8800\1"},
        {"witness_url": "http://10.0.0.5:8800\x01"},
        {"witness_url": "10.0.0.5:8800"},
        {"spool_path": "spool/\nevil"},
    ],
)
def test_rt45_bad_flags_are_refused_before_writing(
    flags: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bad flag gives one clear error, exit 1, and leaves no files behind."""
    monkeypatch.chdir(tmp_path)
    assert run_init(config_path=Path("config.toml"), **flags) == 1
    assert "Error: --" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_rt45_flags_with_special_characters_stay_valid_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Flag values are written as quoted TOML strings, so the profile reads back exactly."""
    monkeypatch.chdir(tmp_path)
    _init_ok(spool_path='spool/"odd" name.jsonl', witness_url="http://witness.local:8800/")
    profile, errors = load_profile("config.toml")
    assert errors == [] and profile is not None
    assert profile["paths"]["spool_path"] == 'spool/"odd" name.jsonl'


def test_rt45_corrupt_private_key_is_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A corrupt key file stops init with one error line, no traceback, and is not replaced."""
    monkeypatch.chdir(tmp_path)
    _init_ok()
    bad = b"-----BEGIN PRIVATE KEY-----\ngarbage\n-----END PRIVATE KEY-----\n"
    Path("data/node1.key").write_bytes(bad)
    Path("data/node1.pub").unlink()
    capsys.readouterr()
    assert cli_main(["init"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("Error: node key:") and "Traceback" not in err
    assert Path("data/node1.key").read_bytes() == bad
