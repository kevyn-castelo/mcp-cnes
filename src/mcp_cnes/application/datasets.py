"""Casos de uso para normalização e exportação local."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
            raise ValueError(
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
            derived = (
                ("CONVENIO_SUS",) if normalized_origin == "portal_sus" else ()
            )
            return NormalizeResult(
                output, records, normalized_origin, missing, derived
            )
        finally:
            batch.close()


class ExportData:
    def __init__(
        self, repository: CNESCatalogRepository, exporter: DatasetExporter
    ) -> None:
        self._repository = repository
        self._exporter = exporter

    def execute(
        self,
        format: str,
        filters: Mapping[str, Any],
        destination: Path | None = None,
        batch_id: str | None = None,
    ) -> DatasetFileResult:
        _validate_filters(filters)
        search = AdvancedSearch(self._repository)
        def hospitals():
            offset = 0
            while True:
                page = search.execute(filters, "cnes", offset, 500, batch_id)
                yield from page.items
                offset += len(page.items)
                if offset >= page.total_available or not page.items:
                    break
        output, records = self._exporter.export(
            hospitals(), format, destination, f"cnes-export-{batch_id or 'ativo'}"
        )
        return DatasetFileResult(output, records)
