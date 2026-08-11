"""Casos de uso de consulta de estabelecimentos."""

from __future__ import annotations

from dataclasses import dataclass

from mcp_cnes.domain.models import HospitalInfo
from mcp_cnes.domain.rules import validate_bed_range

from .analytics import _validate_filters
from .ports import CNESCatalogRepository, CNESRepository


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
    def __init__(self, repository: CNESCatalogRepository) -> None:
        self._repository = repository

    def execute(
        self,
        municipality: str,
        limit: int = 50,
        min_beds: int | None = None,
        max_beds: int | None = None,
        *,
        uf: str | None = None,
        establishment_type: str | None = None,
        legal_nature: str | None = None,
        management: str | None = None,
        sus_agreement: bool | None = None,
        order_by: str = "leitos_existentes",
    ) -> SearchResult:
        _validate_limit(limit)
        validate_bed_range(min_beds, max_beds)
        filters = {
            "municipio": municipality,
            "uf": uf,
            "tipo_estabelecimento": establishment_type,
            "natureza_juridica": legal_nature,
            "gestao": management,
            "convenio_sus": sus_agreement,
            "min_leitos": min_beds,
            "max_leitos": max_beds,
        }
        effective_filters = {
            name: value for name, value in filters.items() if value is not None
        }
        _validate_filters(effective_filters)
        matches, total = self._repository.advanced_search(
            effective_filters, order_by, 0, limit
        )
        return SearchResult(tuple(matches), total, min_beds, max_beds)


class SearchByUF:
    def __init__(self, repository: CNESCatalogRepository) -> None:
        self._repository = repository

    def execute(
        self,
        uf: str,
        limit: int = 100,
        min_beds: int | None = None,
        max_beds: int | None = None,
        *,
        municipality: str | None = None,
        establishment_type: str | None = None,
        legal_nature: str | None = None,
        management: str | None = None,
        sus_agreement: bool | None = None,
        order_by: str = "leitos_existentes",
    ) -> SearchResult:
        _validate_limit(limit)
        validate_bed_range(min_beds, max_beds)
        filters = {
            "uf": uf,
            "municipio": municipality,
            "tipo_estabelecimento": establishment_type,
            "natureza_juridica": legal_nature,
            "gestao": management,
            "convenio_sus": sus_agreement,
            "min_leitos": min_beds,
            "max_leitos": max_beds,
        }
        effective_filters = {
            name: value for name, value in filters.items() if value is not None
        }
        _validate_filters(effective_filters)
        matches, total = self._repository.advanced_search(
            effective_filters, order_by, 0, limit
        )
        return SearchResult(tuple(matches), total, min_beds, max_beds)


class SearchByCNES:
    def __init__(self, repository: CNESRepository) -> None:
        self._repository = repository

    def execute(self, cnes: str) -> HospitalInfo | None:
        return self._repository.get_by_cnes(cnes)
