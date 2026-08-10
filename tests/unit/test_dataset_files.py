from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from mcp import Client
from openpyxl import load_workbook

from mcp_cnes.domain.models import HospitalInfo
from mcp_cnes.infrastructure.config import Settings
from mcp_cnes.infrastructure.exports import LocalDatasetExporter
from mcp_cnes.infrastructure.importers import CsvCNESImporter
from mcp_cnes.interfaces.mcp import create_mcp_server

FIXTURES = Path(__file__).parents[1] / "fixtures" / "csv"


def hospital() -> HospitalInfo:
    return HospitalInfo(
        cnes="1234567",
        nome_fantasia="Hospital Fixture",
        municipio="Manaus",
        uf="AM",
        tipo_estabelecimento="Hospital Geral",
        natureza_juridica="Pública",
        gestao="M",
        convenio_sus=True,
        leitos_existentes=50,
        leitos_sus=40,
        competencia="202501",
    )


@pytest.mark.parametrize("format", ["csv", "json", "xlsx"])
def test_local_exporter_writes_supported_formats_atomically(
    tmp_path: Path, format: str
) -> None:
    exporter = LocalDatasetExporter(tmp_path / "exports")

    output, records = exporter.export([hospital()], format, None, "fixture")

    assert records == 1
    assert output.is_file()
    if format == "csv":
        with output.open(encoding="utf-8", newline="") as handle:
            row = next(csv.DictReader(handle))
        assert row["CNES"] == "1234567"
        assert row["COMPETENCIA"] == "202501"
    elif format == "json":
        assert json.loads(output.read_text(encoding="utf-8"))[0]["cnes"] == "1234567"
    else:
        workbook = load_workbook(output, read_only=True)
        sheet = workbook.active
        assert sheet is not None
        assert sheet["D2"].value == "1234567"
        workbook.close()


def test_export_destination_cannot_escape_configured_root(tmp_path: Path) -> None:
    exporter = LocalDatasetExporter(tmp_path / "exports")

    with pytest.raises(ValueError, match="diretório de exportação"):
        exporter.export([hospital()], "csv", tmp_path / "outside", "fixture")


def test_exporter_consumes_input_once_and_does_not_overwrite_existing_output(
    tmp_path: Path,
) -> None:
    class SinglePass:
        def __init__(self) -> None:
            self.iterations = 0

        def __iter__(self):
            self.iterations += 1
            if self.iterations > 1:
                raise AssertionError("dataset foi materializado ou percorrido novamente")
            yield hospital()

    exporter = LocalDatasetExporter(tmp_path / "exports")
    first = SinglePass()
    second = SinglePass()

    first_path, first_count = exporter.export(first, "json", None, "fixture")
    second_path, second_count = exporter.export(second, "json", None, "fixture")

    assert (first_count, second_count) == (1, 1)
    assert first.iterations == second.iterations == 1
    assert first_path.name == "fixture.json"
    assert second_path.name == "fixture-1.json"


def test_importer_accepts_portal_sus_latin1_semicolon_and_composes_labels(
    tmp_path: Path,
) -> None:
    source = tmp_path / "portal.csv"
    source.write_bytes(
        (
            "COMP;UF;MUNICIPIO;CNES;NOME_ESTABELECIMENTO;TP_GESTAO;"
            "CO_TIPO_UNIDADE;DS_TIPO_UNIDADE;NATUREZA_JURIDICA;"
            "DESC_NATUREZA_JURIDICA;LEITOS_EXISTENTES;LEITOS_SUS\n"
            "202501;SP;S\u00e3o Paulo;0000001;Hospital A;M;05;Hospital Geral;"
            "2062;Sociedade Empres\u00e1ria;80;60\n"
        ).encode("latin-1")
    )

    batch = CsvCNESImporter().import_file(source)
    try:
        item = batch.hospitals[0]
        assert item.competencia == "202501"
        assert item.tipo_estabelecimento == "05 - Hospital Geral"
        assert item.natureza_juridica == "2062 - Sociedade Empresária"
        assert item.gestao == "M"
        assert item.convenio_sus is True
    finally:
        batch.close()


@pytest.mark.asyncio
async def test_mcp_normalize_and_export_round_trip(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=FIXTURES,
        database_path=tmp_path / "cnes.sqlite3",
        allowed_csv_files=("valid.csv",),
        output_dir=tmp_path / "exports",
    )
    server = create_mcp_server(settings=settings)

    async with Client(server) as client:
        normalized = await client.call_tool(
            "cnes_normalize",
            {"filepath": str(FIXTURES / "valid.csv"), "origem": "csv_canonico"},
        )
        loaded = await client.call_tool(
            "cnes_load_data", {"filepath": str(FIXTURES / "valid.csv")}
        )
        exported = await client.call_tool(
            "cnes_export", {"formato": "json", "filtros": {"uf": "AM"}}
        )

    assert normalized.is_error is False
    assert Path(normalized.structured_content["filepath"]).is_file()
    assert normalized.structured_content["registros"] == 1
    assert loaded.is_error is False
    assert exported.is_error is False
    export_path = Path(exported.structured_content["filepath"])
    assert export_path.is_file()
    assert json.loads(export_path.read_text(encoding="utf-8"))[0]["uf"] == "AM"
