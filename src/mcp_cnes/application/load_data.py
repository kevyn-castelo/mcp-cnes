"""Caso de uso de importação atômica."""

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from mcp_cnes.domain.identity import contextual_batch_digest
from mcp_cnes.domain.models import LoadSummary

from .ports import CNESImporter, CNESRepository


class LoadData:
    """Valida um arquivo antes de substituir todo o repositório."""

    def __init__(self, repository: CNESRepository, importer: CNESImporter) -> None:
        self._repository = repository
        self._importer = importer

    def execute(
        self,
        filepath: Path,
        *,
        source: str = "arquivo_local",
        competence: str | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> LoadSummary:
        batch = self._importer.import_file(filepath)
        try:
            content_digest = batch.content_sha256
            batch_id = (
                contextual_batch_digest(
                    content_digest,
                    source=source,
                    competence=competence,
                    filters=filters or {},
                )
                if content_digest is not None
                else None
            )
            replace_with_metadata = getattr(
                self._repository, "replace_all_with_metadata", None
            )
            if callable(replace_with_metadata):
                batch_id = replace_with_metadata(
                    batch.hospitals,
                    batch.source_file,
                    summary=batch.summary,
                    batch_id=batch_id,
                    source=source,
                    competence=competence,
                    filters=filters or {},
                )
            else:
                batch_id = self._repository.replace_all(
                    batch.hospitals,
                    batch.source_file,
                    summary=batch.summary,
                    batch_id=batch_id,
                )
            return replace(batch.summary, batch_id=batch_id)
        finally:
            batch.close()
