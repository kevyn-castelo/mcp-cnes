from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema.validators import validator_for
from mcp import Client
from mcp.types import LATEST_PROTOCOL_VERSION

from mcp_cnes.infrastructure.config import Settings
from mcp_cnes.interfaces.mcp import create_mcp_server

FIXTURES = Path(__file__).parents[1] / "fixtures"
EXPECTED_NAMES = [
    "cnes_load_data",
    "cnes_search_municipio",
    "cnes_search_cnes",
    "cnes_search_uf",
    "cnes_statistics",
    "cnes_download_instructions",
]
EXPANSION_NAMES = [
    "cnes_list_sources",
    "cnes_list_competencias",
    "cnes_fetch",
    "cnes_validate_dataset",
    "cnes_list_lotes",
    "cnes_use_lote",
    "cnes_purge",
    "cnes_aggregate",
    "cnes_timeseries",
    "cnes_diff",
    "cnes_search_advanced",
    "cnes_search_advanced_v2",
    "cnes_group_by_mantenedora",
    "cnes_leads_triggers",
    "cnes_score_leads",
    "cnes_normalize",
    "cnes_export",
]
V2_TOOLS = {
    "cnes_search_advanced_v2",
    "cnes_group_by_mantenedora",
    "cnes_leads_triggers",
    "cnes_score_leads",
}


def canonical_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def result_text(result: Any) -> str:
    return "\n".join(block.text for block in result.content if hasattr(block, "text"))


@pytest.mark.asyncio
async def test_tool_catalog_matches_sdk_snapshot_and_has_valid_schemas() -> None:
    expected = json.loads(
        (FIXTURES / "contracts" / "sdk-tools.snapshot.json").read_text(encoding="utf-8")
    )
    expected_expansion = json.loads(
        (FIXTURES / "contracts" / "sdk-expansion-tools.snapshot.json").read_text(
            encoding="utf-8"
        )
    )

    async with Client(create_mcp_server()) as client:
        listed = await client.list_tools(cache_mode="bypass")

    actual = []
    for tool in listed.tools:
        assert tool.input_schema["additionalProperties"] is False
        assert tool.input_schema["x-cnes-contract-version"] == (
            "v2" if tool.name in V2_TOOLS else "v1"
        )
        if tool.name == "cnes_export":
            assert tool.input_schema["x-cnes-contract-versions"] == ["v1", "v2"]
        validator_for(tool.input_schema).check_schema(tool.input_schema)
        assert tool.output_schema is not None
        validator_for(tool.output_schema).check_schema(tool.output_schema)
        actual.append(
            {
                "name": tool.name,
                "input_properties": list(tool.input_schema.get("properties", {})),
                "input_required": tool.input_schema.get("required", []),
                "output_properties": list(tool.output_schema.get("properties", {})),
                "sha256": canonical_hash(
                    tool.model_dump(by_alias=True, exclude_none=True, mode="json")
                ),
            }
        )

    assert [tool.name for tool in listed.tools] == EXPECTED_NAMES + EXPANSION_NAMES
    assert actual == expected + expected_expansion


@pytest.mark.asyncio
async def test_official_client_calls_all_six_tools_with_controlled_fixture(
    tmp_path: Path,
) -> None:
    server = create_mcp_server(
        settings=Settings(
            data_dir=FIXTURES / "csv",
            database_path=tmp_path / "cnes.sqlite3",
            allowed_csv_files=("valid.csv",),
        )
    )
    csv_path = FIXTURES / "csv" / "valid.csv"

    async with Client(server) as client:
        loaded = await client.call_tool("cnes_load_data", {"filepath": str(csv_path)})
        municipality = await client.call_tool(
            "cnes_search_municipio",
            {"municipio": "Manaus", "min_leitos": 50, "max_leitos": 50},
        )
        cnes = await client.call_tool("cnes_search_cnes", {"cnes": "1234567"})
        uf = await client.call_tool("cnes_search_uf", {"uf": "am"})
        statistics = await client.call_tool("cnes_statistics", {})
        instructions = await client.call_tool("cnes_download_instructions", {})

    results = [loaded, municipality, cnes, uf, statistics, instructions]
    assert all(result.is_error is False for result in results)
    assert loaded.structured_content["registros_carregados"] == 1
    assert municipality.structured_content["total_encontrados"] == 1
    assert cnes.structured_content["estabelecimento"]["cnes"] == "1234567"
    assert uf.structured_content["uf"] == "AM"
    assert statistics.structured_content["total_estabelecimentos"] == 1
    assert instructions.structured_content["url"].startswith("https://")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool, arguments, expected_message",
    [
        ("cnes_search_cnes", {"cnes": "123"}, "sete dígitos"),
        ("cnes_search_uf", {"uf": "Amazonas"}, "at most 2 characters"),
        ("cnes_search_uf", {"uf": "AM", "limit": 0}, "greater than or equal to 1"),
        ("cnes_search_uf", {"uf": "AM", "min_leitos": -1}, "greater than or equal to 0"),
        (
            "cnes_download_instructions",
            {"extra": True},
            "Parâmetros não permitidos",
        ),
    ],
)
async def test_invalid_inputs_return_actionable_tool_errors(
    tool: str, arguments: dict[str, Any], expected_message: str
) -> None:
    async with Client(create_mcp_server()) as client:
        result = await client.call_tool(tool, arguments)

    message = result_text(result)
    assert result.is_error is True
    assert expected_message.casefold() in message.casefold()
    assert "Traceback" not in message
    assert str(Path.cwd()).casefold() not in message.casefold()


@pytest.mark.asyncio
async def test_business_validation_and_missing_file_do_not_leak_internal_paths(
    tmp_path: Path,
) -> None:
    csv_path = FIXTURES / "csv" / "valid.csv"
    server = create_mcp_server(
        settings=Settings(
            data_dir=FIXTURES / "csv",
            database_path=tmp_path / "cnes.sqlite3",
            allowed_csv_files=("valid.csv",),
        )
    )
    async with Client(server) as client:
        missing = await client.call_tool(
            "cnes_load_data", {"filepath": str(Path.cwd() / "private" / "missing.csv")}
        )
        await client.call_tool("cnes_load_data", {"filepath": str(csv_path)})
        inverted = await client.call_tool(
            "cnes_search_municipio",
            {"municipio": "Manaus", "min_leitos": 151, "max_leitos": 150},
        )

    assert missing.is_error is True
    assert "politica de importacao" in result_text(missing)
    assert "private" not in result_text(missing)
    assert inverted.is_error is True
    assert "não pode ser maior" in result_text(inverted)


@pytest.mark.asyncio
async def test_current_and_legacy_protocol_modes_are_supported() -> None:
    server = create_mcp_server()
    async with Client(server) as current:
        assert current.protocol_version == LATEST_PROTOCOL_VERSION
        current_result = await current.call_tool("cnes_download_instructions", {})

    async with Client(server, mode="legacy") as legacy:
        assert legacy.protocol_version != LATEST_PROTOCOL_VERSION
        legacy_result = await legacy.call_tool("cnes_download_instructions", {})

    assert current_result.is_error is False
    assert legacy_result.is_error is False
