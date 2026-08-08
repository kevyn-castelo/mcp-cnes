from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_official_stdio_entrypoint_loads_and_queries_fixture(
    tmp_path: Path,
) -> None:
    environment = dict(os.environ)
    environment["PYTHONUNBUFFERED"] = "1"
    fixtures = Path(__file__).parents[1] / "fixtures" / "csv"
    environment["MCP_CNES_DATA_DIR"] = str(fixtures)
    environment["MCP_CNES_DATABASE_PATH"] = str(tmp_path / "cnes.sqlite3")
    environment["MCP_CNES_ALLOWED_CSV_FILES"] = "valid.csv"
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_cnes"],
        env=environment,
    )

    async with Client(stdio_client(parameters)) as client:
        tools = await client.list_tools(cache_mode="bypass")
        loaded = await client.call_tool(
            "cnes_load_data", {"filepath": str(fixtures / "valid.csv")}
        )
        result = await client.call_tool(
            "cnes_search_municipio", {"municipio": "Manaus"}
        )

    assert len(tools.tools) == 6
    assert loaded.is_error is False
    assert loaded.structured_content["registros_carregados"] == 1
    assert result.is_error is False
    assert result.structured_content["total_encontrados"] == 1
    assert result.structured_content["estabelecimentos"][0]["cnes"] == "1234567"
