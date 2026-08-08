"""Colecao temporaria em disco para importacoes CNES de grande volume."""

from __future__ import annotations

import sqlite3
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import overload

from mcp_cnes.domain.models import HospitalInfo

STAGING_SCHEMA = """
CREATE TABLE seen_rows (
    signature BLOB PRIMARY KEY
) WITHOUT ROWID;
CREATE TABLE hospitals (
    ordinal INTEGER NOT NULL,
    cnes TEXT NOT NULL,
    nome_fantasia TEXT NOT NULL,
    municipio TEXT NOT NULL,
    uf TEXT NOT NULL,
    tipo_estabelecimento TEXT NOT NULL,
    natureza_juridica TEXT NOT NULL,
    gestao TEXT NOT NULL,
    convenio_sus INTEGER NOT NULL,
    leitos_existentes INTEGER NOT NULL,
    leitos_sus INTEGER NOT NULL,
    competencia TEXT NOT NULL,
    PRIMARY KEY (cnes, competencia)
);
CREATE INDEX idx_staged_hospitals_ordinal ON hospitals(ordinal);
"""

HOSPITAL_SELECT = """
    cnes, nome_fantasia, municipio, uf, tipo_estabelecimento,
    natureza_juridica, gestao, convenio_sus, leitos_existentes,
    leitos_sus, competencia
"""


class DiskHospitalSequence(Sequence[HospitalInfo]):
    """Sequence reiteravel apoiada por SQLite e removida explicitamente."""

    def __init__(self) -> None:
        temporary = tempfile.NamedTemporaryFile(
            prefix="mcp-cnes-import-", suffix=".sqlite3", delete=False
        )
        temporary.close()
        self.path = Path(temporary.name)
        self._writer: sqlite3.Connection | None = None
        self._closed = False
        self._writer = sqlite3.connect(self.path)
        self._writer.execute("PRAGMA journal_mode = OFF")
        self._writer.execute("PRAGMA synchronous = OFF")
        self._writer.executescript(STAGING_SCHEMA)
        self._writer.execute("BEGIN")
        self._length = 0

    def accept_signature(self, signature: bytes) -> bool:
        writer = self._require_writer()
        cursor = writer.execute(
            "INSERT OR IGNORE INTO seen_rows(signature) VALUES (?)", (signature,)
        )
        return cursor.rowcount == 1

    def merge(self, hospital: HospitalInfo, ordinal: int) -> None:
        writer = self._require_writer()
        writer.execute(
            """
            INSERT INTO hospitals(
                ordinal, cnes, nome_fantasia, municipio, uf, tipo_estabelecimento,
                natureza_juridica, gestao, convenio_sus, leitos_existentes,
                leitos_sus, competencia
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cnes, competencia) DO UPDATE SET
                nome_fantasia = CASE WHEN hospitals.nome_fantasia = ''
                    THEN excluded.nome_fantasia ELSE hospitals.nome_fantasia END,
                municipio = CASE WHEN hospitals.municipio = ''
                    THEN excluded.municipio ELSE hospitals.municipio END,
                uf = CASE WHEN hospitals.uf = '' THEN excluded.uf ELSE hospitals.uf END,
                tipo_estabelecimento = CASE WHEN hospitals.tipo_estabelecimento = ''
                    THEN excluded.tipo_estabelecimento ELSE hospitals.tipo_estabelecimento END,
                natureza_juridica = CASE WHEN hospitals.natureza_juridica = ''
                    THEN excluded.natureza_juridica ELSE hospitals.natureza_juridica END,
                gestao = CASE WHEN hospitals.gestao = ''
                    THEN excluded.gestao ELSE hospitals.gestao END,
                convenio_sus = MAX(hospitals.convenio_sus, excluded.convenio_sus),
                leitos_existentes = hospitals.leitos_existentes + excluded.leitos_existentes,
                leitos_sus = hospitals.leitos_sus + excluded.leitos_sus
            """,
            (
                ordinal,
                hospital.cnes,
                hospital.nome_fantasia,
                hospital.municipio,
                hospital.uf,
                hospital.tipo_estabelecimento,
                hospital.natureza_juridica,
                hospital.gestao,
                int(hospital.convenio_sus),
                hospital.leitos_existentes,
                hospital.leitos_sus,
                hospital.competencia,
            ),
        )

    def seal(self) -> None:
        writer = self._require_writer()
        self._length = int(writer.execute("SELECT COUNT(*) FROM hospitals").fetchone()[0])
        writer.commit()
        writer.close()
        self._writer = None

    def iter_canonical(self) -> Iterator[HospitalInfo]:
        return self._iter_rows("cnes, competencia")

    def __iter__(self) -> Iterator[HospitalInfo]:
        return self._iter_rows("ordinal")

    def __len__(self) -> int:
        return self._length

    @overload
    def __getitem__(self, index: int) -> HospitalInfo: ...

    @overload
    def __getitem__(self, index: slice) -> list[HospitalInfo]: ...

    def __getitem__(self, index: int | slice) -> HospitalInfo | list[HospitalInfo]:
        if isinstance(index, slice):
            return [self[position] for position in range(*index.indices(len(self)))]
        position = index + len(self) if index < 0 else index
        if position < 0 or position >= len(self):
            raise IndexError(index)
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                f"SELECT {HOSPITAL_SELECT} FROM hospitals "
                "ORDER BY ordinal LIMIT 1 OFFSET ?",
                (position,),
            ).fetchone()
        if row is None:
            raise IndexError(index)
        return self._to_hospital(row)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._writer is not None:
            self._writer.rollback()
            self._writer.close()
            self._writer = None
        self.path.unlink(missing_ok=True)

    def _iter_rows(self, order_by: str) -> Iterator[HospitalInfo]:
        if self._closed:
            raise RuntimeError("Staging temporario ja foi removido")
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                f"SELECT {HOSPITAL_SELECT} FROM hospitals ORDER BY {order_by}"
            )
            for row in rows:
                yield self._to_hospital(row)
        finally:
            connection.close()

    def _require_writer(self) -> sqlite3.Connection:
        if self._writer is None:
            raise RuntimeError("Staging temporario ja foi finalizado")
        return self._writer

    @staticmethod
    def _to_hospital(row: sqlite3.Row) -> HospitalInfo:
        return HospitalInfo(
            cnes=row["cnes"],
            nome_fantasia=row["nome_fantasia"],
            municipio=row["municipio"],
            uf=row["uf"],
            tipo_estabelecimento=row["tipo_estabelecimento"],
            natureza_juridica=row["natureza_juridica"],
            gestao=row["gestao"],
            convenio_sus=bool(row["convenio_sus"]),
            leitos_existentes=row["leitos_existentes"],
            leitos_sus=row["leitos_sus"],
            competencia=row["competencia"],
        )

    def __del__(self) -> None:
        try:
            self.close()
        except OSError:
            pass
