"""Adapter de CSV para o modelo canonico CNES."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from mcp_cnes.domain.errors import CNESDataLoadError, DomainValidationError
from mcp_cnes.domain.identity import canonical_hospital_digest
from mcp_cnes.domain.models import (
    HospitalInfo,
    ImportBatch,
    LoadSummary,
    RejectionReason,
)
from mcp_cnes.domain.rules import normalize_column_name, parse_bool, parse_non_negative_int

from .staging import DiskHospitalSequence

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
    """Le, valida e consolida CSV usando staging temporario em disco."""

    def import_file(self, filepath: Path) -> ImportBatch:
        staged = DiskHospitalSequence()
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
                    serialized = json.dumps(
                        signature, ensure_ascii=False, separators=(",", ":")
                    ).encode("utf-8")
                    if not staged.accept_signature(hashlib.sha256(serialized).digest()):
                        rows_ignored += 1
                        continue
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
                    staged.merge(hospital, rows_read)

            staged.seal()
            summary = LoadSummary(
                len(staged),
                rows_read,
                rows_rejected,
                rows_ignored,
                rejection_reasons=tuple(
                    RejectionReason(code, count)
                    for code, count in sorted(rejection_reasons.items())
                ),
            )
            content_sha256 = canonical_hospital_digest(
                staged.iter_canonical(), presorted=True
            )
            return ImportBatch(staged, summary, str(filepath), content_sha256)
        except CNESDataLoadError:
            staged.close()
            raise
        except (OSError, UnicodeError, csv.Error, sqlite3.Error) as exc:
            staged.close()
            raise CNESDataLoadError(f"Não foi possível carregar o CSV: {exc}") from exc
        except BaseException:
            staged.close()
            raise

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
