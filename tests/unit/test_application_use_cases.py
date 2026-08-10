from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from mcp_cnes.application import (
    GetStatistics,
    LoadData,
    SearchByCNES,
    SearchByMunicipality,
    SearchByUF,
)
from mcp_cnes.domain.models import HospitalInfo, ImportBatch, LoadSummary
from mcp_cnes.infrastructure.persistence import MemoryCNESRepository


def hospital(cnes: str, municipality: str, uf: str, beds: int) -> HospitalInfo:
    return HospitalInfo(cnes, f"Hospital {cnes}", municipality, uf, leitos_existentes=beds)


class FakeImporter:
    def __init__(self, items: Sequence[HospitalInfo]) -> None:
        self.items = items

    def import_file(self, filepath: Path) -> ImportBatch:
        return ImportBatch(
            self.items,
            LoadSummary(len(self.items), len(self.items), 0, 0),
            str(filepath),
        )


def populated_repository() -> MemoryCNESRepository:
    repository = MemoryCNESRepository()
    repository.replace_all(
        [
            hospital("0000001", "Manaus", "AM", 49),
            hospital("0000002", "Manaus", "AM", 50),
            hospital("0000003", "Parintins", "AM", 150),
            hospital("0000004", "Belém", "PA", 151),
        ],
        "fake.csv",
    )
    return repository


def test_load_data_executes_with_in_memory_fake() -> None:
    repository = MemoryCNESRepository()
    importer = FakeImporter((hospital("0000001", "Manaus", "AM", 50),))

    summary = LoadData(repository, importer).execute(Path("fixture.csv"))

    assert summary.records_loaded == 1
    assert repository.hospitals[0].cnes == "0000001"
    assert repository.source_file == "fixture.csv"


def test_load_data_closes_staged_hospitals_after_consumption() -> None:
    class ClosableHospitals(list[HospitalInfo]):
        closed = False

        def close(self) -> None:
            self.closed = True

    items = ClosableHospitals([hospital("0000001", "Manaus", "AM", 50)])

    LoadData(MemoryCNESRepository(), FakeImporter(items)).execute(Path("fixture.csv"))

    assert items.closed is True


class SnapshotOnlyRepository(MemoryCNESRepository):
    def search_by_municipality(
        self,
        municipality: str,
        min_beds: int | None,
        max_beds: int | None,
        limit: int | None = None,
    ) -> list[HospitalInfo]:
        raise AssertionError("legacy split query must not be called")

    def count_by_municipality(
        self, municipality: str, min_beds: int | None, max_beds: int | None
    ) -> int:
        raise AssertionError("legacy split query must not be called")

    def search_by_municipality_with_count(
        self,
        municipality: str,
        min_beds: int | None,
        max_beds: int | None,
        limit: int,
    ) -> tuple[Sequence[HospitalInfo], int]:
        return ([hospital("0000001", "Manaus", "AM", 50)], 7)

    def advanced_search(
        self,
        filters,
        order_by: str,
        offset: int,
        limit: int,
        batch_id: str | None = None,
    ):
        assert filters["municipio"] == "Manaus"
        assert order_by == "leitos_existentes"
        assert (offset, limit, batch_id) == (0, 1, None)
        return ([hospital("0000001", "Manaus", "AM", 50)], 7)


def test_search_by_municipality_executes_with_in_memory_fake() -> None:
    result = SearchByMunicipality(populated_repository()).execute(
        "mana", min_beds=50, max_beds=150
    )

    assert result.total_available == 1
    assert [item.cnes for item in result.items] == ["0000002"]


def test_search_uses_atomic_items_and_count_repository_operation() -> None:
    result = SearchByMunicipality(SnapshotOnlyRepository()).execute("Manaus", limit=1)

    assert [item.cnes for item in result.items] == ["0000001"]
    assert result.total_available == 7


def test_search_by_cnes_executes_with_in_memory_fake() -> None:
    result = SearchByCNES(populated_repository()).execute("0000003")

    assert result is not None
    assert result.municipio == "Parintins"


def test_search_by_uf_executes_with_in_memory_fake_and_limit() -> None:
    result = SearchByUF(populated_repository()).execute("am", limit=2, min_beds=49)

    assert result.total_available == 3
    assert len(result.items) == 2


def test_statistics_executes_with_in_memory_fake() -> None:
    result = GetStatistics(populated_repository()).execute()

    assert result["total_estabelecimentos"] == 4
    assert result["total_leitos_existentes"] == 400
    assert result["estabelecimentos_por_uf"] == {"AM": 3, "PA": 1}
