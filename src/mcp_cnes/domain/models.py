"""Modelos canônicos independentes de transporte e persistência."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class HospitalInfo:
    """Projeção consolidada de um estabelecimento por competência."""

    cnes: str
    nome_fantasia: str
    municipio: str
    uf: str
    tipo_estabelecimento: str = ""
    natureza_juridica: str = ""
    gestao: str = ""
    convenio_sus: bool = True
    leitos_existentes: int = 0
    leitos_sus: int = 0
    competencia: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Converte o modelo para o contrato legado da interface MCP."""

        return asdict(self)


@dataclass(frozen=True)
class RejectionReason:
    """Motivo agregado sem reter o conteudo sensivel da linha rejeitada."""

    code: str
    count: int


@dataclass(frozen=True)
class LoadSummary:
    """Contadores de uma importação concluída."""

    records_loaded: int
    rows_read: int
    rows_rejected: int
    rows_ignored: int
    batch_id: str | None = None
    rejection_reasons: tuple[RejectionReason, ...] = ()


@dataclass(frozen=True)
class ImportBatch:
    """Lote validado que pode substituir atomicamente um repositório."""

    hospitals: Sequence[HospitalInfo]
    summary: LoadSummary
    source_file: str
    content_sha256: str | None = None

    def close(self) -> None:
        """Libera recursos temporarios do adapter, quando presentes."""

        close = getattr(self.hospitals, "close", None)
        if callable(close):
            close()
