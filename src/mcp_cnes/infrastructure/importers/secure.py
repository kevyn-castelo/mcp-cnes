"""Politica de confinamento aplicada antes de ler fontes locais."""

from __future__ import annotations

from pathlib import Path

from mcp_cnes.application.ports import CNESImporter
from mcp_cnes.domain.errors import ImportSecurityError
from mcp_cnes.domain.models import ImportBatch


class SecureCsvImporter:
    """Aceita somente CSVs regulares confinados ao diretorio configurado."""

    def __init__(
        self,
        delegate: CNESImporter,
        data_dir: Path,
        max_size_bytes: int,
        allowed_files: tuple[str, ...] = (),
    ) -> None:
        self._delegate = delegate
        self._data_dir = data_dir
        self._max_size_bytes = max_size_bytes
        self._allowed_files = frozenset(allowed_files)

    def import_file(self, filepath: Path) -> ImportBatch:
        safe_path = self._resolve(filepath)
        return self._delegate.import_file(safe_path)

    def _resolve(self, filepath: Path) -> Path:
        base = self._data_dir.resolve(strict=False)
        candidate = filepath if filepath.is_absolute() else base / filepath
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(base)
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            raise ImportSecurityError("Arquivo CSV nao permitido pela politica de importacao") from exc

        if resolved.suffix.casefold() != ".csv":
            raise ImportSecurityError("Apenas arquivos com extensao .csv sao permitidos")
        if not resolved.is_file():
            raise ImportSecurityError("A fonte deve ser um arquivo CSV regular")
        if self._allowed_files and resolved.name not in self._allowed_files:
            raise ImportSecurityError("Arquivo CSV fora da lista permitida")
        try:
            size = resolved.stat().st_size
        except OSError as exc:
            raise ImportSecurityError("Nao foi possivel validar o arquivo CSV") from exc
        if size > self._max_size_bytes:
            raise ImportSecurityError("Arquivo CSV excede o tamanho maximo permitido")
        return resolved
