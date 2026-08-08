"""Casos de uso de consulta de estabelecimentos."""

from __future__ import annotations

from dataclasses import dataclass

from mcp_cnes.domain.models import HospitalInfo
from mcp_cnes.domain.rules import validate_bed_range

from .ports import CNESRepository


def _validate_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit deve ser um inteiro maior que zero")
    return limit


@dataclass(frozen=True)
class SearchResult:
    """Resultado paginado sem perder o total disponível."""

    items: tuple[HospitalInfo, ...]
    total_available: int
    min_beds: int | None
    max_beds: int | None


class SearchByMunicipality:
    def __init__(self, repository: CNESRepository) -> None:
        self._repository = repository

    def execute(
        self,
        municipality: str,
        limit: int = 50,
        min_beds: int | None = None,
        max_beds: int | None = None,
    ) -> SearchResult:
        _validate_limit(limit)
        validate_bed_range(min_beds, max_beds)
        matches, total = self._repository.search_by_municipality_with_count(
            municipality, min_beds, max_beds, limit
        )
        return SearchResult(tuple(matches), total, min_beds, max_beds)


class SearchByUF:
    def __init__(self, repository: CNESRepository) -> None:
        self._repository = repository

    def execute(
        self,
        uf: str,
        limit: int = 100,
        min_beds: int | None = None,
        max_beds: int | None = None,
    ) -> SearchResult:
        _validate_limit(limit)
        validate_bed_range(min_beds, max_beds)
        matches, total = self._repository.search_by_uf_with_count(
            uf, min_beds, max_beds, limit
        )
        return SearchResult(tuple(matches), total, min_beds, max_beds)


class SearchByCNES:
    def __init__(self, repository: CNESRepository) -> None:
        self._repository = repository

    def execute(self, cnes: str) -> HospitalInfo | None:
        return self._repository.get_by_cnes(cnes)
