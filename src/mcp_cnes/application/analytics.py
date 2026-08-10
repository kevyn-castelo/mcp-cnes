"""Casos de uso de qualidade, estado e análise sobre lotes retidos."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from mcp_cnes.domain.models import HospitalInfo
from mcp_cnes.domain.rules import validate_bed_range

from .ports import CNESCatalogRepository
from .remote import validate_competence


@dataclass(frozen=True)
class AdvancedSearchResult:
    items: tuple[HospitalInfo, ...]
    total_available: int
    offset: int
    limit: int


class ListBatches:
    def __init__(self, repository: CNESCatalogRepository) -> None:
        self._repository = repository

    def execute(self) -> Sequence[dict[str, Any]]:
        return self._repository.list_batches()


class UseBatch:
    def __init__(self, repository: CNESCatalogRepository) -> None:
        self._repository = repository

    def execute(self, batch_id: str) -> None:
        if not batch_id.strip():
            raise ValueError("lote_id não pode ser vazio")
        self._repository.activate_batch(batch_id)


class PurgeBatch:
    def __init__(self, repository: CNESCatalogRepository) -> None:
        self._repository = repository

    def execute(self, batch_id: str) -> tuple[int, int]:
        if not batch_id.strip():
            raise ValueError("lote_id não pode ser vazio")
        return self._repository.purge_batch(batch_id)


class ValidateDataset:
    def __init__(self, repository: CNESCatalogRepository) -> None:
        self._repository = repository

    def execute(self, batch_id: str | None = None) -> dict[str, Any]:
        return self._repository.validate_dataset(batch_id)


class AggregateData:
    def __init__(self, repository: CNESCatalogRepository) -> None:
        self._repository = repository

    def execute(
        self,
        group_by: str,
        metric: str,
        filters: Mapping[str, Any],
        batch_id: str | None = None,
    ) -> Sequence[dict[str, Any]]:
        _validate_filters(filters)
        return self._repository.aggregate(group_by, metric, filters, batch_id)


class TimeSeries:
    def __init__(self, repository: CNESCatalogRepository) -> None:
        self._repository = repository

    def execute(
        self, key: str, key_type: str, start: str, end: str
    ) -> Sequence[dict[str, Any]]:
        validate_competence(start)
        validate_competence(end)
        if start > end:
            raise ValueError("de não pode ser posterior a ate")
        if not key.strip():
            raise ValueError("chave não pode ser vazia")
        return self._repository.timeseries(key, key_type, start, end)


class DiffBatches:
    def __init__(self, repository: CNESCatalogRepository) -> None:
        self._repository = repository

    def execute(self, batch_a: str, batch_b: str) -> dict[str, Any]:
        if batch_a == batch_b:
            raise ValueError("lote_a e lote_b devem ser diferentes")
        return self._repository.diff_batches(batch_a, batch_b)


class AdvancedSearch:
    def __init__(self, repository: CNESCatalogRepository) -> None:
        self._repository = repository

    def execute(
        self,
        filters: Mapping[str, Any],
        order_by: str = "cnes",
        offset: int = 0,
        limit: int = 100,
        batch_id: str | None = None,
    ) -> AdvancedSearchResult:
        _validate_filters(filters)
        if isinstance(offset, bool) or offset < 0:
            raise ValueError("offset deve ser um inteiro não negativo")
        if isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("limit deve estar entre 1 e 500")
        items, total = self._repository.advanced_search(
            filters, order_by, offset, limit, batch_id
        )
        return AdvancedSearchResult(tuple(items), total, offset, limit)


def _validate_filters(filters: Mapping[str, Any]) -> None:
    allowed = {
        "uf",
        "municipio",
        "tipo_estabelecimento",
        "natureza_juridica",
        "gestao",
        "convenio_sus",
        "min_leitos",
        "max_leitos",
    }
    extras = set(filters) - allowed
    if extras:
        raise ValueError(f"Filtros não suportados: {', '.join(sorted(extras))}")
    uf = filters.get("uf")
    if uf is not None and (not isinstance(uf, str) or len(uf) != 2 or not uf.isalpha()):
        raise ValueError("uf deve conter exatamente duas letras")
    convenio = filters.get("convenio_sus")
    if convenio is not None and not isinstance(convenio, bool):
        raise ValueError("convenio_sus deve ser booleano")
    validate_bed_range(filters.get("min_leitos"), filters.get("max_leitos"))
