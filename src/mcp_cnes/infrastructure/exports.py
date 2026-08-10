"""Exportação atômica de datasets canônicos para arquivos locais."""

from __future__ import annotations

import csv
import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from openpyxl import Workbook

from mcp_cnes.domain.models import HospitalInfo

EXPORT_COLUMNS = {
    "COMPETENCIA": "competencia",
    "UF": "uf",
    "MUNICIPIO": "municipio",
    "CNES": "cnes",
    "NOME_FANTASIA": "nome_fantasia",
    "TIPO_ESTABELECIMENTO": "tipo_estabelecimento",
    "NATUREZA_JURIDICA": "natureza_juridica",
    "GESTAO": "gestao",
    "CONVENIO_SUS": "convenio_sus",
    "LEITOS_EXISTENTES": "leitos_existentes",
    "LEITOS_SUS": "leitos_sus",
}


class LocalDatasetExporter:
    def __init__(self, output_dir: Path) -> None:
        self._root = output_dir.resolve(strict=False)

    def export(
        self,
        hospitals: Iterable[HospitalInfo],
        format: str,
        destination: Path | None,
        basename: str,
    ) -> tuple[Path, int]:
        normalized_format = format.casefold()
        if normalized_format not in {"csv", "json", "xlsx"}:
            raise ValueError("formato deve ser csv, json ou xlsx")
        directory = self._resolve_destination(destination)
        directory.mkdir(parents=True, exist_ok=True)
        output = self._available_output(directory, basename, normalized_format)
        temporary: Path | None = None
        records = 0
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=f".{basename}.", suffix=f".{normalized_format}", dir=directory
            )
            os.close(descriptor)
            temporary = Path(name)
            if normalized_format == "csv":
                with temporary.open("w", encoding="utf-8", newline="") as handle:
                    fieldnames: list[str] = list(EXPORT_COLUMNS)
                    writer = csv.DictWriter(handle, fieldnames=fieldnames)
                    writer.writeheader()
                    for hospital in hospitals:
                        domain_row = hospital.to_dict()
                        writer.writerow(
                            {
                                canonical: domain_row[attribute]
                                for canonical, attribute in EXPORT_COLUMNS.items()
                            }
                        )
                        records += 1
                    handle.flush()
                    os.fsync(handle.fileno())
            elif normalized_format == "json":
                with temporary.open("w", encoding="utf-8", newline="") as handle:
                    handle.write("[")
                    for hospital in hospitals:
                        if records:
                            handle.write(",")
                        json.dump(
                            hospital.to_dict(),
                            handle,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        records += 1
                    handle.write("]")
                    handle.flush()
                    os.fsync(handle.fileno())
            else:
                workbook = Workbook(write_only=True)
                sheet = workbook.create_sheet("CNES")
                sheet.append(list(EXPORT_COLUMNS))
                for hospital in hospitals:
                    row = hospital.to_dict()
                    sheet.append([row[attribute] for attribute in EXPORT_COLUMNS.values()])
                    records += 1
                workbook.save(temporary)
            temporary.replace(output)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return output, records

    @staticmethod
    def _available_output(directory: Path, basename: str, format: str) -> Path:
        candidate = directory / f"{basename}.{format}"
        suffix = 1
        while candidate.exists():
            candidate = directory / f"{basename}-{suffix}.{format}"
            suffix += 1
        return candidate

    def _resolve_destination(self, destination: Path | None) -> Path:
        candidate = self._root if destination is None else destination
        if not candidate.is_absolute():
            candidate = self._root / candidate
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise ValueError(
                "destino deve permanecer dentro do diretório de exportação configurado"
            ) from exc
        return resolved
