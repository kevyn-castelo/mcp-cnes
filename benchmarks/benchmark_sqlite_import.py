"""Benchmark reproduzivel da importacao SQLite sem fixture gigante em disco."""

from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Iterator, Sequence
from contextlib import closing
from pathlib import Path

from mcp_cnes.domain.models import HospitalInfo, LoadSummary
from mcp_cnes.infrastructure.persistence import SQLiteCNESRepository


class VirtualHospitals(Sequence[HospitalInfo]):
    def __init__(self, size: int) -> None:
        self.size = size

    def __len__(self) -> int:
        return self.size

    def __getitem__(self, index: int) -> HospitalInfo:
        if index < 0:
            index += self.size
        if not 0 <= index < self.size:
            raise IndexError(index)
        return self._hospital(index)

    def __iter__(self) -> Iterator[HospitalInfo]:
        return (self._hospital(index) for index in range(self.size))

    @staticmethod
    def _hospital(index: int) -> HospitalInfo:
        uf = ("AM", "PA", "SP", "RS")[index % 4]
        municipality = ("Manaus", "Belém", "São Paulo", "Porto Alegre")[index % 4]
        return HospitalInfo(
            cnes=f"{index:07d}",
            nome_fantasia=f"Hospital {index}",
            municipio=municipality,
            uf=uf,
            leitos_existentes=20 + index % 300,
            leitos_sus=10 + index % 150,
            competencia="202607",
        )


def run(rows: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="mcp-cnes-benchmark-") as temporary:
        database = Path(temporary) / "cnes.sqlite3"
        repository = SQLiteCNESRepository(database)
        hospitals = VirtualHospitals(rows)
        summary = LoadSummary(rows, rows, 0, 0)
        tracemalloc.start()
        started = time.perf_counter()
        repository.replace_all(
            hospitals,
            "generated.csv",
            summary=summary,
            batch_id=f"benchmark-{rows}",
        )
        duration = time.perf_counter() - started
        _, peak_memory = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        with closing(sqlite3.connect(database)) as connection:
            persisted = connection.execute("SELECT COUNT(*) FROM establishments").fetchone()[0]
        return {
            "rows_requested": rows,
            "rows_persisted": persisted,
            "duration_seconds": round(duration, 3),
            "peak_python_memory_mib": round(peak_memory / (1024 * 1024), 3),
            "memory_method": "tracemalloc; excludes native SQLite page cache",
            "database_size_mib": round(database.stat().st_size / (1024 * 1024), 3),
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
