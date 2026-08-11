from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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

    def __init__(
        self,
        output: Path,
        *,
        error: CollectorError | None = None,
        download_cache_hit: bool = False,
        etag: str | None = None,
    ) -> None:
        self.output = output
        self.error = error
        self.download_cache_hit = download_cache_hit
        self.etag = etag

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
            etag=self.etag,
            download_cache_hit=self.download_cache_hit,
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
        "filepath": source.output.name,
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
async def test_fetch_response_reports_annual_download_cache_hit(tmp_path: Path) -> None:
    source = FakeRemoteSource(
        tmp_path / "normalized.csv",
        download_cache_hit=True,
        etag='"annual"',
    )
    server = create_mcp_server(
        settings=Settings(database_path=tmp_path / "cnes.sqlite3"),
        repository=MemoryCNESRepository(),
        remote_source=source,
    )

    async with Client(server) as client:
        fetched = await client.call_tool(
            "cnes_fetch", {"competencia": "202501", "auto_load": False}
        )

    assert fetched.structured_content["cache"] is True
    assert fetched.structured_content["etag"] == '"annual"'


@pytest.mark.asyncio
async def test_remote_failure_returns_structured_actionable_error(tmp_path: Path) -> None:
    source = FakeRemoteSource(
        tmp_path / "unused.csv",
        error=CollectorError(
            "remote_server_error",
            "remote_request",
            "fonte temporariamente indisponível",
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
    assert payload["causa"] == "A fonte remota está temporariamente indisponível."
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
        "causa": "A fonte oficial não possui competências para o período solicitado.",
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
async def test_internal_tool_errors_are_generic_and_do_not_leak_details(
    tmp_path: Path,
) -> None:
    class ExplodingRepository(MemoryCNESRepository):
        def has_data(self) -> bool:
            raise RuntimeError(
                f'duckdb.IOException: File "{tmp_path / "private.duckdb"}" SELECT * FROM secrets'
            )

    server = create_mcp_server(
        settings=Settings(database_path=tmp_path / "unused.sqlite3"),
        repository=ExplodingRepository(),
    )
    async with Client(server) as client:
        result = await client.call_tool("cnes_statistics", {})

    payload = json.loads(result_text(result))
    assert payload["erro"] == "internal_error"
    assert payload["causa"] == "Não foi possível concluir a operação solicitada."
    assert "duckdb" not in result_text(result).casefold()
    assert "private" not in result_text(result).casefold()
    assert "select" not in result_text(result).casefold()


@pytest.mark.asyncio
async def test_unclassified_value_errors_cannot_publish_credentials_or_paths(
    tmp_path: Path,
) -> None:
    class ExplodingRepository(MemoryCNESRepository):
        def has_data(self) -> bool:
            raise ValueError(
                "Falha em https://alice:s3cr3t@example.test/private e "
                'C:\\Users\\Jane Doe\\private.duckdb'
            )

    server = create_mcp_server(
        settings=Settings(database_path=tmp_path / "unused.sqlite3"),
        repository=ExplodingRepository(),
    )
    async with Client(server) as client:
        result = await client.call_tool("cnes_statistics", {})

    payload = json.loads(result_text(result))
    assert payload["erro"] == "internal_error"
    assert payload["causa"] == "Não foi possível concluir a operação solicitada."
    for secret in ("alice", "s3cr3t", "example.test", "Jane Doe", "private"):
        assert secret.casefold() not in result_text(result).casefold()


@pytest.mark.asyncio
async def test_repository_value_errors_inside_use_cases_remain_internal(
    tmp_path: Path,
) -> None:
    class ExplodingRepository(MemoryCNESRepository):
        def list_batches(self) -> list[dict[str, Any]]:
            raise ValueError(
                "backend endpoint=https://internal-db.example/private "
                "token=supersecret tenant=acme"
            )

    server = create_mcp_server(
        settings=Settings(database_path=tmp_path / "unused.sqlite3"),
        repository=ExplodingRepository(),
    )
    async with Client(server) as client:
        result = await client.call_tool("cnes_list_lotes", {})

    payload = json.loads(result_text(result))
    assert payload["erro"] == "internal_error"
    assert payload["causa"] == "Não foi possível concluir a operação solicitada."
    for secret in ("internal-db", "token", "supersecret", "tenant", "acme"):
        assert secret.casefold() not in result_text(result).casefold()


@pytest.mark.asyncio
async def test_purge_is_disabled_by_default_even_with_confirmation(tmp_path: Path) -> None:
    repository = MemoryCNESRepository()
    repository.replace_all(
        [
            HospitalInfo(
                cnes="1234567",
                nome_fantasia="Hospital",
                municipio="Manaus",
                uf="AM",
                leitos_existentes=50,
                leitos_sus=40,
                competencia="202501",
            )
        ],
        "fixture.csv",
        batch_id="protected-batch",
    )
    server = create_mcp_server(
        settings=Settings(database_path=tmp_path / "unused.sqlite3"),
        repository=repository,
    )
    async with Client(server) as client:
        result = await client.call_tool(
            "cnes_purge",
            {
                "lote_id": "protected-batch",
                "confirmacao": "EXCLUIR_LOTE:protected-batch",
            },
        )
        lots = await client.call_tool("cnes_list_lotes", {})

    assert result.is_error is True
    assert "MCP_CNES_ALLOW_PURGE=true" in result_text(result)
    assert [item["lote_id"] for item in lots.structured_content["lotes"]] == [
        "protected-batch"
    ]


@pytest.mark.asyncio
async def test_enabled_purge_requires_exact_confirmation_and_then_removes_batch(
    tmp_path: Path,
) -> None:
    repository = MemoryCNESRepository()
    repository.replace_all(
        [
            HospitalInfo(
                cnes="1234567",
                nome_fantasia="Hospital",
                municipio="Manaus",
                uf="AM",
                leitos_existentes=50,
                leitos_sus=40,
                competencia="202501",
            )
        ],
        "fixture.csv",
        batch_id="removable-batch",
    )
    server = create_mcp_server(
        settings=Settings(
            database_path=tmp_path / "unused.sqlite3",
            allow_purge=True,
        ),
        repository=repository,
    )
    async with Client(server) as client:
        rejected = await client.call_tool(
            "cnes_purge",
            {"lote_id": "removable-batch", "confirmacao": "EXCLUIR_LOTE:other"},
        )
        retained = await client.call_tool("cnes_list_lotes", {})
        purged = await client.call_tool(
            "cnes_purge",
            {
                "lote_id": "removable-batch",
                "confirmacao": "EXCLUIR_LOTE:removable-batch",
            },
        )
        remaining = await client.call_tool("cnes_list_lotes", {})

    assert rejected.is_error is True
    assert retained.structured_content["lotes"][0]["lote_id"] == "removable-batch"
    assert purged.is_error is False
    assert purged.structured_content["lote_id"] == "removable-batch"
    assert purged.structured_content["itens_removidos"] == 1
    assert remaining.structured_content["lotes"] == []


@pytest.mark.asyncio
async def test_enabled_cache_purge_requires_cache_confirmation(tmp_path: Path) -> None:
    class PurgeableSource(FakeRemoteSource):
        def __init__(self, output: Path) -> None:
            super().__init__(output)
            self.calls = 0

        def purge_cache(self) -> tuple[int, int]:
            self.calls += 1
            return 2, 1024

    source = PurgeableSource(tmp_path / "unused.csv")
    server = create_mcp_server(
        settings=Settings(
            database_path=tmp_path / "unused.sqlite3",
            allow_purge=True,
        ),
        repository=MemoryCNESRepository(),
        remote_source=source,
    )
    async with Client(server) as client:
        rejected = await client.call_tool(
            "cnes_purge", {"confirmacao": "EXCLUIR_LOTE:any"}
        )
        purged = await client.call_tool(
            "cnes_purge", {"confirmacao": "LIMPAR_CACHE"}
        )

    assert rejected.is_error is True
    assert source.calls == 1
    assert purged.structured_content == {
        "lote_id": None,
        "itens_removidos": 2,
        "bytes_liberados": 1024,
    }


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
async def test_statistics_and_batch_list_hide_absolute_source_paths(tmp_path: Path) -> None:
    repository = MemoryCNESRepository()
    source_path = tmp_path / "private" / "source.csv"
    repository.replace_all(
        [
            HospitalInfo(
                cnes="1234567",
                nome_fantasia="Hospital",
                municipio="Manaus",
                uf="AM",
                leitos_existentes=50,
                leitos_sus=40,
                competencia="202501",
            )
        ],
        str(source_path),
        batch_id="private-source",
    )
    server = create_mcp_server(
        settings=Settings(database_path=tmp_path / "unused.sqlite3"),
        repository=repository,
    )

    async with Client(server) as client:
        statistics = await client.call_tool("cnes_statistics", {})
        lots = await client.call_tool("cnes_list_lotes", {})

    assert statistics.structured_content["arquivo_fonte"] == "source.csv"
    assert lots.structured_content["lotes"][0]["arquivo_fonte"] == "source.csv"
    assert str(tmp_path) not in result_text(statistics)
    assert str(tmp_path) not in result_text(lots)


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
