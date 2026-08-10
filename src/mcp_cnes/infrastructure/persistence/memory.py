"""Repositório em memória usado na migração e nos testes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from mcp_cnes.domain.identity import canonical_hospital_digest
from mcp_cnes.domain.models import HospitalInfo, LoadSummary
from mcp_cnes.domain.rules import is_within_bed_range, normalize_search_text


@dataclass
class MemoryCNESRepository:
    hospitals: list[HospitalInfo] = field(default_factory=list)
    last_updated: datetime | None = None
    source_file: str | None = None
    batches: dict[str, list[HospitalInfo]] = field(default_factory=dict, init=False)
    batch_metadata: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)
    active_batch_id: str | None = field(default=None, init=False)

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
        effective_id = batch_id or canonical_hospital_digest(hospitals)
        self.batches[effective_id] = list(hospitals)
        self.batch_metadata[effective_id] = {
            "lote_id": effective_id,
            "arquivo_fonte": source_file,
            "fonte": "arquivo_local",
            "competencia": None,
            "filtros": {},
            "etag": None,
            "registros": len(hospitals),
            "importado_em": self.last_updated.astimezone(UTC).isoformat(),
        }
        self.active_batch_id = effective_id
        return effective_id

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
        etag: str | None = None,
    ) -> str:
        effective_id = self.replace_all(
            hospitals, source_file, summary=summary, batch_id=batch_id
        )
        self.update_batch_metadata(effective_id, source, competence, filters, etag)
        return effective_id

    def list_batches(self) -> list[dict[str, Any]]:
        return [
            {
                **{key: value for key, value in metadata.items() if key != "etag"},
                "ativo": batch_id == self.active_batch_id,
            }
            for batch_id, metadata in reversed(self.batch_metadata.items())
        ]

    def update_batch_metadata(
        self,
        batch_id: str,
        source: str,
        competence: str | None,
        filters: Mapping[str, Any],
        etag: str | None = None,
    ) -> None:
        if batch_id not in self.batch_metadata:
            raise ValueError(f"Lote inexistente: {batch_id}")
        self.batch_metadata[batch_id].update(
            fonte=source, competencia=competence, filtros=dict(filters), etag=etag
        )

    def get_batch_metadata(self, batch_id: str | None = None) -> dict[str, Any]:
        selected, hospitals = self._selected(batch_id)
        metadata = self.batch_metadata[selected]
        competences = sorted({item.competencia for item in hospitals if item.competencia})
        return {
            "lote_id": selected,
            "fonte": metadata["fonte"],
            "competencia": metadata["competencia"] or (
                competences[0] if len(competences) == 1 else competences or None
            ),
            "filtros": dict(metadata["filtros"]),
            "etag": metadata.get("etag"),
            "importado_em": metadata["importado_em"],
        }

    def activate_batch(self, batch_id: str) -> None:
        if batch_id not in self.batches:
            raise ValueError(f"Lote inexistente: {batch_id}")
        self.active_batch_id = batch_id
        self.hospitals = list(self.batches[batch_id])
        metadata = self.batch_metadata[batch_id]
        self.source_file = str(metadata["arquivo_fonte"])

    def purge_batch(self, batch_id: str) -> tuple[int, int]:
        if batch_id not in self.batches:
            raise ValueError(f"Lote inexistente: {batch_id}")
        removed = len(self.batches.pop(batch_id))
        self.batch_metadata.pop(batch_id)
        if self.active_batch_id == batch_id:
            replacement = next(reversed(self.batches), None)
            if replacement is None:
                self.active_batch_id = None
                self.hospitals = []
            else:
                self.activate_batch(replacement)
        return removed, 0

    def validate_dataset(self, batch_id: str | None = None) -> dict[str, Any]:
        selected, hospitals = self._selected(batch_id)
        text_fields = (
            "cnes", "nome_fantasia", "municipio", "uf", "tipo_estabelecimento",
            "natureza_juridica", "gestao", "competencia",
        )
        keys = [(item.cnes, item.competencia) for item in hospitals]
        competences = sorted({item.competencia for item in hospitals if item.competencia})
        duplicates = len(keys) - len(set(keys))
        return {
            "lote_id": selected,
            "total_registros": len(hospitals),
            "campos_vazios": {
                field_name: sum(not getattr(item, field_name) for item in hospitals)
                for field_name in text_fields
            },
            "cnes_duplicados": duplicates,
            "competencias": competences,
            "competencias_mistas": len(competences) > 1,
            "leitos_invalidos": 0,
            "valido": bool(hospitals) and duplicates == 0,
        }

    def aggregate(
        self,
        group_by: str,
        metric: str,
        filters: Mapping[str, Any],
        batch_id: str | None = None,
    ) -> list[dict[str, Any]]:
        _, hospitals = self._selected(batch_id)
        filtered = self._filter(hospitals, filters)
        attributes = {
            "uf": "uf", "municipio": "municipio", "tipo": "tipo_estabelecimento",
            "natureza": "natureza_juridica", "gestao": "gestao",
        }
        if group_by not in attributes:
            raise ValueError("group_by não suportado")
        grouped: dict[str, list[HospitalInfo]] = {}
        for item in filtered:
            grouped.setdefault(str(getattr(item, attributes[group_by])), []).append(item)
        def value(items: list[HospitalInfo]) -> float | int:
            if metric == "estabelecimentos":
                return len(items)
            if metric == "media_leitos":
                values = [item.leitos_existentes for item in items]
                return sum(values) / len(values) if values else 0
            if metric not in {"leitos_existentes", "leitos_sus"}:
                raise ValueError("metrica não suportada")
            values = [getattr(item, metric) for item in items]
            return sum(values)
        return [{"grupo": key, "valor": value(items)} for key, items in sorted(grouped.items())]

    def advanced_search(
        self,
        filters: Mapping[str, Any],
        order_by: str,
        offset: int,
        limit: int,
        batch_id: str | None = None,
    ) -> tuple[list[HospitalInfo], int]:
        if batch_id is None and self.active_batch_id is None:
            hospitals = list(self.hospitals)
        else:
            _, hospitals = self._selected(batch_id)
        items = self._filter(hospitals, filters)
        reverse = order_by in {"leitos_existentes", "leitos_sus"}
        items.sort(key=lambda item: getattr(item, order_by), reverse=reverse)
        return items[offset : offset + limit], len(items)

    def timeseries(self, key: str, key_type: str, start: str, end: str) -> list[dict[str, Any]]:
        if key_type not in {"cnes", "municipio"}:
            raise ValueError("tipo_chave deve ser cnes ou municipio")
        latest: dict[tuple[str, str], HospitalInfo] = {}
        for hospitals in self.batches.values():
            for item in hospitals:
                matches = item.cnes == key if key_type == "cnes" else normalize_search_text(item.municipio) == normalize_search_text(key)
                if matches and start <= item.competencia <= end:
                    latest[item.cnes, item.competencia] = item
        result = []
        for competence in sorted({item.competencia for item in latest.values()}):
            items = [item for item in latest.values() if item.competencia == competence]
            result.append({"competencia": competence, "estabelecimentos": len(items), "leitos_existentes": sum(item.leitos_existentes for item in items), "leitos_sus": sum(item.leitos_sus for item in items)})
        return result

    def diff_batches(self, batch_a: str, batch_b: str) -> dict[str, Any]:
        _, left_items = self._selected(batch_a)
        _, right_items = self._selected(batch_b)
        mixed = len({item.competencia for item in left_items}) > 1 or len({item.competencia for item in right_items}) > 1
        def make_key(item: HospitalInfo) -> tuple[str, str]:
            return (item.cnes, item.competencia if mixed else "")

        left = {make_key(item): item for item in left_items}
        right = {make_key(item): item for item in right_items}
        def label(key: tuple[str, str]) -> str:
            return f"{key[0]}@{key[1]}" if mixed else key[0]
        changed = []
        for item_key in sorted(left.keys() & right.keys()):
            a, b = left[item_key], right[item_key]
            if (a.leitos_existentes, a.leitos_sus) != (b.leitos_existentes, b.leitos_sus):
                changed.append({"cnes": item_key[0], "competencia_a": a.competencia if mixed else None, "competencia_b": b.competencia if mixed else None, "leitos_existentes_a": a.leitos_existentes, "leitos_existentes_b": b.leitos_existentes, "leitos_sus_a": a.leitos_sus, "leitos_sus_b": b.leitos_sus})
        return {"lote_a": batch_a, "lote_b": batch_b, "entraram": [label(key) for key in sorted(right.keys() - left.keys())], "sairam": [label(key) for key in sorted(left.keys() - right.keys())], "mudaram_leitos": changed, "avisos": []}

    def _selected(self, batch_id: str | None) -> tuple[str, list[HospitalInfo]]:
        selected = batch_id or self.active_batch_id
        if selected is None or selected not in self.batches:
            raise ValueError(f"Lote inexistente: {selected or ''}")
        return selected, list(self.batches[selected])

    @staticmethod
    def _filter(hospitals: Sequence[HospitalInfo], filters: Mapping[str, Any]) -> list[HospitalInfo]:
        result = []
        for item in hospitals:
            if filters.get("cnes_list") and item.cnes not in filters["cnes_list"]:
                continue
            if filters.get("uf") and item.uf.upper() != str(filters["uf"]).upper():
                continue
            if filters.get("municipio") and normalize_search_text(str(filters["municipio"])) not in normalize_search_text(item.municipio):
                continue
            if filters.get("tipo_estabelecimento") and normalize_search_text(str(filters["tipo_estabelecimento"])) not in normalize_search_text(item.tipo_estabelecimento):
                continue
            if filters.get("natureza_juridica") and normalize_search_text(str(filters["natureza_juridica"])) not in normalize_search_text(item.natureza_juridica):
                continue
            if filters.get("gestao") and normalize_search_text(str(filters["gestao"])) not in normalize_search_text(item.gestao):
                continue
            if filters.get("convenio_sus") is not None and item.convenio_sus != filters["convenio_sus"]:
                continue
            if not is_within_bed_range(item.leitos_existentes, filters.get("min_leitos"), filters.get("max_leitos")):
                continue
            result.append(item)
        return result

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
