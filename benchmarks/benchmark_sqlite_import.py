"""Benchmark reproduzivel do pipeline real de importacao CSV para SQLite."""

from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import sys
import tempfile
import time
import tracemalloc
from contextlib import closing
from pathlib import Path
from typing import Any

from mcp_cnes.application import LoadData
from mcp_cnes.infrastructure.importers import CsvCNESImporter, SecureCsvImporter
from mcp_cnes.infrastructure.persistence import SQLiteCNESRepository

PIPELINE = [
    "SecureCsvImporter",
    "CsvCNESImporter",
    "LoadData",
    "SQLiteCNESRepository",
]


def _write_csv(path: Path, rows: int) -> None:
    municipalities = ("Manaus", "Belem", "Sao Paulo", "Porto Alegre")
    ufs = ("AM", "PA", "SP", "RS")
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(
            "CNES,NOME_FANTASIA,MUNICIPIO,UF,LEITOS_EXISTENTES,"
            "LEITOS_SUS,COMPETENCIA\n"
        )
        for index in range(rows):
            group = index % len(ufs)
            stream.write(
                f"{index:07d},Hospital {index},{municipalities[group]},{ufs[group]},"
                f"{20 + index % 300},{10 + index % 150},202607\n"
            )


def _database_files_size(database: Path) -> int:
    return sum(
        candidate.stat().st_size
        for candidate in (database, Path(f"{database}-wal"), Path(f"{database}-shm"))
        if candidate.exists()
    )


def run(rows: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mcp-cnes-benchmark-") as temporary:
        data_dir = Path(temporary)
        source = data_dir / "generated.csv"
        database = data_dir / "cnes.sqlite3"
        _write_csv(source, rows)
        source_size = source.stat().st_size
        repository = SQLiteCNESRepository(database)
        importer = SecureCsvImporter(
            CsvCNESImporter(),
            data_dir,
            max_size_bytes=source_size + 1,
            allowed_files=(source.name,),
        )
        load_data = LoadData(repository, importer)

        tracemalloc.start()
        started = time.perf_counter()
        summary = load_data.execute(source)
        duration = time.perf_counter() - started
        _, peak_memory = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        with closing(sqlite3.connect(database)) as connection:
            persisted = connection.execute("SELECT COUNT(*) FROM establishments").fetchone()[0]
        return {
            "rows_requested": rows,
            "rows_read": summary.rows_read,
            "rows_loaded": summary.records_loaded,
            "rows_persisted": persisted,
            "source_size_mib": round(source_size / (1024 * 1024), 3),
            "duration_seconds": round(duration, 3),
            "peak_python_memory_mib": round(peak_memory / (1024 * 1024), 3),
            "memory_method": "tracemalloc around the complete import; excludes CSV generation",
            "database_size_mib": round(_database_files_size(database) / (1024 * 1024), 3),
            "pipeline": PIPELINE,
            "environment": {
                "python": platform.python_version(),
                "python_implementation": platform.python_implementation(),
                "sqlite": sqlite3.sqlite_version,
                "platform": platform.platform(),
                "processor": platform.processor() or "not-reported",
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=400_000)
    arguments = parser.parse_args()
    if arguments.rows < 1:
        parser.error("--rows must be greater than zero")
    json.dump(run(arguments.rows), sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
