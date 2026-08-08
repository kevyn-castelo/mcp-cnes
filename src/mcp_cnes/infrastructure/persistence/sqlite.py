"""Persistencia SQLite transacional e versionada para o catalogo CNES."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from mcp_cnes.domain.identity import canonical_hospital_digest
from mcp_cnes.domain.models import HospitalInfo, LoadSummary
from mcp_cnes.domain.rules import normalize_search_text

SCHEMA_VERSION = 2

MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS import_batches (
    id TEXT PRIMARY KEY,
    source_file TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('processing', 'completed')),
    rows_read INTEGER NOT NULL,
    accepted_count INTEGER NOT NULL,
    rejected_count INTEGER NOT NULL,
    ignored_count INTEGER NOT NULL,
    rejection_reasons TEXT NOT NULL,
    imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS staging_establishments (
    batch_id TEXT NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    cnes TEXT NOT NULL,
    nome_fantasia TEXT NOT NULL,
    municipio TEXT NOT NULL,
    municipio_normalizado TEXT NOT NULL,
    uf TEXT NOT NULL,
    tipo_estabelecimento TEXT NOT NULL,
    natureza_juridica TEXT NOT NULL,
    gestao TEXT NOT NULL,
    convenio_sus INTEGER NOT NULL,
    leitos_existentes INTEGER NOT NULL,
    leitos_sus INTEGER NOT NULL,
    competencia TEXT NOT NULL,
    PRIMARY KEY (batch_id, ordinal)
);
CREATE TABLE IF NOT EXISTS establishments (
    cnes TEXT NOT NULL,
    nome_fantasia TEXT NOT NULL,
    municipio TEXT NOT NULL,
    municipio_normalizado TEXT NOT NULL,
    uf TEXT NOT NULL,
    tipo_estabelecimento TEXT NOT NULL,
    natureza_juridica TEXT NOT NULL,
    gestao TEXT NOT NULL,
    convenio_sus INTEGER NOT NULL,
    leitos_existentes INTEGER NOT NULL CHECK (leitos_existentes >= 0),
    leitos_sus INTEGER NOT NULL CHECK (leitos_sus >= 0),
    competencia TEXT NOT NULL,
    batch_id TEXT NOT NULL REFERENCES import_batches(id),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (cnes, competencia)
);
CREATE INDEX IF NOT EXISTS idx_establishments_cnes
    ON establishments(cnes);
CREATE INDEX IF NOT EXISTS idx_establishments_uf_beds
    ON establishments(uf, leitos_existentes);
CREATE INDEX IF NOT EXISTS idx_establishments_municipality_beds
    ON establishments(municipio_normalizado, leitos_existentes);
CREATE INDEX IF NOT EXISTS idx_establishments_competence
    ON establishments(competencia);
"""

MIGRATION_2 = """
CREATE VIRTUAL TABLE IF NOT EXISTS establishments_municipality_fts USING fts5(
    municipio_normalizado,
    content='establishments',
    content_rowid='rowid',
    tokenize='trigram'
);
INSERT INTO establishments_municipality_fts(establishments_municipality_fts)
VALUES('rebuild');
"""

MIGRATIONS = {1: MIGRATION_1, 2: MIGRATION_2}

HOSPITAL_COLUMNS = """
    cnes, nome_fantasia, municipio, uf, tipo_estabelecimento,
    natureza_juridica, gestao, convenio_sus, leitos_existentes,
    leitos_sus, competencia
"""

QUALIFIED_HOSPITAL_COLUMNS = """
    e.cnes, e.nome_fantasia, e.municipio, e.uf, e.tipo_estabelecimento,
    e.natureza_juridica, e.gestao, e.convenio_sus, e.leitos_existentes,
    e.leitos_sus, e.competencia
"""


class SQLiteCNESRepository:
    """Repositorio lazy: o arquivo so e criado no primeiro uso de runtime."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._schema_lock = Lock()
        self._schema_ready = False

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        if not self._schema_ready:
            self._ensure_schema(connection)
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        with self._schema_lock:
            if self._schema_ready:
                return
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise RuntimeError("Banco SQLite usa uma versao de schema nao suportada")
            for target_version in range(version + 1, SCHEMA_VERSION + 1):
                connection.executescript(MIGRATIONS[target_version])
                applied_at = datetime.now(UTC).isoformat()
                connection.execute(
                    "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (target_version, applied_at),
                )
                connection.execute(f"PRAGMA user_version = {target_version}")
            self._schema_ready = True

    def replace_all(
        self,
        hospitals: Sequence[HospitalInfo],
        source_file: str,
        loaded_at: datetime | None = None,
        *,
        summary: LoadSummary | None = None,
        batch_id: str | None = None,
    ) -> str:
        effective_summary = summary or LoadSummary(len(hospitals), len(hospitals), 0, 0)
        effective_batch_id = batch_id or canonical_hospital_digest(hospitals)
        timestamp = (loaded_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        reasons = json.dumps(
            [asdict(reason) for reason in effective_summary.rejection_reasons],
            sort_keys=True,
            separators=(",", ":"),
        )

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT status FROM import_batches WHERE id = ?", (effective_batch_id,)
                ).fetchone()
                if existing is not None and existing["status"] == "completed":
                    connection.commit()
                    return effective_batch_id

                connection.execute(
                    """
                    INSERT INTO import_batches(
                        id, source_file, status, rows_read, accepted_count,
                        rejected_count, ignored_count, rejection_reasons, imported_at
                    ) VALUES (?, ?, 'processing', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        effective_batch_id,
                        Path(source_file).name,
                        effective_summary.rows_read,
                        effective_summary.records_loaded,
                        effective_summary.rows_rejected,
                        effective_summary.rows_ignored,
                        reasons,
                        timestamp,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO staging_establishments(
                        batch_id, ordinal, cnes, nome_fantasia, municipio,
                        municipio_normalizado, uf, tipo_estabelecimento,
                        natureza_juridica, gestao, convenio_sus, leitos_existentes,
                        leitos_sus, competencia
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._staging_rows(effective_batch_id, hospitals),
                )
                self._replace_projection(connection, effective_batch_id, timestamp)
                connection.execute(
                    "UPDATE import_batches SET status = 'completed' WHERE id = ?",
                    (effective_batch_id,),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return effective_batch_id

    @staticmethod
    def _staging_rows(
        batch_id: str, hospitals: Sequence[HospitalInfo]
    ) -> Iterable[tuple[Any, ...]]:
        for ordinal, hospital in enumerate(hospitals):
            yield (
                batch_id,
                ordinal,
                hospital.cnes,
                hospital.nome_fantasia,
                hospital.municipio,
                normalize_search_text(hospital.municipio),
                hospital.uf.upper(),
                hospital.tipo_estabelecimento,
                hospital.natureza_juridica,
                hospital.gestao,
                int(hospital.convenio_sus),
                hospital.leitos_existentes,
                hospital.leitos_sus,
                hospital.competencia,
            )

    @staticmethod
    def _replace_projection(
        connection: sqlite3.Connection, batch_id: str, timestamp: str
    ) -> None:
        connection.execute("DELETE FROM establishments")
        connection.execute(
            """
            INSERT INTO establishments(
                cnes, nome_fantasia, municipio, municipio_normalizado, uf,
                tipo_estabelecimento, natureza_juridica, gestao, convenio_sus,
                leitos_existentes, leitos_sus, competencia, batch_id, updated_at
            )
            SELECT
                cnes, nome_fantasia, municipio, municipio_normalizado, uf,
                tipo_estabelecimento, natureza_juridica, gestao, convenio_sus,
                leitos_existentes, leitos_sus, competencia, ?, ?
            FROM staging_establishments
            WHERE batch_id = ?
            """,
            (batch_id, timestamp, batch_id),
        )
        connection.execute(
            "INSERT INTO establishments_municipality_fts"
            "(establishments_municipality_fts) VALUES('rebuild')"
        )

    def has_data(self) -> bool:
        with self._connection() as connection:
            row = connection.execute("SELECT EXISTS(SELECT 1 FROM establishments)").fetchone()
        return bool(row[0])

    @staticmethod
    def _bed_filters(
        min_beds: int | None, max_beds: int | None, *, prefix: str = ""
    ) -> tuple[list[str], list[int]]:
        clauses: list[str] = []
        parameters: list[int] = []
        if min_beds is not None:
            clauses.append(f"{prefix}leitos_existentes >= ?")
            parameters.append(min_beds)
        if max_beds is not None:
            clauses.append(f"{prefix}leitos_existentes <= ?")
            parameters.append(max_beds)
        return clauses, parameters

    def _municipality_query(
        self,
        municipality: str,
        min_beds: int | None,
        max_beds: int | None,
        limit: int | None,
        *,
        count: bool = False,
    ) -> tuple[str, list[Any]]:
        normalized = normalize_search_text(municipality)
        if len(normalized) >= 3:
            clauses, beds = self._bed_filters(min_beds, max_beds, prefix="e.")
            conditions = ["establishments_municipality_fts MATCH ?", *clauses]
            selection = "COUNT(*)" if count else QUALIFIED_HOSPITAL_COLUMNS
            sql = (
                f"SELECT {selection} FROM establishments_municipality_fts "
                "JOIN establishments AS e "
                "ON e.rowid = establishments_municipality_fts.rowid WHERE "
                + " AND ".join(conditions)
            )
            phrase = f'"{normalized.replace(chr(34), chr(34) * 2)}"'
            params: list[Any] = [phrase, *beds]
        else:
            clauses, beds = self._bed_filters(min_beds, max_beds)
            conditions = ["instr(municipio_normalizado, ?) > 0", *clauses]
            selection = "COUNT(*)" if count else HOSPITAL_COLUMNS
            sql = f"SELECT {selection} FROM establishments WHERE " + " AND ".join(
                conditions
            )
            params = [normalized, *beds]
        if not count:
            order_prefix = "e." if len(normalized) >= 3 else ""
            sql += f" ORDER BY {order_prefix}municipio_normalizado, {order_prefix}cnes"
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
        return sql, params

    def search_by_municipality(
        self,
        municipality: str,
        min_beds: int | None,
        max_beds: int | None,
        limit: int | None = None,
    ) -> list[HospitalInfo]:
        sql, params = self._municipality_query(municipality, min_beds, max_beds, limit)
        return self._fetch_hospitals(sql, params)

    def count_by_municipality(
        self, municipality: str, min_beds: int | None, max_beds: int | None
    ) -> int:
        sql, params = self._municipality_query(
            municipality, min_beds, max_beds, None, count=True
        )
        return self._scalar_count(sql, params)

    def _uf_query(
        self,
        uf: str,
        min_beds: int | None,
        max_beds: int | None,
        limit: int | None,
        *,
        count: bool = False,
    ) -> tuple[str, list[Any]]:
        clauses, beds = self._bed_filters(min_beds, max_beds)
        conditions = ["uf = ?", *clauses]
        selection = "COUNT(*)" if count else HOSPITAL_COLUMNS
        sql = (
            f"SELECT {selection} FROM establishments "
            "INDEXED BY idx_establishments_uf_beds WHERE " + " AND ".join(conditions)
        )
        params: list[Any] = [uf.upper(), *beds]
        if not count:
            sql += " ORDER BY leitos_existentes, cnes"
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
        return sql, params

    def search_by_uf(
        self,
        uf: str,
        min_beds: int | None,
        max_beds: int | None,
        limit: int | None = None,
    ) -> list[HospitalInfo]:
        sql, params = self._uf_query(uf, min_beds, max_beds, limit)
        return self._fetch_hospitals(sql, params)

    def count_by_uf(self, uf: str, min_beds: int | None, max_beds: int | None) -> int:
        sql, params = self._uf_query(uf, min_beds, max_beds, None, count=True)
        return self._scalar_count(sql, params)

    def get_by_cnes(self, cnes: str) -> HospitalInfo | None:
        sql = (
            f"SELECT {HOSPITAL_COLUMNS} FROM establishments "
            "INDEXED BY idx_establishments_cnes WHERE cnes = ? "
            "ORDER BY competencia DESC LIMIT 1"
        )
        rows = self._fetch_hospitals(sql, [cnes])
        return rows[0] if rows else None

    def _fetch_hospitals(self, sql: str, params: Sequence[Any]) -> list[HospitalInfo]:
        with self._connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._to_hospital(row) for row in rows]

    def _scalar_count(self, sql: str, params: Sequence[Any]) -> int:
        with self._connection() as connection:
            return int(connection.execute(sql, params).fetchone()[0])

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

    def statistics(self) -> dict[str, Any]:
        with self._connection() as connection:
            totals = connection.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(leitos_existentes), 0),
                       COALESCE(SUM(leitos_sus), 0)
                FROM establishments
                """
            ).fetchone()
            by_uf = connection.execute(
                "SELECT uf, COUNT(*) AS total FROM establishments GROUP BY uf ORDER BY uf"
            ).fetchall()
            metadata = connection.execute(
                """
                SELECT b.imported_at, b.source_file
                FROM import_batches b
                JOIN establishments e ON e.batch_id = b.id
                LIMIT 1
                """
            ).fetchone()
        if not totals[0]:
            return {"error": "Nenhum dado carregado"}
        return {
            "total_estabelecimentos": totals[0],
            "total_leitos_existentes": totals[1],
            "total_leitos_sus": totals[2],
            "estabelecimentos_por_uf": {row["uf"]: row["total"] for row in by_uf},
            "ultima_atualizacao": metadata["imported_at"] if metadata else None,
            "arquivo_fonte": metadata["source_file"] if metadata else None,
        }

    def explain_search_plans(self) -> dict[str, tuple[str, ...]]:
        municipality_sql, municipality_params = self._municipality_query(
            "Mana", 1, 500, 10
        )
        uf_sql, uf_params = self._uf_query("AM", 1, 500, 10)
        cnes_sql = (
            f"SELECT {HOSPITAL_COLUMNS} FROM establishments "
            "INDEXED BY idx_establishments_cnes WHERE cnes = ? "
            "ORDER BY competencia DESC LIMIT 1"
        )
        queries = {
            "municipality": (municipality_sql, municipality_params),
            "uf": (uf_sql, uf_params),
            "cnes": (cnes_sql, ["1234567"]),
        }
        plans: dict[str, tuple[str, ...]] = {}
        with self._connection() as connection:
            for name, (sql, params) in queries.items():
                rows = connection.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
                plans[name] = tuple(str(row[3]) for row in rows)
        return plans
