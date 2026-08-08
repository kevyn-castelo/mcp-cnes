from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from mcp_cnes.application import LoadData, SearchByMunicipality
from mcp_cnes.infrastructure.importers import CsvCNESImporter, SecureCsvImporter
from mcp_cnes.infrastructure.persistence import SQLiteCNESRepository


def test_csv_to_sqlite_to_use_case_is_idempotent(tmp_path: Path) -> None:
    fixtures = Path(__file__).parents[1] / "fixtures" / "csv"
    database = tmp_path / "cnes.sqlite3"
    repository = SQLiteCNESRepository(database)
    importer = SecureCsvImporter(
        CsvCNESImporter(), fixtures, 1024, allowed_files=("valid.csv",)
    )
    use_case = LoadData(repository, importer)

    first = use_case.execute(Path("valid.csv"))
    second = use_case.execute(Path("valid.csv"))
    result = SearchByMunicipality(repository).execute("Manaus")

    assert first.batch_id == second.batch_id
    assert [hospital.cnes for hospital in result.items] == ["1234567"]
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0] == 1
