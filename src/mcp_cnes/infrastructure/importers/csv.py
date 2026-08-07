"""Adapter de CSV para o modelo canônico CNES."""

from __future__ import annotations

import csv
import hashlib
import logging
from collections import Counter
from pathlib import Path
from typing import Any

from mcp_cnes.domain.errors import CNESDataLoadError, DomainValidationError
from mcp_cnes.domain.models import (
    HospitalInfo,
    ImportBatch,
    LoadSummary,
    RejectionReason,
)
from mcp_cnes.domain.rules import normalize_column_name, parse_bool, parse_non_negative_int

logger = logging.getLogger(__name__)

COLUMN_MAP = {
    "CNES": "cnes",
    "NOME_FANTASIA": "nome_fantasia",
    "MUNICIPIO": "municipio",
    "UF": "uf",
    "TIPO_DO_ESTABELECIMENTO": "tipo_estabelecimento",
    "TIPO_ESTABELECIMENTO": "tipo_estabelecimento",
    "NATUREZA_JURIDICA_CATEGORIA": "natureza_juridica",
    "NATUREZA_JURIDICA": "natureza_juridica",
    "GESTAO": "gestao",
    "CONVENIO_SUS": "convenio_sus",
    "LEITOS_EXISTENTES": "leitos_existentes",
    "LEITOS_SUS": "leitos_sus",
    "COMPETENCIA": "competencia",
}


class CsvCNESImporter:
    """Lê, valida, deduplica e consolida um CSV sem alterar persistência."""

    def import_file(self, filepath: Path) -> ImportBatch:
        staged: dict[tuple[str, str], HospitalInfo] = {}
        seen_rows: set[tuple[tuple[str, str | None], ...]] = set()
        rows_read = rows_rejected = rows_ignored = 0
        rejection_reasons: Counter[str] = Counter()

        try:
            with filepath.open("r", encoding="utf-8-sig", newline="") as file:
                reader = csv.DictReader(file)
                if not reader.fieldnames:
                    raise CNESDataLoadError("CSV sem cabeçalho")
                headers = {
                    original: COLUMN_MAP.get(normalize_column_name(original))
                    for original in reader.fieldnames
                }
                if "cnes" not in headers.values():
                    raise CNESDataLoadError("CSV sem coluna CNES")

                for row in reader:
                    rows_read += 1
                    signature = tuple((header, row.get(header)) for header in reader.fieldnames)
                    if signature in seen_rows:
                        rows_ignored += 1
                        continue
                    seen_rows.add(signature)
                    try:
                        hospital = self._to_hospital(row, headers)
                    except DomainValidationError:
                        rows_rejected += 1
                        rejection_reasons["valor_invalido"] += 1
                        logger.warning("Linha %s rejeitada por valor invalido", rows_read)
                        continue
                    if hospital is None:
                        rows_rejected += 1
                        rejection_reasons["cnes_ausente"] += 1
                        continue
                    self._merge(staged, hospital)
        except CNESDataLoadError:
            raise
        except (OSError, UnicodeError, csv.Error) as exc:
            raise CNESDataLoadError(f"Não foi possível carregar o CSV: {exc}") from exc

        hospitals = tuple(staged.values())
        summary = LoadSummary(
            len(hospitals),
            rows_read,
            rows_rejected,
            rows_ignored,
            rejection_reasons=tuple(
                RejectionReason(code, count)
                for code, count in sorted(rejection_reasons.items())
            ),
        )
        try:
            with filepath.open("rb") as source:
                source_sha256 = hashlib.file_digest(source, "sha256").hexdigest()
        except OSError as exc:
            raise CNESDataLoadError("Nao foi possivel calcular a identidade do CSV") from exc
        return ImportBatch(hospitals, summary, str(filepath), source_sha256)

    @staticmethod
    def _to_hospital(
        row: dict[str, str | None], headers: dict[str, str | None]
    ) -> HospitalInfo | None:
        data: dict[str, Any] = {}
        for original, value in row.items():
            attribute = headers.get(original)
            if attribute:
                data[attribute] = value.strip() if value else ""
        cnes = str(data.get("cnes", "")).strip()
        if not cnes:
            return None
        return HospitalInfo(
            cnes=cnes,
            nome_fantasia=str(data.get("nome_fantasia", "")),
            municipio=str(data.get("municipio", "")),
            uf=str(data.get("uf", "")),
            tipo_estabelecimento=str(data.get("tipo_estabelecimento", "")),
            natureza_juridica=str(data.get("natureza_juridica", "")),
            gestao=str(data.get("gestao", "")),
            convenio_sus=parse_bool(data.get("convenio_sus")),
            leitos_existentes=parse_non_negative_int(
                data.get("leitos_existentes"), "LEITOS_EXISTENTES"
            ),
            leitos_sus=parse_non_negative_int(data.get("leitos_sus"), "LEITOS_SUS"),
            competencia=str(data.get("competencia", "")),
        )

    @staticmethod
    def _merge(staged: dict[tuple[str, str], HospitalInfo], hospital: HospitalInfo) -> None:
        key = (hospital.cnes, hospital.competencia)
        existing = staged.get(key)
        if existing is None:
            staged[key] = hospital
            return
        existing.leitos_existentes += hospital.leitos_existentes
        existing.leitos_sus += hospital.leitos_sus
        for attribute in (
            "nome_fantasia",
            "municipio",
            "uf",
            "tipo_estabelecimento",
            "natureza_juridica",
            "gestao",
        ):
            if not getattr(existing, attribute):
                setattr(existing, attribute, getattr(hospital, attribute))
        existing.convenio_sus = existing.convenio_sus or hospital.convenio_sus
