from __future__ import annotations

import os
import sys

import pytest
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_official_stdio_entrypoint_has_clean_protocol_output() -> None:
    environment = dict(os.environ)
    environment["PYTHONUNBUFFERED"] = "1"
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_cnes"],
        env=environment,
    )

    async with Client(stdio_client(parameters)) as client:
        tools = await client.list_tools(cache_mode="bypass")
        result = await client.call_tool("cnes_download_instructions", {})

    assert len(tools.tools) == 6
    assert result.is_error is False
    assert result.structured_content["titulo"] == "Instruções para Download de Dados CNES"
