"""I2 tests: split compose files, published image names, config-less witness, coc trust and coc verify."""
import json
from pathlib import Path
import random
import tomllib
from typing import Any

import pytest

from coc import init as coc_init
from coc.cli import main as cli_main
from coc.init import DEFAULT_CONFIG_TEMPLATE, run_init
from sidecar import heartbeat as hbm
from sidecar import main as sidecar_main
from sidecar.keys import generate_keypair
from sidecar.store import append_entry, init_db
from tools.gen_events import make_event
from witness import checks as witness_checks
from witness.app import create_app, witness_settings
from witness.app import main as witness_main

REPO = Path(__file__).resolve().parents[1]
SIDECAR_IMAGE = "${COC_IMAGE_PREFIX:-ghcr.io/jeevikas-19}/coc-sidecar:${COC_TAG:-latest}"
WITNESS_IMAGE = "${COC_IMAGE_PREFIX:-ghcr.io/jeevikas-19}/coc-witness:${COC_TAG:-latest}"


def _read(rel: str) -> str:
    """Read a repo file as text."""
    return (REPO / rel).read_text(encoding="utf-8")


def _in_container(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Make resolve_witness_url believe it runs in a container."""
    marker = tmp_path / ".dockerenv"
    marker.write_text("", encoding="utf-8")
    monkeypatch.setattr(hbm, "CONTAINER_MARKER", marker)


# --- compose files and images ---------------------------------------------------------------

def test_sidecar_compose_runs_only_the_published_sidecar() -> None:
    """Platform laptop: published image, read-only spool, host gateway, the profile's witness_url."""
    text = _read("sidecar-compose.yml")
    assert f"image: {SIDECAR_IMAGE}" in text
    assert "build:" not in text and "coc-witness" not in text and "witness-data" not in text
    assert "${SPOOL_DIR:-./spool}:/app/spool:ro" in text
    assert "./config.toml:/app/config.toml:ro" in text
    assert "host.docker.internal:host-gateway" in text
    assert "--witness-url" not in text  # the profile decides where the witness is


def test_witness_compose_needs_no_config_or_clone() -> None:
    """Witness laptop: published image, trust file read-only, own volume, interval passed explicitly."""
    text = _read("witness-compose.yml")
    assert f"image: {WITNESS_IMAGE}" in text
    assert "build:" not in text and "config.toml:" not in text and "coc-sidecar" not in text
    assert "./witness_nodes.toml:/etc/coc/witness_nodes.toml:ro" in text
    assert "witness-data:/app/data" in text
    assert '"--interval-s", "${COC_INTERVAL_S:-30}"' in text
    assert "${COC_WITNESS_PORT:-8800}:8800" in text


def test_local_build_uses_the_published_names() -> None:
    """defence-compose.yml builds locally under the names the wrappers and split files use."""
    text = _read("defence-compose.yml")
    assert text.count("build:") == 2
    assert f"image: {SIDECAR_IMAGE}" in text and f"image: {WITNESS_IMAGE}" in text
    for wrapper in ("coc.ps1", "coc.sh"):
        assert "ghcr.io/jeevikas-19/coc-sidecar:latest" in _read(wrapper)
    assert "--config" not in _read("witness/Dockerfile").split("CMD", 1)[1]


def test_publish_workflow_is_least_privilege() -> None:
    """The workflow builds both images for both architectures with GITHUB_TOKEN only, from main or by hand."""
    if not (REPO.parent / ".git").exists():
        pytest.skip("only chain-of-custody/ was copied (e.g. a test run inside the image); the workflow is at the repo root")
    path = REPO.parent / ".github" / "workflows" / "publish-images.yml"
    text = path.read_text(encoding="utf-8")
    assert "contents: read" in text and "packages: write" in text
    assert "branches: [main]" in text and "workflow_dispatch:" in text
    assert "pull_request" not in text  # never publish from a PR
    assert "image: [sidecar, witness]" in text
    assert "platforms: linux/amd64,linux/arm64" in text
    assert "file: chain-of-custody/${{ matrix.image }}/Dockerfile" in text
    secrets = {line.split("secrets.")[1].split()[0] for line in text.splitlines() if "secrets." in line}
    assert secrets == {"GITHUB_TOKEN"}
    for image in ("sidecar", "witness"):
        assert (REPO / image / "Dockerfile").is_file()


# --- one localhost rule everywhere ------------------------------------------------------------

def test_sidecar_applies_the_same_localhost_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In a container, the sidecar heartbeats to host.docker.internal, as coc check expects."""
    monkeypatch.chdir(tmp_path)
    assert run_init(config_path=Path("config.toml")) == 0
    _in_container(monkeypatch, tmp_path)
    seen: dict[str, Any] = {}
    monkeypatch.setattr(sidecar_main, "start_heartbeat_thread", lambda **kw: seen.update(kw))
    monkeypatch.setattr(sidecar_main, "follow_spool", lambda *a, **kw: None)
    assert sidecar_main.run_sidecar(Path("config.toml")) == 0
    assert seen["witness_url"] == "http://host.docker.internal:8800"


# --- config-less witness -----------------------------------------------------------------------

def test_witness_settings_precedence(tmp_path: Path) -> None:
    """Explicit flag, else profile, else default."""
    assert witness_settings() == {"interval_s": 30.0, "nodes_file": Path("data/witness_nodes.toml"),
                                  "db_path": Path("data/witness.db"), "port": 8800}
    cfg = tmp_path / "c.toml"
    cfg.write_text('[heartbeat]\ninterval_s = 10\n[witness]\nport = 9000\ndb_path = "x.db"\n', encoding="utf-8")
    s = witness_settings(cfg)
    assert (s["interval_s"], s["port"], s["db_path"]) == (10.0, 9000, Path("x.db"))
    s = witness_settings(cfg, interval_s=5, port=7000, db_path=Path("y.db"))
    assert (s["interval_s"], s["port"], s["db_path"]) == (5.0, 7000, Path("y.db"))


def test_witness_without_config_uses_its_interval_for_silence(tmp_path: Path) -> None:
    """No profile: the --interval-s value sets the SILENCE window (2 * interval + 15 s)."""
    _, pub = generate_keypair()
    nodes = tmp_path / "witness_nodes.toml"
    nodes.write_text(f'[nodes]\nnode-1 = "{pub}"\n', encoding="utf-8")
    clock = {"t": 1_000_000.0}
    app = create_app(None, now=lambda: clock["t"], nodes_file=nodes, interval_s=5, db_path=tmp_path / "w.db")
    window = 2 * 5 + witness_checks.SILENCE_MARGIN_S
    clock["t"] += window
    assert app.state.sweep_silence() == []
    clock["t"] += 1
    assert [a["code"] for a in app.state.sweep_silence()] == ["SILENCE"]


def test_witness_cli_runs_without_config_and_refuses_a_missing_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The witness laptop runs from flags alone; an explicit --config that is missing is exit 2."""
    import uvicorn

    monkeypatch.chdir(tmp_path)
    _, pub = generate_keypair()
    Path("witness_nodes.toml").write_text(f'[nodes]\nnode-1 = "{pub}"\n', encoding="utf-8")
    ran: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port: ran.update(host=host, port=port))
    argv = ["--host", "0.0.0.0", "--port", "8899", "--nodes-file", "witness_nodes.toml",
            "--db-path", "d/witness.db", "--interval-s", "30"]
    assert witness_main(argv) == 0
    assert ran == {"host": "0.0.0.0", "port": 8899} and Path("d/witness.db").exists()
    assert witness_main(["--config", "absent.toml"]) == 2
    assert "absent.toml" in capsys.readouterr().err
    assert witness_main(["--nodes-file", "missing.toml"]) == 2


# --- coc trust ---------------------------------------------------------------------------------

def test_coc_trust_writes_a_file_the_witness_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """New file, second node, repeat (no change), different key refused, --replace on purpose."""
    monkeypatch.chdir(tmp_path)
    k1, k2, k3 = (generate_keypair()[1] for _ in range(3))
    assert cli_main(["trust", "node-1", k1]) == 0
    assert cli_main(["trust", "node-2", k2.upper()]) == 0  # keys are normalised to lowercase
    before = Path("witness_nodes.toml").read_bytes()
    assert cli_main(["trust", "node-1", k1]) == 0
    assert Path("witness_nodes.toml").read_bytes() == before
    assert witness_checks.load_nodes(Path("witness_nodes.toml")) == {"node-1": k1, "node-2": k2}
    capsys.readouterr()
    assert cli_main(["trust", "node-1", k3]) == 1
    assert "different key" in capsys.readouterr().err
    assert Path("witness_nodes.toml").read_bytes() == before
    assert cli_main(["trust", "node-1", k3, "--replace"]) == 0
    assert witness_checks.load_nodes(Path("witness_nodes.toml")) == {"node-1": k3, "node-2": k2}


@pytest.mark.parametrize(
    "node_id, key",
    [("node 1", "a" * 64), ("node-1", "a" * 63), ("node-1", "g" * 64), ("node-1", "a" * 64 + "\n")],
)
def test_coc_trust_refuses_bad_input(
    node_id: str, key: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bad node id or key is one clear error and writes nothing."""
    monkeypatch.chdir(tmp_path)
    if key.endswith("\n"):  # trailing newline is stripped like a pasted .pub file: accepted
        assert cli_main(["trust", node_id, key]) == 0
        return
    assert cli_main(["trust", node_id, key]) == 1
    assert capsys.readouterr().err.startswith("Error: ")
    assert list(tmp_path.iterdir()) == []


def test_coc_trust_refuses_a_bom_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file saved as 'UTF-8 with BOM' (Notepad) is refused and left alone."""
    monkeypatch.chdir(tmp_path)
    bom = b"\xef\xbb\xbf[nodes]\n"
    Path("witness_nodes.toml").write_bytes(bom)
    assert cli_main(["trust", "node-1", "a" * 64]) == 1
    assert Path("witness_nodes.toml").read_bytes() == bom


# --- coc verify and init -----------------------------------------------------------------------

def test_coc_verify_matches_the_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Same JSON and exit codes as python -m verifier: 0 clean, 1 tampered, 2 failed."""
    monkeypatch.chdir(tmp_path)
    assert run_init(config_path=Path("config.toml")) == 0
    conn = init_db(Path("data/sidecar.db"))
    rng = random.Random(3)
    for i in range(5):
        append_entry(conn, make_event(rng, f"2026-10-12T10:00:0{i}Z"))
    conn.close()
    capsys.readouterr()
    assert cli_main(["verify"]) == 0
    assert json.loads(capsys.readouterr().out) == {"clean": True, "checked_entries": 5, "verdicts": []}
    import sqlite3
    with sqlite3.connect("data/sidecar.db") as c:
        c.execute("UPDATE entries SET actor = 'mallory' WHERE seq = 2")
    assert cli_main(["verify"]) == 1
    assert json.loads(capsys.readouterr().out)["verdicts"][0]["code"] == "EDIT"
    assert cli_main(["verify", "--config", "absent.toml"]) == 2


def test_coc_verify_uses_the_localhost_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    """In a container, coc verify asks the host's witness, and says so on stderr (stdout stays JSON)."""
    from coc import cli

    _in_container(monkeypatch, tmp_path)
    seen: list[list[str]] = []
    monkeypatch.setattr(cli, "verifier_main", lambda argv: seen.append(argv) or 0)
    assert cli_main(["verify", "--witness", "http://localhost:8800", "--node-id", "node-1"]) == 0
    assert seen[0] == ["--config", "config.toml", "--witness", "http://host.docker.internal:8800",
                       "--node-id", "node-1"]
    captured = capsys.readouterr()
    assert captured.out == "" and "host.docker.internal" in captured.err


def test_builtin_template_matches_config_example() -> None:
    """Without a clone, coc init writes the same profile as config.example.toml."""
    assert tomllib.loads(DEFAULT_CONFIG_TEMPLATE) == tomllib.loads(_read("config.example.toml"))


def test_init_prints_the_trust_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """coc init tells the operator the exact coc trust line for the witness laptop."""
    monkeypatch.chdir(tmp_path)
    assert run_init(config_path=Path("config.toml"), node_id="bank-1") == 0
    pub = Path("data/node1.pub").read_text(encoding="utf-8")
    assert f"coc trust bank-1 {pub}" in capsys.readouterr().out
    assert coc_init.NODE_ID_RE.fullmatch("bank-1")
