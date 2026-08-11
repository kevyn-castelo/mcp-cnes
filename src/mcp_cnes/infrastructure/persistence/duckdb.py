"""Catálogo DuckDB com lotes imutáveis em Parquet."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, cast

import duckdb
import pandas as pd

from mcp_cnes.domain.identity import canonical_hospital_digest, contextual_batch_digest
from mcp_cnes.domain.models import HospitalInfo, HospitalInfoV2, LoadSummary
from mcp_cnes.domain.rules import normalize_search_text

V1_COLUMNS = (
    "competencia",
    "uf",
    "municipio",
    "cnes",
    "nome_fantasia",
    "tipo_estabelecimento",
    "natureza_juridica",
    "gestao",
    "leitos_existentes",
    "leitos_sus",
    "convenio_sus",
)
V2_COLUMNS = (
    "razao_social",
    "cnpj",
    "cnpj_mantenedora",
    "tipo_pessoa",
    "nivel_dependencia",
    "logradouro",
    "numero",
    "complemento",
    "bairro",
    "cep",
    "latitude",
    "longitude",
    "geo_confiavel",
    "telefone",
    "email",
    "leitos_uti_adulto",
    "leitos_uti_pediatrica",
    "leitos_uti_neonatal",
    "leitos_cirurgicos",
    "leitos_clinicos",
    "leitos_obstetricos",
    "leitos_complementares",
    "habilitacoes",
    "total_habilitacoes",
    "campos_ausentes",
)
STORAGE_COLUMNS = (*V1_COLUMNS, *V2_COLUMNS, "municipio_normalizado")
TEXT_V2_COLUMNS = {
    "razao_social",
    "cnpj",
    "cnpj_mantenedora",
    "tipo_pessoa",
    "nivel_dependencia",
    "logradouro",
    "numero",
    "complemento",
    "bairro",
    "cep",
    "telefone",
    "email",
}
NULLABLE_INTEGER_V2_COLUMNS = {
    "leitos_uti_adulto",
    "leitos_uti_pediatrica",
    "leitos_uti_neonatal",
    "leitos_cirurgicos",
    "leitos_clinicos",
    "leitos_obstetricos",
    "leitos_complementares",
}


class DuckDBCNESRepository:
    """Mantém metadados no DuckDB e consulta os lotes direto no Parquet."""

    def __init__(
        self,
        database_path: Path,
        *,
        columnar_dir: Path | None = None,
        batch_retention_count: int = 5,
    ) -> None:
        if batch_retention_count < 1:
            raise ValueError("batch_retention_count deve ser maior que zero")
        self.database_path = database_path
        self.columnar_dir = columnar_dir or database_path.parent / "parquet"
        self.batch_retention_count = batch_retention_count
        self._write_lock = RLock()
        self._schema_ready = False

    def _connect(self) -> duckdb.DuckDBPyConnection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = duckdb.connect(str(self.database_path))
        if not self._schema_ready:
            self._ensure_schema(connection)
        return connection

    def _ensure_schema(self, connection: duckdb.DuckDBPyConnection) -> None:
        with self._write_lock:
            if self._schema_ready:
                return
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS import_batches (
                    id VARCHAR PRIMARY KEY,
                    source_file VARCHAR NOT NULL,
                    source VARCHAR NOT NULL,
                    competence VARCHAR,
                    filters_json VARCHAR NOT NULL,
                    etag VARCHAR,
                    resource_version VARCHAR,
                    contract_version VARCHAR NOT NULL,
                    parquet_path VARCHAR NOT NULL,
                    accepted_count BIGINT NOT NULL,
                    rows_read BIGINT NOT NULL,
                    rejected_count BIGINT NOT NULL,
                    ignored_count BIGINT NOT NULL,
                    rejection_reasons VARCHAR NOT NULL,
                    imported_at VARCHAR NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    active_batch_id VARCHAR
                );
                INSERT INTO runtime_state(singleton, active_batch_id)
                VALUES (1, NULL) ON CONFLICT DO NOTHING;
                """
            )
            self._schema_ready = True

    @staticmethod
    def _file_digest(path: Path) -> str:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

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
        selected = batch_id or canonical_hospital_digest(hospitals)
        self.columnar_dir.mkdir(parents=True, exist_ok=True)
        target = self.columnar_dir / f"{selected}.parquet"
        if not target.exists():
            records: list[dict[str, Any]] = []
            for hospital in hospitals:
                row = hospital.to_dict()
                row.update({name: None for name in TEXT_V2_COLUMNS})
                row.update({name: None for name in NULLABLE_INTEGER_V2_COLUMNS})
                row.update(
                    {
                        "latitude": None,
                        "longitude": None,
                        "geo_confiavel": False,
                        "habilitacoes": [],
                        "total_habilitacoes": 0,
                        "campos_ausentes": list(V2_COLUMNS),
                        "municipio_normalizado": normalize_search_text(hospital.municipio),
                    }
                )
                records.append(row)
            frame = pd.DataFrame.from_records(records, columns=STORAGE_COLUMNS)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{selected}-", suffix=".parquet", dir=self.columnar_dir
            )
            os.close(descriptor)
            temporary = Path(temporary_name)
            try:
                with duckdb.connect() as connection:
                    connection.register("incoming_batch", frame)
                    connection.execute(
                        "COPY incoming_batch TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
                        [str(temporary)],
                    )
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return self._register_metadata(
            selected,
            target,
            source_file=source_file,
            source=source,
            competence=competence,
            filters=filters or {},
            records=effective_summary.records_loaded,
            rows_read=effective_summary.rows_read,
            rejected=effective_summary.rows_rejected,
            ignored=effective_summary.rows_ignored,
            rejection_reasons=[asdict(item) for item in effective_summary.rejection_reasons],
            etag=etag,
            resource_version=None,
            contract_version="v1",
            imported_at=loaded_at,
        )

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

    def register_parquet_batch(
        self,
        parquet_path: Path,
        *,
        source_file: str,
        source: str,
        competence: str,
        filters: Mapping[str, Any],
        records: int,
        etag: str | None,
        contract_version: str,
        resource_version: str | None,
    ) -> str:
        source_path = parquet_path.resolve(strict=True)
        columns = self._parquet_columns(source_path)
        expected = set(STORAGE_COLUMNS if contract_version == "v2" else V1_COLUMNS)
        missing = sorted(expected - columns)
        extras = sorted(columns - set(STORAGE_COLUMNS))
        if missing:
            raise ValueError(
                f"Parquet {contract_version} sem campos obrigatórios: {', '.join(missing)}"
            )
        if extras:
            raise ValueError(
                "Parquet contém campos fora da allowlist institucional: " + ", ".join(extras)
            )
        digest = resource_version or self._file_digest(source_path)
        batch_id = contextual_batch_digest(
            hashlib.sha256(digest.encode()).hexdigest(),
            source=source,
            competence=competence,
            filters=filters,
        )
        self.columnar_dir.mkdir(parents=True, exist_ok=True)
        target = (self.columnar_dir / f"{batch_id}.parquet").resolve(strict=False)
        if source_path != target:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{batch_id}-", suffix=".parquet", dir=self.columnar_dir
            )
            os.close(descriptor)
            temporary = Path(temporary_name)
            try:
                shutil.copy2(source_path, temporary)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return self._register_metadata(
            batch_id,
            target,
            source_file=source_file,
            source=source,
            competence=competence,
            filters=filters,
            records=records,
            rows_read=records,
            rejected=0,
            ignored=0,
            rejection_reasons=[],
            etag=etag,
            resource_version=resource_version,
            contract_version=contract_version,
        )

    @staticmethod
    def _parquet_columns(path: Path) -> set[str]:
        with duckdb.connect() as connection:
            rows = connection.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]
            ).fetchall()
        return {str(row[0]) for row in rows}

    def _register_metadata(
        self,
        batch_id: str,
        parquet_path: Path,
        *,
        source_file: str,
        source: str,
        competence: str | None,
        filters: Mapping[str, Any],
        records: int,
        rows_read: int,
        rejected: int,
        ignored: int,
        rejection_reasons: Sequence[Mapping[str, Any]],
        etag: str | None,
        resource_version: str | None,
        contract_version: str,
        imported_at: datetime | None = None,
    ) -> str:
        timestamp = (imported_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        with self._write_lock, self._connect() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.execute(
                    """
                    INSERT INTO import_batches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        source = excluded.source, competence = excluded.competence,
                        filters_json = excluded.filters_json, etag = excluded.etag,
                        resource_version = excluded.resource_version,
                        parquet_path = excluded.parquet_path
                    """,
                    [
                        batch_id,
                        Path(source_file).name,
                        source,
                        competence,
                        json.dumps(filters, ensure_ascii=False, sort_keys=True),
                        etag,
                        resource_version,
                        contract_version,
                        str(parquet_path),
                        records,
                        rows_read,
                        rejected,
                        ignored,
                        json.dumps(list(rejection_reasons), ensure_ascii=False, sort_keys=True),
                        timestamp,
                    ],
                )
                connection.execute(
                    "UPDATE runtime_state SET active_batch_id = ? WHERE singleton = 1",
                    [batch_id],
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        self._purge_old_batches(batch_id)
        return batch_id

    def _purge_old_batches(self, current_batch_id: str) -> None:
        with self._write_lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, parquet_path FROM import_batches WHERE id <> ?
                ORDER BY imported_at DESC, id DESC OFFSET ?
                """,
                [current_batch_id, self.batch_retention_count - 1],
            ).fetchall()
            for batch_id, raw_path in rows:
                connection.execute("DELETE FROM import_batches WHERE id = ?", [batch_id])
                self._safe_parquet_path(str(raw_path)).unlink(missing_ok=True)

    def _safe_parquet_path(self, raw_path: str) -> Path:
        base = self.columnar_dir.resolve(strict=False)
        path = Path(raw_path).resolve(strict=False)
        try:
            path.relative_to(base)
        except ValueError as exc:
            raise RuntimeError("Caminho de lote fora do diretório colunar") from exc
        return path

    def _active_batch_id(self, connection: duckdb.DuckDBPyConnection) -> str | None:
        row = connection.execute(
            "SELECT active_batch_id FROM runtime_state WHERE singleton = 1"
        ).fetchone()
        return str(row[0]) if row and row[0] else None

    def _batch(self, batch_id: str | None = None) -> dict[str, Any]:
        with self._connect() as connection:
            selected = batch_id or self._active_batch_id(connection)
            if selected is None:
                raise ValueError("Nenhum lote ativo")
            cursor = connection.execute(
                """
                SELECT id, source_file, source, competence, filters_json, etag,
                       resource_version, contract_version, parquet_path, accepted_count,
                       imported_at
                FROM import_batches WHERE id = ?
                """,
                [selected],
            )
            row = cursor.fetchone()
            names = [item[0] for item in cursor.description]
        if row is None:
            raise ValueError(f"Lote inexistente: {selected}")
        result = dict(zip(names, row, strict=True))
        result["parquet_path"] = str(self._safe_parquet_path(result["parquet_path"]))
        return result

    def has_data(self) -> bool:
        try:
            batch = self._batch()
        except ValueError:
            return False
        return Path(batch["parquet_path"]).is_file() and int(batch["accepted_count"]) > 0

    @staticmethod
    def _filters(filters: Mapping[str, Any]) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        exact = {"uf": "uf", "gestao": "gestao", "convenio_sus": "convenio_sus"}
        partial = {
            "municipio": "municipio_normalizado",
            "tipo_estabelecimento": "tipo_estabelecimento",
            "natureza_juridica": "natureza_juridica",
        }
        for name, column in exact.items():
            value = filters.get(name)
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(bool(value) if name == "convenio_sus" else str(value).upper())
        for name, column in partial.items():
            value = filters.get(name)
            if value:
                clauses.append(f"contains(lower({column}), lower(?))")
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

    @staticmethod
    def _dict_rows(cursor: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
        names = [item[0] for item in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    @staticmethod
    def _to_v1(row: Mapping[str, Any]) -> HospitalInfo:
        return HospitalInfo(**{name: row[name] for name in V1_COLUMNS})

    @staticmethod
    def _to_v2(row: Mapping[str, Any]) -> HospitalInfoV2:
        values = {name: row.get(name) for name in (*V1_COLUMNS, *V2_COLUMNS)}
        return HospitalInfoV2(**cast(Any, values))

    def advanced_search(
        self,
        filters: Mapping[str, Any],
        order_by: str,
        offset: int,
        limit: int,
        batch_id: str | None = None,
    ) -> tuple[list[HospitalInfo], int]:
        rows, total = self._advanced_rows(filters, order_by, offset, limit, batch_id, False)
        return [self._to_v1(row) for row in rows], total

    def advanced_search_v2(
        self,
        filters: Mapping[str, Any],
        order_by: str,
        offset: int,
        limit: int,
        batch_id: str | None = None,
    ) -> tuple[list[HospitalInfoV2], int]:
        batch = self._batch(batch_id)
        if batch["contract_version"] != "v2":
            raise ValueError("O lote selecionado usa contrato v1; carregue datasus_base_completa")
        rows, total = self._advanced_rows(filters, order_by, offset, limit, batch_id, True)
        return [self._to_v2(row) for row in rows], total

    def _advanced_rows(
        self,
        filters: Mapping[str, Any],
        order_by: str,
        offset: int,
        limit: int,
        batch_id: str | None,
        v2: bool,
    ) -> tuple[list[dict[str, Any]], int]:
        orders = {
            "cnes": "cnes",
            "municipio": "municipio_normalizado, cnes",
            "leitos_existentes": "leitos_existentes DESC, cnes",
            "leitos_sus": "leitos_sus DESC, cnes",
        }
        if order_by not in orders:
            raise ValueError("order_by não suportado")
        batch = self._batch(batch_id)
        clauses, params = self._filters(filters)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        columns = ", ".join((*V1_COLUMNS, *V2_COLUMNS) if v2 else V1_COLUMNS)
        with duckdb.connect() as connection:
            total_row = connection.execute(
                f"SELECT COUNT(*) FROM read_parquet(?) {where}",
                [batch["parquet_path"], *params],
            ).fetchone()
            assert total_row is not None
            total = int(total_row[0])
            cursor = connection.execute(
                f"SELECT {columns} FROM read_parquet(?) {where} "
                f"ORDER BY {orders[order_by]} LIMIT ? OFFSET ?",
                [batch["parquet_path"], *params, limit, offset],
            )
            rows = self._dict_rows(cursor)
        return rows, total

    def search_by_municipality(
        self,
        municipality: str,
        min_beds: int | None,
        max_beds: int | None,
        limit: int | None = None,
    ) -> list[HospitalInfo]:
        rows, _ = self.advanced_search(
            {"municipio": municipality, "min_leitos": min_beds, "max_leitos": max_beds},
            "municipio",
            0,
            limit or 500,
        )
        return rows

    def count_by_municipality(
        self, municipality: str, min_beds: int | None, max_beds: int | None
    ) -> int:
        return self.search_by_municipality_with_count(municipality, min_beds, max_beds, 1)[1]

    def search_by_municipality_with_count(
        self,
        municipality: str,
        min_beds: int | None,
        max_beds: int | None,
        limit: int,
    ) -> tuple[list[HospitalInfo], int]:
        return self.advanced_search(
            {"municipio": municipality, "min_leitos": min_beds, "max_leitos": max_beds},
            "municipio",
            0,
            limit,
        )

    def search_by_uf(
        self,
        uf: str,
        min_beds: int | None,
        max_beds: int | None,
        limit: int | None = None,
    ) -> list[HospitalInfo]:
        rows, _ = self.advanced_search(
            {"uf": uf, "min_leitos": min_beds, "max_leitos": max_beds},
            "leitos_existentes",
            0,
            limit or 500,
        )
        return rows

    def count_by_uf(self, uf: str, min_beds: int | None, max_beds: int | None) -> int:
        return self.search_by_uf_with_count(uf, min_beds, max_beds, 1)[1]

    def search_by_uf_with_count(
        self,
        uf: str,
        min_beds: int | None,
        max_beds: int | None,
        limit: int,
    ) -> tuple[list[HospitalInfo], int]:
        return self.advanced_search(
            {"uf": uf, "min_leitos": min_beds, "max_leitos": max_beds},
            "leitos_existentes",
            0,
            limit,
        )

    def get_by_cnes(self, cnes: str) -> HospitalInfo | None:
        rows, _ = self.advanced_search({"cnes_list": [cnes]}, "cnes", 0, 1)
        return rows[0] if rows else None

    def statistics(self) -> dict[str, Any]:
        batch = self._batch()
        with duckdb.connect() as connection:
            totals = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(leitos_existentes), 0), "
                "COALESCE(SUM(leitos_sus), 0) FROM read_parquet(?)",
                [batch["parquet_path"]],
            ).fetchone()
            assert totals is not None
            rows = connection.execute(
                "SELECT uf, COUNT(*) FROM read_parquet(?) GROUP BY uf ORDER BY uf",
                [batch["parquet_path"]],
            ).fetchall()
        return {
            "total_estabelecimentos": int(totals[0]),
            "total_leitos_existentes": int(totals[1]),
            "total_leitos_sus": int(totals[2]),
            "estabelecimentos_por_uf": {str(uf): int(total) for uf, total in rows},
            "ultima_atualizacao": str(batch["imported_at"]),
            "arquivo_fonte": batch["source_file"],
        }

    def list_batches(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            active = self._active_batch_id(connection)
            rows = connection.execute(
                "SELECT id, source_file, source, competence, filters_json, accepted_count, "
                "imported_at FROM import_batches ORDER BY imported_at DESC, id DESC"
            ).fetchall()
        return [
            {
                "lote_id": str(row[0]),
                "arquivo_fonte": str(row[1]),
                "fonte": str(row[2]),
                "competencia": row[3],
                "filtros": json.loads(row[4]),
                "registros": int(row[5]),
                "importado_em": str(row[6]),
                "ativo": row[0] == active,
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
        self._batch(batch_id)
        with self._write_lock, self._connect() as connection:
            connection.execute(
                "UPDATE import_batches SET source=?, competence=?, filters_json=?, etag=? "
                "WHERE id=?",
                [
                    source,
                    competence,
                    json.dumps(filters, ensure_ascii=False, sort_keys=True),
                    etag,
                    batch_id,
                ],
            )

    def get_batch_metadata(self, batch_id: str | None = None) -> dict[str, Any]:
        batch = self._batch(batch_id)
        return {
            "lote_id": batch["id"],
            "fonte": batch["source"],
            "competencia": batch["competence"],
            "filtros": json.loads(batch["filters_json"]),
            "etag": batch["etag"],
            "importado_em": str(batch["imported_at"]),
            "versao_contrato": batch["contract_version"],
            "versao_recurso": batch["resource_version"],
        }

    def activate_batch(self, batch_id: str) -> None:
        self._batch(batch_id)
        with self._write_lock, self._connect() as connection:
            connection.execute(
                "UPDATE runtime_state SET active_batch_id=? WHERE singleton=1", [batch_id]
            )

    def purge_batch(self, batch_id: str) -> tuple[int, int]:
        batch = self._batch(batch_id)
        path = Path(batch["parquet_path"])
        size = path.stat().st_size if path.exists() else 0
        with self._write_lock, self._connect() as connection:
            active = self._active_batch_id(connection)
            connection.execute("DELETE FROM import_batches WHERE id=?", [batch_id])
            if active == batch_id:
                replacement = connection.execute(
                    "SELECT id FROM import_batches ORDER BY imported_at DESC, id DESC LIMIT 1"
                ).fetchone()
                connection.execute(
                    "UPDATE runtime_state SET active_batch_id=? WHERE singleton=1",
                    [replacement[0] if replacement else None],
                )
        path.unlink(missing_ok=True)
        return int(batch["accepted_count"]), size

    def validate_dataset(self, batch_id: str | None = None) -> dict[str, Any]:
        batch = self._batch(batch_id)
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
        expressions = ", ".join(
            f"COUNT(*) FILTER (WHERE COALESCE(trim({name}), '') = '') AS {name}"
            for name in text_columns
        )
        with duckdb.connect() as connection:
            empty_cursor = connection.execute(
                f"SELECT {expressions} FROM read_parquet(?)", [batch["parquet_path"]]
            )
            empty_values = empty_cursor.fetchone()
            assert empty_values is not None
            empty = {name: value for name, value in zip(text_columns, empty_values, strict=True)}
            duplicate_row = connection.execute(
                "SELECT COALESCE(SUM(total - 1), 0) FROM (SELECT COUNT(*) total "
                "FROM read_parquet(?) GROUP BY cnes, competencia HAVING COUNT(*) > 1)",
                [batch["parquet_path"]],
            ).fetchone()
            assert duplicate_row is not None
            duplicates = int(duplicate_row[0])
            competences = [
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT competencia FROM read_parquet(?) "
                    "WHERE COALESCE(trim(competencia), '') <> '' ORDER BY competencia",
                    [batch["parquet_path"]],
                ).fetchall()
            ]
            invalid_row = connection.execute(
                "SELECT COUNT(*) FILTER (WHERE leitos_existentes < 0 OR leitos_sus < 0 "
                "OR leitos_sus > leitos_existentes), COUNT(*) FILTER (WHERE "
                "list_contains(CAST(campos_ausentes AS VARCHAR[]), 'leitos_existentes') "
                "OR list_contains(CAST(campos_ausentes AS VARCHAR[]), 'leitos_sus')) "
                "FROM read_parquet(?)",
                [batch["parquet_path"]],
            ).fetchone()
            assert invalid_row is not None
            invalid = int(invalid_row[0])
            missing = int(invalid_row[1])
        total = int(batch["accepted_count"])
        return {
            "lote_id": batch["id"],
            "total_registros": total,
            "campos_vazios": {name: int(value) for name, value in empty.items()},
            "cnes_duplicados": duplicates,
            "competencias": competences,
            "competencias_mistas": len(competences) > 1,
            "leitos_invalidos": invalid,
            "leitos_ausentes": missing,
            "valido": total > 0 and duplicates == 0 and invalid == 0 and missing == 0,
        }

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
        batch = self._batch(batch_id)
        clauses, params = self._filters(filters)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with duckdb.connect() as connection:
            rows = connection.execute(
                f"SELECT {groups[group_by]} grupo, {metrics[metric]} valor "
                f"FROM read_parquet(?) {where} GROUP BY grupo ORDER BY valor DESC, grupo",
                [batch["parquet_path"], *params],
            ).fetchall()
        return [{"grupo": row[0], "valor": row[1]} for row in rows]

    def timeseries(self, key: str, key_type: str, start: str, end: str) -> list[dict[str, Any]]:
        if key_type not in {"cnes", "municipio"}:
            raise ValueError("tipo_chave deve ser cnes ou municipio")
        column = "cnes" if key_type == "cnes" else "municipio_normalizado"
        value = key if key_type == "cnes" else normalize_search_text(key)
        latest: dict[tuple[str, str], tuple[str, int, int]] = {}
        with self._connect() as catalog:
            batches = catalog.execute(
                "SELECT parquet_path, imported_at FROM import_batches ORDER BY imported_at"
            ).fetchall()
        with duckdb.connect() as connection:
            for raw_path, imported_at in batches:
                rows = connection.execute(
                    f"SELECT cnes, competencia, leitos_existentes, leitos_sus "
                    f"FROM read_parquet(?) WHERE competencia BETWEEN ? AND ? AND {column}=?",
                    [str(self._safe_parquet_path(raw_path)), start, end, value],
                ).fetchall()
                for cnes, competence, beds, sus in rows:
                    latest[(str(cnes), str(competence))] = (str(imported_at), int(beds), int(sus))
        grouped: dict[str, list[int]] = {}
        for (_, competence), (_, beds, sus) in latest.items():
            values = grouped.setdefault(competence, [0, 0, 0])
            values[0] += 1
            values[1] += beds
            values[2] += sus
        return [
            {
                "competencia": competence,
                "estabelecimentos": values[0],
                "leitos_existentes": values[1],
                "leitos_sus": values[2],
            }
            for competence, values in sorted(grouped.items())
        ]

    def diff_batches(self, batch_a: str, batch_b: str) -> dict[str, Any]:
        left_batch, right_batch = self._batch(batch_a), self._batch(batch_b)
        with duckdb.connect() as connection:
            left_rows = connection.execute(
                "SELECT cnes, competencia, leitos_existentes, leitos_sus FROM read_parquet(?)",
                [left_batch["parquet_path"]],
            ).fetchall()
            right_rows = connection.execute(
                "SELECT cnes, competencia, leitos_existentes, leitos_sus FROM read_parquet(?)",
                [right_batch["parquet_path"]],
            ).fetchall()
        mixed = len({row[1] for row in left_rows}) > 1 or len({row[1] for row in right_rows}) > 1

        def key(row: Sequence[Any]) -> tuple[str, str]:
            return (str(row[0]), str(row[1]) if mixed else "")

        left = {key(row): row for row in left_rows}
        right = {key(row): row for row in right_rows}

        def display(item: tuple[str, str]) -> str:
            return f"{item[0]}@{item[1]}" if mixed else item[0]

        changed = [
            {
                "cnes": item[0],
                "competencia_a": str(left[item][1]) if mixed else None,
                "competencia_b": str(right[item][1]) if mixed else None,
                "leitos_existentes_a": int(left[item][2]),
                "leitos_existentes_b": int(right[item][2]),
                "leitos_sus_a": int(left[item][3]),
                "leitos_sus_b": int(right[item][3]),
            }
            for item in sorted(left.keys() & right.keys())
            if left[item][2:] != right[item][2:]
        ]
        return {
            "lote_a": left_batch["id"],
            "lote_b": right_batch["id"],
            "entraram": [display(item) for item in sorted(right.keys() - left.keys())],
            "sairam": [display(item) for item in sorted(left.keys() - right.keys())],
            "mudaram_leitos": changed,
            "avisos": []
            if left_batch["filters_json"] == right_batch["filters_json"]
            else [
                "Os lotes possuem filtros de origem diferentes; entradas e saídas podem "
                "refletir cobertura, não mudança cadastral."
            ],
        }

    def _batch_for_competence(
        self, competence: str, batch_id: str | None = None
    ) -> dict[str, Any]:
        if batch_id is not None:
            batch = self._batch(batch_id)
            if batch["contract_version"] != "v2":
                raise ValueError(f"O lote {batch_id} não usa o contrato v2")
            if batch["competence"] != competence:
                raise ValueError(
                    f"O lote {batch_id} pertence à competência {batch['competence']}, não {competence}"
                )
            return batch
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM import_batches WHERE competence=? AND contract_version='v2' "
                "ORDER BY imported_at DESC, id DESC",
                [competence],
            ).fetchall()
        if not rows:
            raise ValueError(
                f"Nenhum lote v2 retido para {competence}; carregue datasus_base_completa"
            )
        if len(rows) > 1:
            identifiers = ", ".join(str(row[0]) for row in rows)
            raise ValueError(
                f"Mais de um lote v2 existe para {competence}: {identifiers}. "
                "Informe lote_a/lote_b explicitamente."
            )
        return self._batch(str(rows[0][0]))

    def group_by_maintainer(
        self,
        filters: Mapping[str, Any],
        limit: int,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        batch = self._batch(batch_id)
        if batch["contract_version"] != "v2":
            raise ValueError("Agrupamento por mantenedora requer um lote v2")
        clauses, params = self._filters(filters)
        missing_clauses = [*clauses, "COALESCE(trim(cnpj_mantenedora), '') = ''"]
        missing_where = " WHERE " + " AND ".join(missing_clauses)
        clauses.append("COALESCE(trim(cnpj_mantenedora), '') <> ''")
        where = " WHERE " + " AND ".join(clauses)
        with duckdb.connect() as connection:
            missing_row = connection.execute(
                f"SELECT COUNT(*) FROM read_parquet(?) {missing_where}",
                [batch["parquet_path"], *params],
            ).fetchone()
            assert missing_row is not None
            cursor = connection.execute(
                f"""
                WITH filtered AS (
                    SELECT * FROM read_parquet(?) {where}
                ), ranked AS (
                    SELECT cnpj_mantenedora, COUNT(*) AS unidades,
                           CASE WHEN COUNT(*) FILTER (WHERE list_contains(
                               CAST(campos_ausentes AS VARCHAR[]), 'leitos_existentes'
                           )) > 0 THEN NULL ELSE SUM(leitos_existentes) END
                               AS leitos_existentes,
                           CASE WHEN COUNT(*) FILTER (WHERE list_contains(
                               CAST(campos_ausentes AS VARCHAR[]), 'leitos_sus'
                           )) > 0 THEN NULL ELSE SUM(leitos_sus) END AS leitos_sus
                    FROM filtered GROUP BY cnpj_mantenedora
                    ORDER BY unidades DESC, leitos_existentes DESC, cnpj_mantenedora
                    LIMIT ?
                )
                SELECT r.cnpj_mantenedora, r.unidades, r.leitos_existentes,
                       r.leitos_sus, f.uf, COUNT(*) AS unidades_uf
                FROM ranked r JOIN filtered f USING (cnpj_mantenedora)
                GROUP BY ALL
                ORDER BY r.unidades DESC, r.leitos_existentes DESC,
                         r.cnpj_mantenedora, f.uf
                """,
                [batch["parquet_path"], *params, limit],
            )
            rows = self._dict_rows(cursor)
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            cnpj = str(row["cnpj_mantenedora"])
            total_beds = (
                int(row["leitos_existentes"])
                if row["leitos_existentes"] is not None
                else None
            )
            sus_beds = int(row["leitos_sus"]) if row["leitos_sus"] is not None else None
            valid_mix = (
                total_beds is not None
                and sus_beds is not None
                and total_beds > 0
                and 0 <= sus_beds <= total_beds
            )
            mix_sus = (
                round(sus_beds / total_beds, 6)
                if valid_mix and sus_beds is not None and total_beds is not None
                else None
            )
            alerts = (
                ["leitos_sus_maior_que_leitos_existentes"]
                if total_beds is not None and sus_beds is not None and sus_beds > total_beds
                else []
            )
            missing_fields = ["nome_mantenedora"]
            if total_beds is None:
                missing_fields.append("leitos_existentes")
            if sus_beds is None:
                missing_fields.append("leitos_sus")
            item = grouped.setdefault(
                cnpj,
                {
                    "cnpj_mantenedora": cnpj,
                    "rede": None,
                    "unidades": int(row["unidades"]),
                    "leitos_existentes": total_beds,
                    "leitos_sus": sus_beds,
                    "mix_sus": mix_sus,
                    "mix_nao_sus": round(1 - mix_sus, 6) if mix_sus is not None else None,
                    "distribuicao_uf": {},
                    "campos_ausentes": missing_fields,
                    "alertas": alerts,
                },
            )
            item["distribuicao_uf"][str(row["uf"])] = int(row["unidades_uf"])
        return {
            "lote_id": str(batch["id"]),
            "unidades_sem_cnpj_mantenedora": int(missing_row[0]),
            "redes": list(grouped.values()),
        }

    def lead_triggers(
        self,
        competence_a: str,
        competence_b: str,
        delta_min: int,
        establishment_type: str | None = None,
        batch_a: str | None = None,
        batch_b: str | None = None,
    ) -> dict[str, Any]:
        left, right = (
            self._batch_for_competence(competence_a, batch_a),
            self._batch_for_competence(competence_b, batch_b),
        )
        type_clause = ""
        params: list[Any] = [left["parquet_path"], right["parquet_path"]]
        if establishment_type:
            type_clause = (
                " AND contains(lower(COALESCE(b.tipo_estabelecimento, "
                "a.tipo_estabelecimento)), lower(?))"
            )
            params.append(establishment_type)
        params.append(delta_min)
        with duckdb.connect() as connection:
            cursor = connection.execute(
                f"""
                WITH a AS (
                    SELECT cnes, nome_fantasia, tipo_estabelecimento,
                           leitos_existentes, leitos_sus,
                           CAST(campos_ausentes AS VARCHAR[]) AS campos_ausentes
                    FROM read_parquet(?)
                ), b AS (
                    SELECT cnes, nome_fantasia, tipo_estabelecimento,
                           leitos_existentes, leitos_sus,
                           CAST(campos_ausentes AS VARCHAR[]) AS campos_ausentes
                    FROM read_parquet(?)
                ), compared_base AS (
                    SELECT
                        COALESCE(b.cnes, a.cnes) AS cnes,
                        COALESCE(b.nome_fantasia, a.nome_fantasia) AS nome_fantasia,
                        COALESCE(b.tipo_estabelecimento, a.tipo_estabelecimento)
                            AS tipo_estabelecimento,
                        a.leitos_existentes AS leitos_existentes_a,
                        b.leitos_existentes AS leitos_existentes_b,
                        a.leitos_sus AS leitos_sus_a,
                        b.leitos_sus AS leitos_sus_b,
                        (a.cnes IS NOT NULL AND COALESCE(list_contains(
                            a.campos_ausentes, 'leitos_existentes'
                        ), false)) OR (b.cnes IS NOT NULL AND COALESCE(list_contains(
                            b.campos_ausentes, 'leitos_existentes'
                        ), false)) AS leitos_ausentes
                    FROM a FULL OUTER JOIN b USING (cnes)
                    WHERE 1=1 {type_clause}
                ), compared AS (
                    SELECT *,
                        CASE WHEN NOT leitos_ausentes THEN
                            COALESCE(leitos_existentes_b, 0)
                                - COALESCE(leitos_existentes_a, 0)
                        END AS delta_leitos,
                        CASE
                            WHEN leitos_ausentes THEN NULL
                            WHEN leitos_existentes_a IS NULL THEN 'entrada'
                            WHEN leitos_existentes_b IS NULL THEN 'saida'
                            WHEN leitos_existentes_b > leitos_existentes_a THEN 'expansao'
                            WHEN leitos_existentes_b < leitos_existentes_a THEN 'retracao'
                        END AS motivo
                    FROM compared_base
                )
                SELECT * FROM compared
                WHERE leitos_ausentes OR (motivo IS NOT NULL AND abs(delta_leitos) >= ?)
                ORDER BY abs(delta_leitos) DESC, cnes
                """,
                params,
            )
            rows = self._dict_rows(cursor)
        omitted = sum(bool(row.pop("leitos_ausentes")) for row in rows)
        triggers = [row for row in rows if row["motivo"] is not None]
        warnings = []
        if omitted:
            warnings.append(
                f"{omitted} estabelecimento(s) omitido(s) porque leitos_existentes "
                "está ausente em ao menos uma competência."
            )
        if left["filters_json"] != right["filters_json"]:
            warnings.append(
                "Os lotes possuem filtros de origem diferentes; entrada e saída podem "
                "refletir cobertura."
            )
        return {
            "competencia_a": competence_a,
            "competencia_b": competence_b,
            "lote_a": left["id"],
            "lote_b": right["id"],
            "gatilhos": triggers,
            "avisos": warnings,
        }

    def score_leads(
        self,
        competence_a: str,
        competence_b: str,
        weights: Mapping[str, float],
        filters: Mapping[str, Any],
        limit: int,
        batch_a: str | None = None,
        batch_b: str | None = None,
    ) -> dict[str, Any]:
        left, right = (
            self._batch_for_competence(competence_a, batch_a),
            self._batch_for_competence(competence_b, batch_b),
        )
        clauses, filter_params = self._filters(filters)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        porte = float(weights["porte"])
        complexity = float(weights["complexidade"])
        payer = float(weights["mix_pagador"])
        trend = float(weights["tendencia"])
        with duckdb.connect() as connection:
            cursor = connection.execute(
                f"""
                WITH prior AS (
                    SELECT cnes, leitos_existentes,
                           CAST(campos_ausentes AS VARCHAR[]) AS campos_ausentes
                    FROM read_parquet(?)
                ), current AS (
                    SELECT * EXCLUDE (campos_ausentes),
                           CAST(campos_ausentes AS VARCHAR[]) AS campos_ausentes
                    FROM read_parquet(?) {where}
                ), raw AS (
                    SELECT
                        c.*,
                        CASE WHEN c.leitos_uti_adulto IS NOT NULL
                                  AND c.leitos_uti_pediatrica IS NOT NULL
                                  AND c.leitos_uti_neonatal IS NOT NULL
                            THEN c.leitos_uti_adulto + c.leitos_uti_pediatrica
                                + c.leitos_uti_neonatal
                        END AS leitos_uti,
                        CASE
                            WHEN list_contains(c.campos_ausentes, 'leitos_existentes')
                                THEN NULL
                            WHEN p.cnes IS NOT NULL AND list_contains(
                                p.campos_ausentes, 'leitos_existentes'
                            ) THEN NULL
                            ELSE c.leitos_existentes - COALESCE(p.leitos_existentes, 0)
                        END AS delta_leitos
                    FROM current c LEFT JOIN prior p USING (cnes)
                ), percentiles AS (
                    SELECT *,
                        CASE WHEN NOT list_contains(
                            campos_ausentes, 'leitos_existentes'
                        ) THEN 100 * cume_dist() OVER (ORDER BY leitos_existentes) END
                            AS score_porte,
                        CASE
                            WHEN leitos_uti IS NULL THEN NULL
                            WHEN MAX(leitos_uti) OVER () = 0 THEN 0
                            ELSE 100 * percent_rank() OVER (ORDER BY leitos_uti)
                        END AS score_complexidade_uti,
                        CASE
                            WHEN MAX(total_habilitacoes) OVER () = 0 THEN 0
                            ELSE 100 * percent_rank() OVER (ORDER BY total_habilitacoes)
                        END AS score_complexidade_habilitacoes,
                        CASE WHEN leitos_existentes > 0
                                  AND leitos_sus BETWEEN 0 AND leitos_existentes
                                  AND NOT list_contains(
                                      campos_ausentes, 'leitos_sus'
                                  ) THEN
                            100 * (1 - least(leitos_sus, leitos_existentes)
                                / leitos_existentes::DOUBLE)
                        END AS score_mix_pagador,
                        CASE WHEN delta_leitos IS NOT NULL THEN
                            100 * cume_dist() OVER (ORDER BY delta_leitos)
                        END
                            AS score_tendencia
                    FROM raw
                ), dimensions AS (
                    SELECT *,
                        CASE WHEN score_complexidade_uti IS NULL
                            THEN score_complexidade_habilitacoes
                            ELSE (
                                score_complexidade_uti
                                + score_complexidade_habilitacoes
                            ) / 2
                        END AS score_complexidade
                    FROM percentiles
                ), totals AS (
                    SELECT *,
                        CASE WHEN (
                            CASE WHEN score_porte IS NULL THEN 0 ELSE ? END
                            + CASE WHEN score_complexidade IS NULL THEN 0 ELSE ? END
                            + CASE WHEN score_tendencia IS NULL THEN 0 ELSE ? END
                            + CASE WHEN score_mix_pagador IS NULL THEN 0 ELSE ? END
                        ) > 0
                        THEN (
                            COALESCE(score_porte * ?, 0)
                            + COALESCE(score_complexidade * ?, 0)
                            + COALESCE(score_tendencia * ?, 0)
                            + COALESCE(score_mix_pagador * ?, 0)
                        ) / (
                            CASE WHEN score_porte IS NULL THEN 0 ELSE ? END
                            + CASE WHEN score_complexidade IS NULL THEN 0 ELSE ? END
                            + CASE WHEN score_tendencia IS NULL THEN 0 ELSE ? END
                            + CASE WHEN score_mix_pagador IS NULL THEN 0 ELSE ? END
                        ) END AS score_total
                    FROM dimensions
                )
                SELECT cnes, nome_fantasia, razao_social, cnpj, cnpj_mantenedora,
                       municipio, uf, tipo_estabelecimento, leitos_existentes,
                       leitos_sus, leitos_uti, total_habilitacoes, delta_leitos,
                       round(score_porte, 4) AS score_porte,
                       round(score_complexidade_uti, 4) AS score_complexidade_uti,
                       round(score_complexidade_habilitacoes, 4)
                            AS score_complexidade_habilitacoes,
                       round(score_complexidade, 4) AS score_complexidade,
                       round(score_mix_pagador, 4) AS score_mix_pagador,
                       round(score_tendencia, 4) AS score_tendencia,
                       round(score_total, 4) AS score_total,
                       list_concat(
                           campos_ausentes,
                           CASE WHEN score_porte IS NULL
                               THEN ['score_porte'] ELSE []::VARCHAR[] END,
                           CASE WHEN score_complexidade_uti IS NULL
                               THEN ['score_complexidade_uti'] ELSE []::VARCHAR[] END,
                           CASE WHEN score_mix_pagador IS NULL
                               THEN ['score_mix_pagador'] ELSE []::VARCHAR[] END,
                           CASE WHEN score_tendencia IS NULL
                               THEN ['score_tendencia'] ELSE []::VARCHAR[] END
                       ) AS campos_ausentes
                FROM totals ORDER BY score_total DESC NULLS LAST, cnes LIMIT ?
                """,
                [
                    left["parquet_path"],
                    right["parquet_path"],
                    *filter_params,
                    porte,
                    complexity,
                    trend,
                    payer,
                    porte,
                    complexity,
                    trend,
                    payer,
                    porte,
                    complexity,
                    trend,
                    payer,
                    limit,
                ],
            )
            leads = self._dict_rows(cursor)
        warnings = []
        if left["filters_json"] != right["filters_json"]:
            warnings.append(
                "Os lotes possuem filtros de origem diferentes; tendência e entrada podem "
                "refletir cobertura."
            )
        return {
            "lote_a": str(left["id"]),
            "lote_b": str(right["id"]),
            "leads": leads,
            "avisos": warnings,
        }
