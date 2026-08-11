from __future__ import annotations

from pathlib import Path

import pytest

from mcp_cnes.infrastructure.config import Settings
from mcp_server import CNESDataLoadError, CNESDataStore, HospitalInfo, MCPServer

CSV_FIXTURES = Path(__file__).parents[1] / "fixtures" / "csv"


def test_import_normalizes_aliases_and_accents() -> None:
    store = CNESDataStore()

    summary = store.load_from_csv(CSV_FIXTURES / "valid.csv")

    assert summary.records_loaded == 1
    assert summary.rows_rejected == 0
    assert store.hospitals == [
        HospitalInfo(
            cnes="1234567",
            nome_fantasia="Hospital Exemplo",
            municipio="Manaus",
            uf="AM",
            natureza_juridica="2062 - Sociedade Empresária",
            convenio_sus=True,
            leitos_existentes=50,
            leitos_sus=40,
            competencia="2026/07",
        )
    ]


def test_missing_optional_columns_preserve_typed_defaults() -> None:
    store = CNESDataStore()

    store.load_from_csv(CSV_FIXTURES / "incomplete.csv")

    hospital = store.hospitals[0]
    assert hospital.convenio_sus is True
    assert hospital.leitos_existentes == 0
    assert hospital.leitos_sus == 0
    assert isinstance(hospital.convenio_sus, bool)
    assert isinstance(hospital.leitos_existentes, int)
    assert isinstance(hospital.leitos_sus, int)


def test_invalid_csv_does_not_replace_previous_state() -> None:
    store = CNESDataStore()
    store.load_from_csv(CSV_FIXTURES / "valid.csv")
    original = list(store.hospitals)
    original_source = store.source_file

    with pytest.raises(CNESDataLoadError, match="coluna CNES"):
        store.load_from_csv(CSV_FIXTURES / "invalid.csv")

    assert store.hospitals == original
    assert store.source_file == original_source


def test_invalid_row_is_rejected_without_corrupting_valid_rows() -> None:
    store = CNESDataStore()

    summary = store.load_from_csv(CSV_FIXTURES / "invalid_rows.csv")

    assert summary.records_loaded == 1
    assert summary.rows_read == 2
    assert summary.rows_rejected == 1
    assert store.hospitals[0].cnes == "2222222"


def test_granular_rows_are_consolidated_and_exact_duplicates_ignored() -> None:
    store = CNESDataStore()

    summary = store.load_from_csv(CSV_FIXTURES / "granular_duplicate.csv")

    assert summary.records_loaded == 1
    assert summary.rows_read == 3
    assert summary.rows_ignored == 1
    assert summary.rows_rejected == 0
    assert store.hospitals[0].leitos_existentes == 50
    assert store.hospitals[0].leitos_sus == 30
    assert store.get_statistics()["total_estabelecimentos"] == 1


@pytest.mark.asyncio
async def test_load_tool_reports_failure_without_losing_previous_data() -> None:
    server = MCPServer(
        settings=Settings(
            data_dir=CSV_FIXTURES,
            allowed_csv_files=("valid.csv", "invalid.csv"),
        )
    )
    valid = await server.call_tool(
        "cnes_load_data", {"filepath": str(CSV_FIXTURES / "valid.csv")}
    )

    invalid = await server.call_tool(
        "cnes_load_data", {"filepath": str(CSV_FIXTURES / "invalid.csv")}
    )

    assert valid["success"] is True
    assert invalid == {"success": False, "error": "CSV sem coluna CNES"}
    assert server.data_store.hospitals[0].cnes == "1234567"


@pytest.mark.asyncio
async def test_load_tool_rejects_csv_outside_configured_data_dir() -> None:
    server = MCPServer(settings=Settings(data_dir=CSV_FIXTURES / "allowed"))

    result = await server.call_tool(
        "cnes_load_data", {"filepath": str(CSV_FIXTURES / "valid.csv")}
    )

    assert result == {
        "success": False,
        "error": "Arquivo CSV nao permitido pela politica de importacao",
    }
