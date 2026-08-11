"""Persistencia SQLite transacional e versionada para o catalogo CNES."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from mcp_cnes.domain.identity import canonical_hospital_digest
from mcp_cnes.domain.models import HospitalInfo, LoadSummary
from mcp_cnes.domain.rules import normalize_search_text

SCHEMA_VERSION = 4

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

MIGRATION_3_COLUMNS = {
    "source": "TEXT NOT NULL DEFAULT 'arquivo_local'",
    "competence": "TEXT",
    "filters_json": "TEXT NOT NULL DEFAULT '{}'",
}
MIGRATION_3 = """
CREATE INDEX IF NOT EXISTS idx_import_batches_imported_at
    ON import_batches(imported_at DESC);
"""

MIGRATION_4_COLUMNS = {"etag": "TEXT"}

MIGRATIONS = {1: MIGRATION_1, 2: MIGRATION_2, 3: MIGRATION_3}

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

    def __init__(self, database_path: Path, *, batch_retention_count: int = 5) -> None:
        if batch_retention_count < 1:
            raise ValueError("batch_retention_count deve ser maior que zero")
        self.database_path = database_path
        self.batch_retention_count = batch_retention_count
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
                if target_version == 3:
                    self._apply_migration_3(connection)
                    continue
                if target_version == 4:
                    self._apply_migration_4(connection)
                    continue
                connection.executescript(MIGRATIONS[target_version])
                applied_at = datetime.now(UTC).isoformat()
                connection.execute(
                    "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (target_version, applied_at),
                )
                connection.execute(f"PRAGMA user_version = {target_version}")
            self._schema_ready = True

    @staticmethod
    def _apply_migration_3(connection: sqlite3.Connection) -> None:
        """Aplica e recupera a migração aditiva sob um único write lock."""

        connection.execute("BEGIN IMMEDIATE")
        try:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current >= 3:
                connection.commit()
                return
            if current != 2:
                raise RuntimeError("Migração 3 exige schema SQLite na versão 2")
            existing = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(import_batches)")
            }
            for name, definition in MIGRATION_3_COLUMNS.items():
                if name not in existing:
                    connection.execute(
                        f"ALTER TABLE import_batches ADD COLUMN {name} {definition}"
                    )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_import_batches_imported_at "
                "ON import_batches(imported_at DESC)"
            )
            applied_at = datetime.now(UTC).isoformat()
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (3, ?)",
                (applied_at,),
            )
            connection.execute("PRAGMA user_version = 3")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    @staticmethod
    def _apply_migration_4(connection: sqlite3.Connection) -> None:
        """Adiciona a proveniência HTTP sem invalidar lotes existentes."""

        connection.execute("BEGIN IMMEDIATE")
        try:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current >= 4:
                connection.commit()
                return
            if current != 3:
                raise RuntimeError("Migração 4 exige schema SQLite na versão 3")
            existing = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(import_batches)")
            }
            for name, definition in MIGRATION_4_COLUMNS.items():
                if name not in existing:
                    connection.execute(
                        f"ALTER TABLE import_batches ADD COLUMN {name} {definition}"
                    )
            applied_at = datetime.now(UTC).isoformat()
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (4, ?)",
                (applied_at,),
            )
            connection.execute("PRAGMA user_version = 4")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    def replace_all(
        self,
        hospitals: Sequence[HospitalInfo],
        source_file: str,
        loaded_at: datetime | None = None,
        *,
        summary: LoadSummary | None = None,
        batch_id: str | None = None,
        source: str = "arquivo_local",
        competence: str | None = None,
        filters: Mapping[str, Any] | None = None,
        etag: str | None = None,
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
                    active = connection.execute(
                        "SELECT EXISTS(SELECT 1 FROM establishments WHERE batch_id = ?)",
                        (effective_batch_id,),
                    ).fetchone()[0]
                    if active:
                        self._update_batch_metadata_row(
                            connection,
                            effective_batch_id,
                            source,
                            competence,
                            filters or {},
                            etag,
                        )
                        self._purge_old_batches(connection, effective_batch_id)
                        connection.commit()
                        return effective_batch_id
                    self._replace_projection(connection, effective_batch_id, timestamp)
                    self._update_batch_metadata_row(
                        connection,
                        effective_batch_id,
                        source,
                        competence,
                        filters or {},
                        etag,
                    )
                    self._purge_old_batches(connection, effective_batch_id)
                    connection.commit()
                    return effective_batch_id

                connection.execute(
                    """
                    INSERT INTO import_batches(
                        id, source_file, status, rows_read, accepted_count,
                        rejected_count, ignored_count, rejection_reasons, imported_at,
                        source, competence, filters_json, etag
                    ) VALUES (?, ?, 'processing', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        source,
                        competence,
                        json.dumps(filters or {}, ensure_ascii=False, sort_keys=True),
                        etag,
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
                self._purge_old_batches(connection, effective_batch_id)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return effective_batch_id

    def replace_all_with_metadata(
        self,
        hospitals: Sequence[HospitalInfo],
        source_file: str,
        *,
        summary: LoadSummary,
        batch_id: str | None,
        source: str,
        competence: str | None,
        filters: Mapping[str, Any],
        etag: str | None = None,
    ) -> str:
        return self.replace_all(
            hospitals,
            source_file,
            summary=summary,
            batch_id=batch_id,
            source=source,
            competence=competence,
            filters=filters,
            etag=etag,
        )

    def _purge_old_batches(
        self, connection: sqlite3.Connection, current_batch_id: str
    ) -> None:
        older_to_keep = self.batch_retention_count - 1
        connection.execute(
            """
            DELETE FROM import_batches
            WHERE id IN (
                SELECT id FROM import_batches
                WHERE status = 'completed' AND id <> ?
                ORDER BY imported_at DESC, id DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (current_batch_id, older_to_keep),
        )

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
        include_total: bool = False,
    ) -> tuple[str, list[Any]]:
        normalized = normalize_search_text(municipality)
        if len(normalized) >= 3:
            clauses, beds = self._bed_filters(min_beds, max_beds, prefix="e.")
            conditions = ["establishments_municipality_fts MATCH ?", *clauses]
            selection = self._query_selection(
                QUALIFIED_HOSPITAL_COLUMNS, count, include_total
            )
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
            selection = self._query_selection(HOSPITAL_COLUMNS, count, include_total)
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

    @staticmethod
    def _query_selection(columns: str, count: bool, include_total: bool) -> str:
        if count:
            return "COUNT(*)"
        if include_total:
            return f"{columns}, COUNT(*) OVER() AS total_available"
        return columns

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

    def search_by_municipality_with_count(
        self,
        municipality: str,
        min_beds: int | None,
        max_beds: int | None,
        limit: int,
    ) -> tuple[list[HospitalInfo], int]:
        sql, params = self._municipality_query(
            municipality, min_beds, max_beds, limit, include_total=True
        )
        return self._fetch_hospitals_with_count(sql, params)

    def _uf_query(
        self,
        uf: str,
        min_beds: int | None,
        max_beds: int | None,
        limit: int | None,
        *,
        count: bool = False,
        include_total: bool = False,
    ) -> tuple[str, list[Any]]:
        clauses, beds = self._bed_filters(min_beds, max_beds)
        conditions = ["uf = ?", *clauses]
        selection = self._query_selection(HOSPITAL_COLUMNS, count, include_total)
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

    def search_by_uf_with_count(
        self,
        uf: str,
        min_beds: int | None,
        max_beds: int | None,
        limit: int,
    ) -> tuple[list[HospitalInfo], int]:
        sql, params = self._uf_query(
            uf, min_beds, max_beds, limit, include_total=True
        )
        return self._fetch_hospitals_with_count(sql, params)

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

    def _fetch_hospitals_with_count(
        self, sql: str, params: Sequence[Any]
    ) -> tuple[list[HospitalInfo], int]:
        with self._connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        total = int(rows[0]["total_available"]) if rows else 0
        return [self._to_hospital(row) for row in rows], total

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

    def _active_batch_id(self, connection: sqlite3.Connection) -> str | None:
        row = connection.execute(
            "SELECT batch_id FROM establishments LIMIT 1"
        ).fetchone()
        return str(row[0]) if row else None

    def _require_batch(
        self, connection: sqlite3.Connection, batch_id: str | None
    ) -> str:
        selected = batch_id or self._active_batch_id(connection)
        if selected is None:
            raise ValueError("Nenhum lote ativo")
        exists = connection.execute(
            "SELECT 1 FROM import_batches WHERE id = ? AND status = 'completed'",
            (selected,),
        ).fetchone()
        if exists is None:
            raise ValueError(f"Lote inexistente: {selected}")
        return selected

    def list_batches(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            active = self._active_batch_id(connection)
            rows = connection.execute(
                """
                SELECT id, source_file, source, competence, filters_json,
                       accepted_count, imported_at
                FROM import_batches
                WHERE status = 'completed'
                ORDER BY imported_at DESC, id DESC
                """
            ).fetchall()
        return [
            {
                "lote_id": row["id"],
                "arquivo_fonte": row["source_file"],
                "fonte": row["source"],
                "competencia": row["competence"],
                "filtros": json.loads(row["filters_json"]),
                "registros": row["accepted_count"],
                "importado_em": row["imported_at"],
                "ativo": row["id"] == active,
            }
            for row in rows
        ]

    def update_batch_metadata(
        self,
        batch_id: str,
        source: str,
        competence: str | None,
        filters: Mapping[str, Any],
        etag: str | None = None,
    ) -> None:
        with self._connection() as connection, connection:
            self._update_batch_metadata_row(
                connection, batch_id, source, competence, filters, etag
            )

    @staticmethod
    def _update_batch_metadata_row(
        connection: sqlite3.Connection,
        batch_id: str,
        source: str,
        competence: str | None,
        filters: Mapping[str, Any],
        etag: str | None,
    ) -> None:
        cursor = connection.execute(
            """
            UPDATE import_batches
            SET source = ?, competence = ?, filters_json = ?, etag = ?
            WHERE id = ? AND status = 'completed'
            """,
            (
                source,
                competence,
                json.dumps(filters, ensure_ascii=False, sort_keys=True),
                etag,
                batch_id,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"Lote inexistente: {batch_id}")

    def get_batch_metadata(self, batch_id: str | None = None) -> dict[str, Any]:
        with self._connection() as connection:
            selected = self._require_batch(connection, batch_id)
            row = connection.execute(
                """
                SELECT id, source, competence, filters_json, etag, imported_at
                FROM import_batches
                WHERE id = ? AND status = 'completed'
                """,
                (selected,),
            ).fetchone()
            competences = [
                str(item[0])
                for item in connection.execute(
                    """
                    SELECT DISTINCT competencia
                    FROM staging_establishments
                    WHERE batch_id = ? AND trim(competencia) <> ''
                    ORDER BY competencia
                    """,
                    (selected,),
                )
            ]
        if row is None:
            raise ValueError(f"Lote inexistente: {selected}")
        return {
            "lote_id": str(row["id"]),
            "fonte": str(row["source"]),
            "competencia": (
                row["competence"]
                if row["competence"] is not None
                else (competences[0] if len(competences) == 1 else competences or None)
            ),
            "filtros": json.loads(row["filters_json"]),
            "etag": row["etag"],
            "importado_em": str(row["imported_at"]),
        }

    def activate_batch(self, batch_id: str) -> None:
        timestamp = datetime.now(UTC).isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                selected = self._require_batch(connection, batch_id)
                self._replace_projection(connection, selected, timestamp)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def purge_batch(self, batch_id: str) -> tuple[int, int]:
        with self._connection() as connection:
            page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
            free_before = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
            connection.execute("BEGIN IMMEDIATE")
            try:
                selected = self._require_batch(connection, batch_id)
                count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM staging_establishments WHERE batch_id = ?",
                        (selected,),
                    ).fetchone()[0]
                )
                was_active = selected == self._active_batch_id(connection)
                if was_active:
                    connection.execute("DELETE FROM establishments")
                connection.execute("DELETE FROM import_batches WHERE id = ?", (selected,))
                if was_active:
                    replacement = connection.execute(
                        """
                        SELECT id FROM import_batches WHERE status = 'completed'
                        ORDER BY imported_at DESC, id DESC LIMIT 1
                        """
                    ).fetchone()
                    if replacement:
                        self._replace_projection(
                            connection, str(replacement[0]), datetime.now(UTC).isoformat()
                        )
                    else:
                        connection.execute(
                            "INSERT INTO establishments_municipality_fts"
                            "(establishments_municipality_fts) VALUES('rebuild')"
                        )
                connection.commit()
                free_after = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
                return count, max(0, free_after - free_before) * page_size
            except BaseException:
                connection.rollback()
                raise

    def validate_dataset(self, batch_id: str | None = None) -> dict[str, Any]:
        text_columns = (
            "cnes",
            "nome_fantasia",
            "municipio",
            "uf",
            "tipo_estabelecimento",
            "natureza_juridica",
            "gestao",
            "competencia",
        )
        with self._connection() as connection:
            selected = self._require_batch(connection, batch_id)
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM staging_establishments WHERE batch_id = ?",
                    (selected,),
                ).fetchone()[0]
            )
            empty = {
                column: int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM staging_establishments "
                        f"WHERE batch_id = ? AND trim({column}) = ''",
                        (selected,),
                    ).fetchone()[0]
                )
                for column in text_columns
            }
            duplicates = int(
                connection.execute(
                    """
                    SELECT COALESCE(SUM(total - 1), 0) FROM (
                        SELECT COUNT(*) AS total FROM staging_establishments
                        WHERE batch_id = ? GROUP BY cnes, competencia HAVING COUNT(*) > 1
                    )
                    """,
                    (selected,),
                ).fetchone()[0]
            )
            competences = [
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT DISTINCT competencia FROM staging_establishments
                    WHERE batch_id = ? ORDER BY competencia
                    """,
                    (selected,),
                )
                if row[0]
            ]
            invalid_beds = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM staging_establishments
                    WHERE batch_id = ? AND (leitos_existentes < 0 OR leitos_sus < 0)
                    """,
                    (selected,),
                ).fetchone()[0]
            )
        return {
            "lote_id": selected,
            "total_registros": total,
            "campos_vazios": empty,
            "cnes_duplicados": duplicates,
            "competencias": competences,
            "competencias_mistas": len(competences) > 1,
            "leitos_invalidos": invalid_beds,
            "valido": total > 0 and duplicates == 0 and invalid_beds == 0,
        }

    @staticmethod
    def _staging_filters(filters: Mapping[str, Any]) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        exact = {"uf": "uf", "gestao": "gestao", "convenio_sus": "convenio_sus"}
        partial = {
            "municipio": "municipio_normalizado",
            "tipo_estabelecimento": "tipo_estabelecimento",
            "natureza_juridica": "natureza_juridica",
        }
        for name, column in exact.items():
            if (value := filters.get(name)) is not None:
                clauses.append(f"{column} = ?")
                params.append(int(value) if name == "convenio_sus" else str(value).upper())
        for name, column in partial.items():
            if value := filters.get(name):
                clauses.append(f"instr(lower({column}), lower(?)) > 0")
                params.append(
                    normalize_search_text(str(value)) if name == "municipio" else str(value)
                )
        if (value := filters.get("min_leitos")) is not None:
            clauses.append("leitos_existentes >= ?")
            params.append(int(value))
        if (value := filters.get("max_leitos")) is not None:
            clauses.append("leitos_existentes <= ?")
            params.append(int(value))
        if cnes_list := filters.get("cnes_list"):
            values = [str(value) for value in cnes_list]
            clauses.append(f"cnes IN ({', '.join('?' for _ in values)})")
            params.extend(values)
        return clauses, params

    def aggregate(
        self,
        group_by: str,
        metric: str,
        filters: Mapping[str, Any],
        batch_id: str | None = None,
    ) -> list[dict[str, Any]]:
        groups = {
            "uf": "uf",
            "municipio": "municipio",
            "tipo": "tipo_estabelecimento",
            "natureza": "natureza_juridica",
            "gestao": "gestao",
        }
        metrics = {
            "estabelecimentos": "COUNT(*)",
            "leitos_existentes": "SUM(leitos_existentes)",
            "leitos_sus": "SUM(leitos_sus)",
            "media_leitos": "AVG(leitos_existentes)",
        }
        if group_by not in groups or metric not in metrics:
            raise ValueError("group_by ou metrica não suportada")
        with self._connection() as connection:
            selected = self._require_batch(connection, batch_id)
            clauses, params = self._staging_filters(filters)
            where = " AND ".join(["batch_id = ?", *clauses])
            rows = connection.execute(
                f"SELECT {groups[group_by]} AS grupo, {metrics[metric]} AS valor "
                f"FROM staging_establishments WHERE {where} "
                "GROUP BY grupo ORDER BY valor DESC, grupo",
                [selected, *params],
            ).fetchall()
        return [{"grupo": row["grupo"], "valor": row["valor"]} for row in rows]

    def timeseries(
        self, key: str, key_type: str, start: str, end: str
    ) -> list[dict[str, Any]]:
        if key_type not in {"cnes", "municipio"}:
            raise ValueError("tipo_chave deve ser cnes ou municipio")
        column = "cnes" if key_type == "cnes" else "municipio_normalizado"
        value = key if key_type == "cnes" else normalize_search_text(key)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                WITH ranked AS (
                    SELECT s.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY s.cnes, s.competencia
                               ORDER BY b.imported_at DESC, b.id DESC
                           ) AS position
                    FROM staging_establishments s
                    JOIN import_batches b ON b.id = s.batch_id
                    WHERE s.competencia BETWEEN ? AND ? AND s.{column} = ?
                )
                SELECT competencia, COUNT(*) AS estabelecimentos,
                       SUM(leitos_existentes) AS leitos_existentes,
                       SUM(leitos_sus) AS leitos_sus
                FROM ranked WHERE position = 1
                GROUP BY competencia ORDER BY competencia
                """,
                (start, end, value),
            ).fetchall()
        return [dict(row) for row in rows]

    def diff_batches(self, batch_a: str, batch_b: str) -> dict[str, Any]:
        with self._connection() as connection:
            left_id = self._require_batch(connection, batch_a)
            right_id = self._require_batch(connection, batch_b)
            metadata = {
                row["id"]: row["filters_json"]
                for row in connection.execute(
                    "SELECT id, filters_json FROM import_batches WHERE id IN (?, ?)",
                    (left_id, right_id),
                )
            }
            left_rows = connection.execute(
                "SELECT cnes, competencia, leitos_existentes, leitos_sus "
                "FROM staging_establishments WHERE batch_id = ?",
                (left_id,),
            ).fetchall()
            right_rows = connection.execute(
                "SELECT cnes, competencia, leitos_existentes, leitos_sus "
                "FROM staging_establishments WHERE batch_id = ?",
                (right_id,),
            ).fetchall()
        mixed = (
            len({str(row["competencia"]) for row in left_rows}) > 1
            or len({str(row["competencia"]) for row in right_rows}) > 1
        )
        key = (
            (lambda row: (str(row["cnes"]), str(row["competencia"])))
            if mixed
            else (lambda row: (str(row["cnes"]), ""))
        )
        left = {key(row): row for row in left_rows}
        right = {key(row): row for row in right_rows}
        def display_key(item: tuple[str, str]) -> str:
            return f"{item[0]}@{item[1]}" if mixed else item[0]

        entered = [display_key(item) for item in sorted(right.keys() - left.keys())]
        exited = [display_key(item) for item in sorted(left.keys() - right.keys())]
        changed = [
            {
                "cnes": item[0],
                "competencia_a": str(left[item]["competencia"]) if mixed else None,
                "competencia_b": str(right[item]["competencia"]) if mixed else None,
                "leitos_existentes_a": left[item]["leitos_existentes"],
                "leitos_existentes_b": right[item]["leitos_existentes"],
                "leitos_sus_a": left[item]["leitos_sus"],
                "leitos_sus_b": right[item]["leitos_sus"],
            }
            for item in sorted(left.keys() & right.keys())
            if (
                left[item]["leitos_existentes"],
                left[item]["leitos_sus"],
            )
            != (
                right[item]["leitos_existentes"],
                right[item]["leitos_sus"],
            )
        ]
        return {
            "lote_a": left_id,
            "lote_b": right_id,
            "entraram": entered,
            "sairam": exited,
            "mudaram_leitos": changed,
            "avisos": (
                []
                if metadata.get(left_id) == metadata.get(right_id)
                else [
                    "Os lotes possuem filtros de origem diferentes; entradas e saídas "
                    "podem refletir cobertura, não mudança cadastral."
                ]
            ),
        }

    def advanced_search(
        self,
        filters: Mapping[str, Any],
        order_by: str,
        offset: int,
        limit: int,
        batch_id: str | None = None,
    ) -> tuple[list[HospitalInfo], int]:
        orders = {
            "cnes": "cnes",
            "municipio": "municipio_normalizado, cnes",
            "leitos_existentes": "leitos_existentes DESC, cnes",
            "leitos_sus": "leitos_sus DESC, cnes",
        }
        if order_by not in orders:
            raise ValueError("order_by não suportado")
        with self._connection() as connection:
            selected = self._require_batch(connection, batch_id)
            clauses, params = self._staging_filters(filters)
            where = " AND ".join(["batch_id = ?", *clauses])
            rows = connection.execute(
                f"""
                WITH filtered AS (
                    SELECT {HOSPITAL_COLUMNS}, municipio_normalizado
                    FROM staging_establishments WHERE {where}
                ),
                page AS (
                    SELECT * FROM filtered
                    ORDER BY {orders[order_by]} LIMIT ? OFFSET ?
                ),
                total AS (
                    SELECT COUNT(*) AS total_available FROM filtered
                )
                SELECT page.*, total.total_available
                FROM total LEFT JOIN page ON 1 = 1
                """,
                [selected, *params, limit, offset],
            ).fetchall()
        total = int(rows[0]["total_available"])
        return [self._to_hospital(row) for row in rows if row["cnes"] is not None], total

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
