"""Setup guide (site/index.html): self-contained, and in step with the files and commands it tells people to use."""
from pathlib import Path
import re

import pytest

REPO = Path(__file__).resolve().parents[1]
PAGE = REPO / "site" / "index.html"
RAW = "https://raw.githubusercontent.com/hummingbirdj19-ship-it/cyber/main/chain-of-custody"


def _page() -> str:
    """The guide's HTML."""
    return PAGE.read_text(encoding="utf-8")


def test_page_loads_nothing_from_outside() -> None:
    """No external scripts, styles or fonts: the page works offline at the demo."""
    html = _page()
    assert not re.search(r"<script[^>]+src=", html)
    assert not re.search(r"<link[^>]+rel=\"stylesheet\"", html)
    assert "@import" not in html


def test_every_in_page_link_has_a_target() -> None:
    """Navigation anchors all point at a section that exists."""
    html = _page()
    ids = set(re.findall(r' id="([^"]+)"', html))
    assert {"how", "setup", "demo", "integrate", "trouble", "reference"} <= ids
    assert [a for a in re.findall(r'href="#([^"]+)"', html) if a not in ids] == []


def test_every_os_block_has_a_twin() -> None:
    """Each Windows block has a macOS/Linux counterpart, so the OS switch never hides a step."""
    html = _page()
    assert html.count('class="os-only" data-os="win"') == html.count('class="os-only" data-os="nix"') > 0


def test_downloaded_files_exist_in_the_repo() -> None:
    """Every file the guide downloads from main is a real file in this folder."""
    html = _page()
    names = set(re.findall(r'\$base/([\w.-]+)"', html))
    assert names == {"witness-compose.yml", "sidecar-compose.yml", "coc.ps1", "coc.sh"}
    assert f'$base = "{RAW}"' in html and f"base={RAW}" in html
    for name in names:
        assert (REPO / name).is_file(), name


@pytest.mark.parametrize("command", ["init", "check", "trust", "verify"])
def test_documented_coc_commands_exist(command: str) -> None:
    """The guide only names coc commands the CLI actually has."""
    from coc.cli import main

    with pytest.raises(SystemExit) as exc:
        main([command, "--help"])
    assert exc.value.code == 0
    assert f"coc.ps1 {command}" in _page() or f"coc {command}" in _page()


def test_image_name_matches_the_compose_files() -> None:
    """The image the guide runs is the one the compose files and wrappers use."""
    assert "ghcr.io/hummingbirdj19-ship-it/coc-sidecar:latest" in _page()
    assert "ghcr.io/hummingbirdj19-ship-it}/coc-sidecar" in (REPO / "sidecar-compose.yml").read_text(encoding="utf-8")
