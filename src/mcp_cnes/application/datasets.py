"""Casos de uso para normalização e exportação local."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from mcp_cnes.domain.errors import DomainValidationError
from mcp_cnes.domain.models import HospitalInfo

from .analytics import AdvancedSearch, _validate_filters
from .ports import CNESCatalogRepository, CNESImporter, DatasetExporter


@dataclass(frozen=True)
class DatasetFileResult:
    filepath: Path
    records: int


@dataclass(frozen=True)
class NormalizeResult(DatasetFileResult):
    origin: str
    missing_fields: tuple[str, ...]
    derived_fields: tuple[str, ...]


class NormalizeData:
    SUPPORTED_ORIGINS = {"auto", "csv_canonico", "portal_sus"}

    def __init__(self, importer: CNESImporter, exporter: DatasetExporter) -> None:
        self._importer = importer
        self._exporter = exporter

    def execute(
        self, filepath: Path, origin: str, destination: Path | None = None
    ) -> NormalizeResult:
        normalized_origin = origin.casefold()
        if normalized_origin not in self.SUPPORTED_ORIGINS:
            raise DomainValidationError(
                "origem suportada: auto, csv_canonico ou portal_sus já extraído em CSV"
            )
        batch = self._importer.import_file(filepath)
        try:
            seen = False
            non_empty_fields: set[str] = set()
            text_fields = {
                "NOME_FANTASIA": "nome_fantasia",
                "MUNICIPIO": "municipio",
                "UF": "uf",
                "TIPO_ESTABELECIMENTO": "tipo_estabelecimento",
                "NATUREZA_JURIDICA": "natureza_juridica",
                "GESTAO": "gestao",
                "COMPETENCIA": "competencia",
            }

            def tracked_hospitals():
                nonlocal seen
                for hospital in batch.hospitals:
                    seen = True
                    non_empty_fields.update(
                        attribute
                        for attribute in text_fields.values()
                        if getattr(hospital, attribute)
                    )
                    yield hospital

            output, records = self._exporter.export(
                tracked_hospitals(),
                "csv",
                destination,
                f"cnes-normalizado-{(batch.content_sha256 or 'dataset')[:12]}",
            )
            missing = tuple(
                canonical
                for canonical, attribute in text_fields.items()
                if seen and attribute not in non_empty_fields
            )
            derived = ("CONVENIO_SUS",) if normalized_origin == "portal_sus" else ()
            return NormalizeResult(output, records, normalized_origin, missing, derived)
        finally:
            batch.close()


class ExportData:
    def __init__(self, repository: CNESCatalogRepository, exporter: DatasetExporter) -> None:
        self._repository = repository
        self._exporter = exporter

    def execute(
        self,
        format: str,
        filters: Mapping[str, Any],
        destination: Path | None = None,
        batch_id: str | None = None,
        cnes_list: list[str] | None = None,
        limit: int | None = None,
        offset: int = 0,
        order_by: str = "cnes",
        output_profile: str | None = None,
    ) -> DatasetFileResult:
        _validate_filters(filters)
        if isinstance(offset, bool) or offset < 0:
            raise DomainValidationError("offset deve ser um inteiro não negativo")
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500
        ):
            raise DomainValidationError("limit deve estar entre 1 e 500")
        selected_cnes: list[str] | None = None
        if cnes_list is not None:
            selected_cnes = list(dict.fromkeys(cnes_list))
            invalid = [value for value in selected_cnes if len(value) != 7 or not value.isdigit()]
            if invalid:
                raise DomainValidationError(
                    "cnes_list aceita somente códigos CNES de sete dígitos"
                )
            if not selected_cnes:
                raise DomainValidationError("cnes_list não pode ser vazia")
        effective_filters = dict(filters)
        if selected_cnes is not None:
            effective_filters["cnes_list"] = selected_cnes
        search = AdvancedSearch(self._repository)
        if output_profile not in {None, "crm_generico"}:
            raise DomainValidationError("perfil_saida deve ser crm_generico ou nulo")

        batch_metadata = dict(self._repository.get_batch_metadata(batch_id))
        effective_batch_id = str(batch_metadata["lote_id"])

        def hospitals():
            current_offset = offset
            remaining = limit
            while True:
                page_size = 500 if remaining is None else min(500, remaining)
                if output_profile is None:
                    page = search.execute(
                        effective_filters,
                        order_by,
                        current_offset,
                        page_size,
                        effective_batch_id,
                    )
                    items = page.items
                    total_available = page.total_available
                else:
                    advanced_search_v2 = getattr(self._repository, "advanced_search_v2", None)
                    if not callable(advanced_search_v2):
                        raise DomainValidationError(
                            "perfil CRM requer o backend colunar e um lote v2"
                        )
                    search_v2 = cast(
                        Callable[..., tuple[Sequence[HospitalInfo], int]],
                        advanced_search_v2,
                    )
                    items, total_available = search_v2(
                        effective_filters,
                        order_by,
                        current_offset,
                        page_size,
                        effective_batch_id,
                    )
                yield from items
                current_offset += len(items)
                if remaining is not None:
                    remaining -= len(items)
                if current_offset >= total_available or not items or remaining == 0:
                    break

        provenance = {
            "competencia": batch_metadata.get("competencia"),
            "lote_id": batch_metadata["lote_id"],
            "filtros_aplicados": {
                **dict(filters),
                "cnes_list": selected_cnes,
                "order_by": order_by,
                "offset": offset,
                "limit": limit,
            },
            "etag": batch_metadata.get("etag"),
            "extraido_em": datetime.now(UTC).isoformat(),
            "versao_contrato": "v2" if output_profile else "v1",
            "perfil_saida": output_profile,
            "campos_ausentes": [
                name for name in ("competencia", "etag") if batch_metadata.get(name) is None
            ],
        }
        output, records = self._exporter.export(
            hospitals(),
            format,
            destination,
            f"cnes-export-{effective_batch_id}",
            provenance,
            output_profile,
        )
        return DatasetFileResult(output, records)
