"""Portas dirigidas e condutoras da aplicação."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from mcp_cnes.domain.models import HospitalInfo, ImportBatch, LoadSummary
from mcp_cnes.domain.remote import RemoteFetchRequest, RemoteFetchResult, SourceResource


class CNESRepository(Protocol):
    """Persistência abstrata das projeções de estabelecimentos."""

    def replace_all(
        self,
        hospitals: Sequence[HospitalInfo],
        source_file: str,
        loaded_at: datetime | None = None,
        *,
        summary: LoadSummary | None = None,
        batch_id: str | None = None,
    ) -> str: ...

    def has_data(self) -> bool: ...

    def search_by_municipality(
        self,
        municipality: str,
        min_beds: int | None,
        max_beds: int | None,
        limit: int | None = None,
    ) -> Sequence[HospitalInfo]: ...

    def count_by_municipality(
        self, municipality: str, min_beds: int | None, max_beds: int | None
    ) -> int: ...

    def search_by_municipality_with_count(
        self,
        municipality: str,
        min_beds: int | None,
        max_beds: int | None,
        limit: int,
    ) -> tuple[Sequence[HospitalInfo], int]: ...

    def search_by_uf(
        self,
        uf: str,
        min_beds: int | None,
        max_beds: int | None,
        limit: int | None = None,
    ) -> Sequence[HospitalInfo]: ...

    def count_by_uf(self, uf: str, min_beds: int | None, max_beds: int | None) -> int: ...

    def search_by_uf_with_count(
        self,
        uf: str,
        min_beds: int | None,
        max_beds: int | None,
        limit: int,
    ) -> tuple[Sequence[HospitalInfo], int]: ...

    def get_by_cnes(self, cnes: str) -> HospitalInfo | None: ...

    def statistics(self) -> dict[str, Any]: ...


class CNESCatalogRepository(CNESRepository, Protocol):
    """Extensão para histórico, qualidade e análises multi-lote."""

    def list_batches(self) -> Sequence[dict[str, Any]]: ...

    def replace_all_with_metadata(
        self,
        hospitals: Sequence[HospitalInfo],
        source_file: str,
        *,
        summary: LoadSummary,
        batch_id: str | None,
        source: str,
        competence: str | None,
        filters: Mapping[str, Any],
    ) -> str: ...

    def update_batch_metadata(
        self,
        batch_id: str,
        source: str,
        competence: str | None,
        filters: Mapping[str, Any],
    ) -> None: ...

    def activate_batch(self, batch_id: str) -> None: ...

    def purge_batch(self, batch_id: str) -> tuple[int, int]: ...

    def validate_dataset(self, batch_id: str | None = None) -> dict[str, Any]: ...

    def aggregate(
        self,
        group_by: str,
        metric: str,
        filters: Mapping[str, Any],
        batch_id: str | None = None,
    ) -> Sequence[dict[str, Any]]: ...

    def timeseries(
        self, key: str, key_type: str, start: str, end: str
    ) -> Sequence[dict[str, Any]]: ...

    def diff_batches(self, batch_a: str, batch_b: str) -> dict[str, Any]: ...

    def advanced_search(
        self,
        filters: Mapping[str, Any],
        order_by: str,
        offset: int,
        limit: int,
        batch_id: str | None = None,
    ) -> tuple[Sequence[HospitalInfo], int]: ...


class CNESImporter(Protocol):
    """Importa uma fonte para um lote canônico ainda não persistido."""

    def import_file(self, filepath: Path) -> ImportBatch: ...


class CNESCollector(Protocol):
    """Coleta estabelecimentos de uma fonte externa."""

    def collect(
        self, municipality: str, min_beds: int | None = None, max_beds: int | None = None
    ) -> Sequence[HospitalInfo]: ...


class CNESRemoteSource(Protocol):
    """Descobre e normaliza recursos oficiais sem expor transporte à aplicação."""

    name: str

    def list_resources(self) -> Sequence[SourceResource]: ...

    def list_competences(self) -> Sequence[str]: ...

    def fetch(
        self, request: RemoteFetchRequest, destination: Path | None = None
    ) -> RemoteFetchResult: ...


class DatasetExporter(Protocol):
    """Serializa registros canônicos em um diretório local controlado."""

    def export(
        self,
        hospitals: Iterable[HospitalInfo],
        format: str,
        destination: Path | None,
        basename: str,
    ) -> tuple[Path, int]: ...
