"""Repositório em memória usado na migração e nos testes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from mcp_cnes.domain.identity import canonical_hospital_digest
from mcp_cnes.domain.models import HospitalInfo, LoadSummary
from mcp_cnes.domain.rules import is_within_bed_range


@dataclass
class MemoryCNESRepository:
    hospitals: list[HospitalInfo] = field(default_factory=list)
    last_updated: datetime | None = None
    source_file: str | None = None

    def replace_all(
        self,
        hospitals: Sequence[HospitalInfo],
        source_file: str,
        loaded_at: datetime | None = None,
        *,
        summary: LoadSummary | None = None,
        batch_id: str | None = None,
    ) -> str:
        self.hospitals = list(hospitals)
        self.last_updated = loaded_at or datetime.now()
        self.source_file = source_file
        if batch_id is not None:
            return batch_id
        return canonical_hospital_digest(hospitals)

    def has_data(self) -> bool:
        return bool(self.hospitals)

    def search_by_municipality(
        self,
        municipality: str,
        min_beds: int | None,
        max_beds: int | None,
        limit: int | None = None,
    ) -> list[HospitalInfo]:
        query = municipality.casefold()
        matches = [
            hospital
            for hospital in self.hospitals
            if query in hospital.municipio.casefold()
            and is_within_bed_range(hospital.leitos_existentes, min_beds, max_beds)
        ]
        return matches if limit is None else matches[:limit]

    def count_by_municipality(
        self, municipality: str, min_beds: int | None, max_beds: int | None
    ) -> int:
        return len(self.search_by_municipality(municipality, min_beds, max_beds))

    def search_by_municipality_with_count(
        self,
        municipality: str,
        min_beds: int | None,
        max_beds: int | None,
        limit: int,
    ) -> tuple[list[HospitalInfo], int]:
        matches = self.search_by_municipality(municipality, min_beds, max_beds)
        return matches[:limit], len(matches)

    def search_by_uf(
        self,
        uf: str,
        min_beds: int | None,
        max_beds: int | None,
        limit: int | None = None,
    ) -> list[HospitalInfo]:
        query = uf.upper()
        matches = [
            hospital
            for hospital in self.hospitals
            if hospital.uf.upper() == query
            and is_within_bed_range(hospital.leitos_existentes, min_beds, max_beds)
        ]
        return matches if limit is None else matches[:limit]

    def count_by_uf(self, uf: str, min_beds: int | None, max_beds: int | None) -> int:
        return len(self.search_by_uf(uf, min_beds, max_beds))

    def search_by_uf_with_count(
        self,
        uf: str,
        min_beds: int | None,
        max_beds: int | None,
        limit: int,
    ) -> tuple[list[HospitalInfo], int]:
        matches = self.search_by_uf(uf, min_beds, max_beds)
        return matches[:limit], len(matches)

    def get_by_cnes(self, cnes: str) -> HospitalInfo | None:
        return next((hospital for hospital in self.hospitals if hospital.cnes == cnes), None)

    def statistics(self) -> dict[str, Any]:
        if not self.hospitals:
            return {"error": "Nenhum dado carregado"}
        establishments_by_uf: dict[str, int] = {}
        for hospital in self.hospitals:
            establishments_by_uf[hospital.uf] = establishments_by_uf.get(hospital.uf, 0) + 1
        return {
            "total_estabelecimentos": len(self.hospitals),
            "total_leitos_existentes": sum(h.leitos_existentes for h in self.hospitals),
            "total_leitos_sus": sum(h.leitos_sus for h in self.hospitals),
            "estabelecimentos_por_uf": establishments_by_uf,
            "ultima_atualizacao": self.last_updated.isoformat() if self.last_updated else None,
            "arquivo_fonte": self.source_file,
        }

    # Compatibilidade temporária com consumidores do servidor legado.
    def search_by_municipio(
        self, municipio: str, min_beds: int | None = None, max_beds: int | None = None
    ) -> list[HospitalInfo]:
        return self.search_by_municipality(municipio, min_beds, max_beds)

    def search_by_cnes(self, cnes: str) -> HospitalInfo | None:
        return self.get_by_cnes(cnes)

    def get_statistics(self) -> dict[str, Any]:
        return self.statistics()
