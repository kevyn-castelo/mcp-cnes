from __future__ import annotations

import pytest

from mcp_cnes.application import SearchByMunicipality, SearchByUF
from mcp_cnes.domain.models import HospitalInfo
from mcp_cnes.domain.rules import validate_bed_range
from mcp_cnes.infrastructure.persistence import MemoryCNESRepository


def hospital(cnes: str, beds: int, uf: str = "AM") -> HospitalInfo:
    return HospitalInfo(
        cnes=cnes,
        nome_fantasia=f"Hospital {beds}",
        municipio="Manaus",
        uf=uf,
        leitos_existentes=beds,
    )


def repository_with_beds(*hospitals: HospitalInfo) -> MemoryCNESRepository:
    repository = MemoryCNESRepository()
    repository.replace_all(hospitals, "fixture.csv")
    return repository


def test_search_accepts_custom_inclusive_bed_range() -> None:
    repository = repository_with_beds(
        hospital("0000049", 49),
        hospital("0000050", 50),
        hospital("0000150", 150),
        hospital("0000151", 151),
    )

    unfiltered = SearchByMunicipality(repository).execute("Manaus", limit=2)
    filtered = SearchByMunicipality(repository).execute(
        "Manaus", min_beds=50, max_beds=150
    )

    assert unfiltered.total_available == 4
    assert len(unfiltered.items) == 2
    assert [item.leitos_existentes for item in filtered.items] == [150, 50]
    assert filtered.total_available == 2


def test_uf_search_supports_one_sided_range() -> None:
    repository = repository_with_beds(hospital("0000050", 50), hospital("0000151", 151))

    result = SearchByUF(repository).execute("AM", min_beds=100)

    assert [item.cnes for item in result.items] == ["0000151"]


@pytest.mark.parametrize(
    ("minimum", "maximum", "message"),
    [
        (-1, None, "não pode ser negativo"),
        (151, 150, "não pode ser maior"),
    ],
)
def test_rejects_invalid_bed_ranges(
    minimum: int | None, maximum: int | None, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_bed_range(minimum, maximum)


def test_rejects_non_integer_bed_ranges() -> None:
    with pytest.raises(ValueError, match="deve ser um inteiro"):
        validate_bed_range("50", None)  # type: ignore[arg-type]
