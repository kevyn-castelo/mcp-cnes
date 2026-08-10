from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp import Client

from mcp_cnes.domain.errors import CollectorError
from mcp_cnes.domain.models import HospitalInfo
from mcp_cnes.domain.remote import (
    RemoteCompetenceResult,
    RemoteFetchRequest,
    RemoteFetchResult,
    SourceResource,
)
from mcp_cnes.infrastructure.config import Settings
from mcp_cnes.infrastructure.persistence import MemoryCNESRepository
from mcp_cnes.interfaces.mcp import create_mcp_server


class FakeRemoteSource:
    name = "portal_sus_hospitais_leitos"

    def __init__(self, output: Path, *, error: CollectorError | None = None) -> None:
        self.output = output
        self.error = error

    def list_resources(self) -> tuple[SourceResource, ...]:
        if self.error is not None:
            raise self.error
        return (
            SourceResource(
                source=self.name,
                resource_id="fixture",
                name="Leitos 2025",
                format="CSV",
                url="https://example.invalid/leitos.csv",
                year=2025,
            ),
        )

    def list_competences(self, year: int | None = None) -> RemoteCompetenceResult:
        if self.error is not None:
            raise self.error
        selected_year = year if year is not None else 2025
        return RemoteCompetenceResult(selected_year, (f"{selected_year}01",))

    def fetch(
        self, request: RemoteFetchRequest, destination: Path | None = None
    ) -> RemoteFetchResult:
        if self.error is not None:
            raise self.error
        return RemoteFetchResult(
            filepath=self.output,
            source=self.name,
            competence=request.competence,
            records=2,
            native_filters=(),
            local_filters=("competencia", "municipio"),
            missing_fields=(),
            derived_fields=("CONVENIO_SUS",),
            from_cache=False,
            resource_id="fixture",
        )


def result_text(result: object) -> str:
    content = getattr(result, "content")
    return "\n".join(block.text for block in content if hasattr(block, "text"))


@pytest.mark.asyncio
async def test_remote_tools_are_discoverable_and_fetch_reports_filter_provenance(
    tmp_path: Path,
) -> None:
    source = FakeRemoteSource(tmp_path / "normalized.csv")
    server = create_mcp_server(
        settings=Settings(database_path=tmp_path / "cnes.sqlite3"),
        repository=MemoryCNESRepository(),
        remote_source=source,
    )

    async with Client(server) as client:
        listed = await client.list_tools(cache_mode="bypass")
        sources = await client.call_tool("cnes_list_sources", {})
        latest_competences = await client.call_tool("cnes_list_competencias", {})
        competences = await client.call_tool("cnes_list_competencias", {"ano": 2025})
        fetched = await client.call_tool(
            "cnes_fetch",
            {
                "competencia": "202501",
                "municipio": "S\u00e3o",
                "auto_load": False,
            },
        )

    names = {tool.name for tool in listed.tools}
    assert {
        "cnes_list_sources",
        "cnes_list_competencias",
        "cnes_fetch",
    }.issubset(names)
    assert sources.structured_content["fontes"][0]["status"] == "disponivel"
    assert latest_competences.structured_content == competences.structured_content
    assert competences.structured_content["ano_consultado"] == 2025
    assert competences.structured_content["competencias_disponiveis"] == ["202501"]
    assert fetched.structured_content == {
        "filepath": str(source.output),
        "lote_id": None,
        "registros": 2,
        "filtros_nativos": [],
        "filtros_locais": ["competencia", "municipio"],
        "fonte_usada": source.name,
        "campos_nao_preenchidos": [],
        "campos_derivados": ["CONVENIO_SUS"],
        "cache": False,
        "etag": None,
    }


@pytest.mark.asyncio
async def test_remote_failure_returns_structured_actionable_error(tmp_path: Path) -> None:
    source = FakeRemoteSource(
        tmp_path / "unused.csv",
        error=CollectorError(
            "remote_server_error",
            "remote_request",
            "fonte temporariamente indisponÃ­vel",
            retryable=True,
            status_code=503,
        ),
    )
    server = create_mcp_server(
        settings=Settings(database_path=tmp_path / "cnes.sqlite3"),
        repository=MemoryCNESRepository(),
        remote_source=source,
    )

    async with Client(server) as client:
        result = await client.call_tool(
            "cnes_fetch", {"competencia": "202501", "auto_load": False}
        )

    assert result.is_error is True
    message = result_text(result)
    payload = json.loads(message[message.index("{") :])
    assert payload["erro"] == "remote_server_error"
    assert payload["causa"] == "fonte temporariamente indisponÃ­vel"
    assert "retry" in payload["sugestao"]
    assert "Traceback" not in result_text(result)


@pytest.mark.asyncio
async def test_unavailable_competence_year_returns_structured_actionable_error(
    tmp_path: Path,
) -> None:
    source = FakeRemoteSource(
        tmp_path / "unused.csv",
        error=CollectorError(
            "remote_competence_unavailable",
            "catalog_select",
            "A fonte oficial não publicou arquivo CSV para 2024",
            status_code=404,
        ),
    )
    server = create_mcp_server(
        settings=Settings(database_path=tmp_path / "cnes.sqlite3"),
        repository=MemoryCNESRepository(),
        remote_source=source,
    )

    async with Client(server) as client:
        result = await client.call_tool("cnes_list_competencias", {"ano": 2024})

    assert result.is_error is True
    payload = json.loads(result_text(result))
    assert payload == {
        "erro": "remote_competence_unavailable",
        "causa": "A fonte oficial não publicou arquivo CSV para 2024",
        "sugestao": (
            "Use cnes_list_sources para ver os anos ou omita ano em cnes_list_competencias."
        ),
    }
    assert "Traceback" not in result_text(result)


@pytest.mark.asyncio
async def test_remote_auto_load_registers_source_competence_and_filters_atomically(
    tmp_path: Path,
) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "csv" / "valid.csv"
    source = FakeRemoteSource(fixture)
    server = create_mcp_server(
        settings=Settings(database_path=tmp_path / "cnes.sqlite3"),
        remote_source=source,
    )

    async with Client(server) as client:
        fetched = await client.call_tool(
            "cnes_fetch",
            {"competencia": "202501", "municipio": "Manaus"},
        )
        lots = await client.call_tool("cnes_list_lotes", {})

    assert fetched.is_error is False
    lot = lots.structured_content["lotes"][0]
    assert lot["lote_id"] == fetched.structured_content["lote_id"]
    assert lot["fonte"] == source.name
    assert lot["competencia"] == "202501"
    assert lot["filtros"]["municipio"] == "Manaus"
    assert lot["ativo"] is True


@pytest.mark.asyncio
async def test_controlled_tool_errors_are_exact_structured_json(tmp_path: Path) -> None:
    server = create_mcp_server(
        settings=Settings(database_path=tmp_path / "unused.sqlite3"),
        repository=MemoryCNESRepository(),
    )
    async with Client(server) as client:
        result = await client.call_tool("cnes_use_lote", {"lote_id": "missing"})

    payload = json.loads(result_text(result))
    assert set(payload) == {"erro", "causa", "sugestao"}
    assert payload["causa"] == "Lote inexistente: missing"


@pytest.mark.asyncio
async def test_memory_repository_is_substitutable_for_catalog_state_tools(
    tmp_path: Path,
) -> None:
    repository = MemoryCNESRepository()
    repository.replace_all(
        [
            HospitalInfo(
                cnes="1234567", nome_fantasia="Hospital", municipio="Manaus",
                uf="AM", leitos_existentes=50, leitos_sus=40, competencia="202501",
            )
        ],
        "fixture.csv",
        batch_id="memory-batch",
    )
    server = create_mcp_server(
        settings=Settings(database_path=tmp_path / "unused.sqlite3"),
        repository=repository,
    )
    async with Client(server) as client:
        lots = await client.call_tool("cnes_list_lotes", {})
        quality = await client.call_tool(
            "cnes_validate_dataset", {"lote_id": "memory-batch"}
        )
        search = await client.call_tool(
            "cnes_search_advanced", {"filtros": {"uf": "AM"}}
        )
        aggregate = await client.call_tool(
            "cnes_aggregate", {"group_by": "uf", "metrica": "media_leitos"}
        )

    assert lots.is_error is False
    assert quality.is_error is False
    assert search.structured_content["total_encontrados"] == 1
    assert aggregate.structured_content["resultados"] == [{"grupo": "AM", "valor": 50.0}]


@pytest.mark.asyncio
async def test_same_content_with_different_remote_filters_creates_distinct_lots(
    tmp_path: Path,
) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "csv" / "valid.csv"
    server = create_mcp_server(
        settings=Settings(database_path=tmp_path / "cnes.sqlite3", batch_retention_count=5),
        remote_source=FakeRemoteSource(fixture),
    )
    async with Client(server) as client:
        first = await client.call_tool("cnes_fetch", {"competencia": "202501", "uf": "AM"})
        second = await client.call_tool(
            "cnes_fetch", {"competencia": "202501", "municipio": "Manaus"}
        )
        lots = await client.call_tool("cnes_list_lotes", {})

    assert first.structured_content["lote_id"] != second.structured_content["lote_id"]
    assert len(lots.structured_content["lotes"]) == 2
