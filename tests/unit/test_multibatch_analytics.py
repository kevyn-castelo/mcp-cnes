from __future__ import annotations

from pathlib import Path

import pytest
from mcp import Client

from mcp_cnes.domain.models import HospitalInfo
from mcp_cnes.infrastructure.config import Settings
from mcp_cnes.infrastructure.persistence import SQLiteCNESRepository
from mcp_cnes.interfaces.mcp import create_mcp_server


def hospital(
    cnes: str,
    competence: str,
    *,
    municipality: str = "Manaus",
    uf: str = "AM",
    beds: int = 50,
    sus_beds: int = 40,
    management: str = "M",
) -> HospitalInfo:
    return HospitalInfo(
        cnes=cnes,
        nome_fantasia=f"Hospital {cnes}",
        municipio=municipality,
        uf=uf,
        tipo_estabelecimento="05 - Hospital Geral",
        natureza_juridica="2062 - Sociedade Empresária",
        gestao=management,
        convenio_sus=sus_beds > 0,
        leitos_existentes=beds,
        leitos_sus=sus_beds,
        competencia=competence,
    )


def prepared_repository(tmp_path: Path) -> SQLiteCNESRepository:
    repository = SQLiteCNESRepository(
        tmp_path / "cnes.sqlite3", batch_retention_count=5
    )
    repository.replace_all(
        [
            hospital("0000001", "202501", beds=50),
            hospital("0000002", "202501", municipality="Belém", uf="PA", beds=80),
        ],
        "january.csv",
        batch_id="january",
    )
    repository.update_batch_metadata(
        "january", "fixture", "202501", {"uf": None}
    )
    repository.replace_all(
        [
            hospital("0000001", "202502", beds=60),
            hospital("0000003", "202502", beds=100, sus_beds=0),
        ],
        "february.csv",
        batch_id="february",
    )
    repository.update_batch_metadata(
        "february", "fixture", "202502", {"uf": "AM"}
    )
    return repository


def test_lots_are_retained_listed_and_can_be_activated_atomically(tmp_path: Path) -> None:
    repository = prepared_repository(tmp_path)

    batches = repository.list_batches()
    assert {item["lote_id"] for item in batches} == {"january", "february"}
    assert next(item for item in batches if item["ativo"])["lote_id"] == "february"
    assert repository.get_by_cnes("0000003") is not None

    repository.activate_batch("january")

    assert repository.get_by_cnes("0000003") is None
    assert repository.get_by_cnes("0000002") is not None
    assert next(item for item in repository.list_batches() if item["ativo"])[
        "lote_id"
    ] == "january"


def test_quality_aggregate_advanced_search_timeseries_and_diff(tmp_path: Path) -> None:
    repository = prepared_repository(tmp_path)

    quality = repository.validate_dataset("february")
    aggregate = repository.aggregate(
        "uf", "leitos_existentes", {"min_leitos": 50}, "february"
    )
    matches, total = repository.advanced_search(
        {"municipio": "mana", "convenio_sus": True},
        "leitos_existentes",
        0,
        500,
        "february",
    )
    series = repository.timeseries("0000001", "cnes", "202501", "202502")
    difference = repository.diff_batches("january", "february")

    assert quality["valido"] is True
    assert quality["competencias"] == ["202502"]
    assert aggregate == [{"grupo": "AM", "valor": 160}]
    assert total == 1
    assert [item.cnes for item in matches] == ["0000001"]
    assert [item["leitos_existentes"] for item in series] == [50, 60]
    assert difference["entraram"] == ["0000003"]
    assert difference["sairam"] == ["0000002"]
    assert difference["mudaram_leitos"][0]["cnes"] == "0000001"


def test_purging_active_lot_reactivates_latest_remaining_lot(tmp_path: Path) -> None:
    repository = prepared_repository(tmp_path)
    repository.activate_batch("january")

    removed, released = repository.purge_batch("january")

    assert removed == 2
    assert released >= 0
    assert [item["lote_id"] for item in repository.list_batches()] == ["february"]
    assert repository.get_by_cnes("0000003") is not None


def test_diff_preserves_all_cnes_competence_pairs_in_mixed_batches(tmp_path: Path) -> None:
    repository = SQLiteCNESRepository(tmp_path / "mixed.sqlite3", batch_retention_count=5)
    repository.replace_all(
        [hospital("0000001", "202501", beds=50), hospital("0000001", "202502", beds=60)],
        "a.csv",
        batch_id="mixed-a",
    )
    repository.replace_all(
        [hospital("0000001", "202501", beds=55), hospital("0000001", "202502", beds=65)],
        "b.csv",
        batch_id="mixed-b",
    )

    result = repository.diff_batches("mixed-a", "mixed-b")

    assert [(item["cnes"], item["competencia_a"]) for item in result["mudaram_leitos"]] == [
        ("0000001", "202501"),
        ("0000001", "202502"),
    ]


def test_advanced_search_preserves_total_when_offset_returns_empty_page(
    tmp_path: Path,
) -> None:
    repository = prepared_repository(tmp_path)

    items, total = repository.advanced_search({}, "cnes", 2, 100, "january")

    assert items == []
    assert total == 2


@pytest.mark.parametrize(
    ("order_by", "expected_cnes"),
    [
        ("cnes", ["0000001", "0000002"]),
        ("municipio", ["0000002", "0000001"]),
        ("leitos_existentes", ["0000002", "0000001"]),
        ("leitos_sus", ["0000001", "0000002"]),
    ],
)
def test_advanced_search_supports_every_documented_order(
    tmp_path: Path,
    order_by: str,
    expected_cnes: list[str],
) -> None:
    repository = prepared_repository(tmp_path)

    items, total = repository.advanced_search({}, order_by, 0, 100, "january")

    assert [item.cnes for item in items] == expected_cnes
    assert total == 2


@pytest.mark.asyncio
async def test_mcp_state_and_analysis_tools_use_retained_lots(tmp_path: Path) -> None:
    repository = prepared_repository(tmp_path)
    server = create_mcp_server(
        settings=Settings(database_path=tmp_path / "unused.sqlite3"),
        repository=repository,
    )

    async with Client(server) as client:
        lots = await client.call_tool("cnes_list_lotes", {})
        quality = await client.call_tool(
            "cnes_validate_dataset", {"lote_id": "january"}
        )
        aggregate = await client.call_tool(
            "cnes_aggregate",
            {
                "group_by": "uf",
                "metrica": "estabelecimentos",
                "lote_id": "january",
            },
        )
        advanced = await client.call_tool(
            "cnes_search_advanced",
            {
                "filtros": {"uf": "PA"},
                "lote_id": "january",
                "limit": 500,
            },
        )
        selected = await client.call_tool("cnes_use_lote", {"lote_id": "january"})
        legacy = await client.call_tool("cnes_search_uf", {"uf": "PA"})

    assert len(lots.structured_content["lotes"]) == 2
    assert quality.structured_content["valido"] is True
    assert aggregate.structured_content["resultados"] == [
        {"grupo": "AM", "valor": 1},
        {"grupo": "PA", "valor": 1},
    ]
    assert advanced.structured_content["total_encontrados"] == 1
    assert advanced.structured_content["estabelecimentos"][0]["municipio"] == "Belém"
    assert selected.structured_content == {"lote_id": "january", "ativo": True}
    assert legacy.structured_content["total_encontrados"] == 1
