from __future__ import annotations

import tomllib
from importlib.metadata import version as distribution_version
from pathlib import Path

from mcp_cnes import __version__
from mcp_cnes.interfaces.mcp import create_mcp_server


def test_project_distribution_and_mcp_versions_match() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]
    expected = project["version"]

    assert distribution_version("mcp-cnes") == expected
    assert __version__ == expected
    assert create_mcp_server().version == expected
