from __future__ import annotations

import os
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "csv"


@pytest.mark.asyncio
async def test_last_known_good_checkout_uses_an_official_mcp_entrypoint(
    tmp_path: Path,
) -> None:
    database = tmp_path / "rollback.duckdb"
    environment = dict(os.environ)
    environment["MCP_CNES_DATABASE_PATH"] = str(database)
    environment["MCP_CNES_DATA_DIR"] = str(FIXTURES)
    environment["MCP_CNES_ALLOWED_CSV_FILES"] = "valid.csv"
    parameters = StdioServerParameters(
        command="uv",
        args=[
            "--directory",
            str(ROOT),
            "run",
            "--locked",
            "--no-sync",
            "mcp-cnes",
        ],
        env=environment,
    )

    async with Client(stdio_client(parameters), read_timeout_seconds=10) as preparation:
        prepared = await preparation.call_tool(
            "cnes_load_data", {"filepath": str(FIXTURES / "valid.csv")}
        )

    async with Client(stdio_client(parameters), read_timeout_seconds=10) as client:
        tools = await client.list_tools(cache_mode="bypass")
        results = [
            await client.call_tool("cnes_statistics", {}),
            await client.call_tool("cnes_search_municipio", {"municipio": "Manaus"}),
            await client.call_tool("cnes_search_cnes", {"cnes": "1234567"}),
            await client.call_tool("cnes_search_uf", {"uf": "AM"}),
            await client.call_tool("cnes_download_instructions", {}),
            await client.call_tool("cnes_load_data", {"filepath": str(FIXTURES / "valid.csv")}),
        ]

    assert prepared.is_error is False
    assert database.exists()
    assert len(tools.tools) >= 6
    assert all(result.is_error is False for result in results)
    assert results[2].structured_content["estabelecimento"]["cnes"] == "1234567"
    assert results[0].structured_content["total_estabelecimentos"] == 1
    assert results[5].structured_content["lote_id"] == prepared.structured_content["lote_id"]


@pytest.mark.asyncio
async def test_rollback_detects_catalog_loss_before_any_reload(tmp_path: Path) -> None:
    database = tmp_path / "rollback.duckdb"
    environment = dict(os.environ)
    environment["MCP_CNES_DATABASE_PATH"] = str(database)
    environment["MCP_CNES_DATA_DIR"] = str(FIXTURES)
    environment["MCP_CNES_ALLOWED_CSV_FILES"] = "valid.csv"
    parameters = StdioServerParameters(
        command="uv",
        args=[
            "--directory",
            str(ROOT),
            "run",
            "--locked",
            "--no-sync",
            "mcp-cnes",
        ],
        env=environment,
    )

    async with Client(stdio_client(parameters), read_timeout_seconds=10) as preparation:
        prepared = await preparation.call_tool(
            "cnes_load_data", {"filepath": str(FIXTURES / "valid.csv")}
        )

    assert prepared.is_error is False
    assert database.exists()
    database.unlink()

    async with Client(stdio_client(parameters), read_timeout_seconds=10) as client:
        statistics = await client.call_tool("cnes_statistics", {})
        search = await client.call_tool("cnes_search_cnes", {"cnes": "1234567"})

    assert statistics.is_error is True
    assert search.is_error is True


def test_runbook_does_not_present_the_manual_server_as_mcp_rollback() -> None:
    runbook = (ROOT / "docs" / "cutover.md").read_text(encoding="utf-8")
    rollback_section = runbook.split("## 4. Preparar e ensaiar o rollback oficial", 1)[1]

    assert "uv run python mcp_server.py" not in runbook
    assert "last-known-good" in runbook
    assert '"mcp-cnes"' in rollback_section
    assert "MCP_CNES_DATA_DIR" in rollback_section
    assert "MCP_CNES_DATABASE_PATH" in rollback_section
