"""Caso de uso de importação atômica."""

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
        self._repository.replace_all(batch.hospitals, batch.source_file)
        return batch.summary
