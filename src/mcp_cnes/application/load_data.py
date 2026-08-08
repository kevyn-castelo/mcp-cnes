"""Caso de uso de importação atômica."""

from dataclasses import replace
from pathlib import Path

from mcp_cnes.domain.models import LoadSummary

from .ports import CNESImporter, CNESRepository


class LoadData:
    """Valida um arquivo antes de substituir todo o repositório."""

    def __init__(self, repository: CNESRepository, importer: CNESImporter) -> None:
        self._repository = repository
        self._importer = importer

    def execute(self, filepath: Path) -> LoadSummary:
        batch = self._importer.import_file(filepath)
        try:
            batch_id = self._repository.replace_all(
                batch.hospitals,
                batch.source_file,
                summary=batch.summary,
                batch_id=batch.content_sha256,
            )
            return replace(batch.summary, batch_id=batch_id)
        finally:
            batch.close()
