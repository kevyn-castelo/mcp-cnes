"""Portas dirigidas e condutoras da aplicação."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from mcp_cnes.domain.models import HospitalInfo, ImportBatch, LoadSummary


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


class CNESImporter(Protocol):
    """Importa uma fonte para um lote canônico ainda não persistido."""

    def import_file(self, filepath: Path) -> ImportBatch: ...


class CNESCollector(Protocol):
    """Coleta estabelecimentos de uma fonte externa."""

    def collect(
        self, municipality: str, min_beds: int | None = None, max_beds: int | None = None
    ) -> Sequence[HospitalInfo]: ...
