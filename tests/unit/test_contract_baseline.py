from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from mcp_cnes.infrastructure.config import Settings
from mcp_server import MCPServer

FIXTURES_DIR = Path(__file__).parents[1] / "fixtures" / "contracts"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def test_package_import_does_not_load_playwright() -> None:
    sys.modules.pop("mcp_cnes", None)

    __import__("mcp_cnes")

    assert "playwright" not in sys.modules


def test_tool_contract_matches_baseline() -> None:
    expected = load_fixture("tools.json")

    actual = MCPServer().get_tools()

    assert actual == expected
    assert [tool["name"] for tool in actual] == [
        "cnes_load_data",
        "cnes_search_municipio",
        "cnes_search_cnes",
        "cnes_search_uf",
        "cnes_statistics",
        "cnes_download_instructions",
    ]


@pytest.mark.asyncio
async def test_example_responses_match_baseline() -> None:
    fixture = load_fixture("examples.json")
    repository_root = Path(__file__).parents[2]
    server = MCPServer(
        settings=Settings(
            data_dir=repository_root,
            allowed_csv_files=("sample_data.csv",),
        )
    )

    for example in fixture:
        arguments = dict(example["arguments"])
        if example["tool"] == "cnes_load_data":
            arguments["filepath"] = str(repository_root / arguments["filepath"])

        actual = await server.call_tool(example["tool"], arguments)
        for dynamic_field, replacement in example.get("normalize", {}).items():
            if dynamic_field in actual:
                actual[dynamic_field] = replacement

        assert actual == example["response"], example["tool"]
