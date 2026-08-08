from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from mcp_cnes.infrastructure.config import Settings
from mcp_cnes.interfaces.mcp import create_mcp_server
from mcp_server import MCPServer as LegacyMCPServer

ROOT = Path(__file__).parents[2]
CONTRACTS = ROOT / "tests" / "fixtures" / "contracts"
SAMPLE = ROOT / "sample_data.csv"
CALLS = [
    ("cnes_load_data", {"filepath": str(SAMPLE)}),
    ("cnes_search_municipio", {"municipio": "São Paulo", "limit": 1}),
    ("cnes_search_cnes", {"cnes": "2077485"}),
    ("cnes_search_uf", {"uf": "RJ", "limit": 1}),
    ("cnes_statistics", {}),
    ("cnes_download_instructions", {}),
]
DYNAMIC_FIELDS = {
    "cnes_load_data": {"lote_id"},
    "cnes_statistics": {"ultima_atualizacao"},
}


def _normalise(value: dict[str, Any], ignored: set[str]) -> dict[str, Any]:
    normalised = {
        key: item for key, item in value.items() if key not in ignored and item is not None
    }
    source_file = normalised.get("arquivo_fonte")
    if isinstance(source_file, str):
        normalised["arquivo_fonte"] = Path(source_file).name
    return normalised


@pytest.mark.asyncio
async def test_legacy_and_official_servers_have_only_justified_response_differences(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = json.loads((CONTRACTS / "cutover-parity.json").read_text(encoding="utf-8"))
    monkeypatch.setenv("MCP_CNES_DASHBOARD_URL", "https://ambient.invalid/dashboard")
    settings = Settings(
        data_dir=ROOT,
        database_path=tmp_path / "official.sqlite3",
        allowed_csv_files=(SAMPLE.name,),
    )
    legacy = LegacyMCPServer(settings=settings)
    official = create_mcp_server(settings=settings)

    async with Client(official) as client:
        for tool_name, arguments in CALLS:
            legacy_result = await legacy.call_tool(tool_name, arguments)
            result = await client.call_tool(tool_name, arguments)
            assert result.is_error is False, tool_name
            official_result = _normalise(result.structured_content, set())
            rule = policy[tool_name]
            changed = set(rule["changed"])
            new_only = set(official_result) - set(legacy_result)

            assert rule["justification"].strip(), tool_name
            assert new_only == set(rule["new_only"]), tool_name
            assert set(legacy_result) - set(official_result) == set(), tool_name
            assert changed | new_only == set(rule["expected_official"]) | DYNAMIC_FIELDS.get(
                tool_name, set()
            ), tool_name
            assert _normalise(official_result, changed | new_only) == _normalise(
                legacy_result, changed
            ), tool_name
            for field, expected in rule["expected_official"].items():
                assert official_result[field] == expected, (tool_name, field)

            if tool_name == "cnes_load_data":
                assert len(official_result["lote_id"]) == 64
                int(official_result["lote_id"], 16)
                assert official_result["linhas_aceitas"] == official_result["registros_carregados"]
            if tool_name == "cnes_statistics":
                datetime.fromisoformat(official_result["ultima_atualizacao"])


@pytest.mark.asyncio
async def test_legacy_and_official_servers_expose_the_same_six_tool_names(
    tmp_path: Path,
) -> None:
    settings = Settings(database_path=tmp_path / "catalog.sqlite3")
    legacy_names = [tool["name"] for tool in LegacyMCPServer(settings=settings).get_tools()]
    async with Client(create_mcp_server(settings=settings)) as client:
        official_names = [tool.name for tool in (await client.list_tools()).tools]

    assert official_names == legacy_names
