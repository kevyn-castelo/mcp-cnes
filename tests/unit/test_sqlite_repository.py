from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mcp_cnes.domain.models import HospitalInfo, LoadSummary, RejectionReason
from mcp_cnes.infrastructure.persistence import SQLiteCNESRepository
from mcp_cnes.infrastructure.persistence.sqlite import MIGRATION_1, SCHEMA_VERSION


def hospital(cnes: str, municipality: str = "Manaus", uf: str = "AM", beds: int = 50):
    return HospitalInfo(
        cnes=cnes,
        nome_fantasia=f"Hospital {cnes}",
        municipio=municipality,
        uf=uf,
        leitos_existentes=beds,
        leitos_sus=beds // 2,
        competencia="202607",
    )


def test_schema_is_versioned_queries_are_limited_and_indexes_are_used(tmp_path: Path) -> None:
    database = tmp_path / "cnes.sqlite3"
    repository = SQLiteCNESRepository(database)
    repository.replace_all(
        [
            hospital("0000001"),
            hospital("0000002", beds=75),
            hospital("0000003", "Belém", "PA", 80),
        ],
        "fixture.csv",
        batch_id="batch-one",
    )

    assert [item.cnes for item in repository.search_by_municipality("Mán", 50, 100, 1)] == [
        "0000001"
    ]
    assert repository.count_by_municipality("Mana", 50, 100) == 2
    assert repository.count_by_municipality("naus", 50, 100) == 2
    assert len(repository.search_by_uf("AM", None, None, 1)) == 1
    assert repository.count_by_uf("AM", None, None) == 2
    found = repository.get_by_cnes("0000003")
    assert found is not None
    assert found.municipio == "Belém"

    plans = repository.explain_search_plans()
    municipality_plan = " ".join(plans["municipality"])
    assert "VIRTUAL TABLE INDEX" in municipality_plan
    assert not any(
        step.startswith(("SCAN e ", "SCAN establishments "))
        for step in plans["municipality"]
    )
    assert "idx_establishments_uf_beds" in " ".join(plans["uf"])
    assert "idx_establishments_cnes" in " ".join(plans["cnes"])

    with closing(sqlite3.connect(database)) as connection, connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(establishments)")
        }
    assert {
        "idx_establishments_cnes",
        "idx_establishments_uf_beds",
        "idx_establishments_municipality_beds",
        "idx_establishments_competence",
    }.issubset(indexes)


def test_migrates_version_one_database_and_builds_municipality_search_index(
    tmp_path: Path,
) -> None:
    database = tmp_path / "cnes.sqlite3"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.executescript(MIGRATION_1)
        connection.execute("PRAGMA user_version = 1")

    repository = SQLiteCNESRepository(database)
    repository.replace_all([hospital("0000001")], "fixture.csv", batch_id="migrated")

    assert repository.count_by_municipality("Mana", None, None) == 1
    with closing(sqlite3.connect(database)) as connection, connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        table = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'establishments_municipality_fts'"
        ).fetchone()
    assert table is not None
    assert "tokenize='trigram'" in table[0]


def test_reimporting_same_batch_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "cnes.sqlite3"
    repository = SQLiteCNESRepository(database)
    items = [hospital("0000001")]

    first = repository.replace_all(items, "first.csv", batch_id="same-batch")
    second = repository.replace_all(items, "second.csv", batch_id="same-batch")

    assert first == second == "same-batch"
    with closing(sqlite3.connect(database)) as connection, connection:
        assert connection.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM staging_establishments").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM establishments").fetchone()[0] == 1


def test_failure_during_projection_rolls_back_entire_import(tmp_path: Path) -> None:
    database = tmp_path / "cnes.sqlite3"
    repository = SQLiteCNESRepository(database)
    repository.replace_all([hospital("0000001")], "stable.csv", batch_id="stable")

    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            """
            CREATE TRIGGER reject_failure BEFORE INSERT ON establishments
            WHEN NEW.cnes = 'FAIL'
            BEGIN SELECT RAISE(ABORT, 'injected failure'); END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        repository.replace_all([hospital("FAIL")], "broken.csv", batch_id="broken")

    assert repository.get_by_cnes("0000001") is not None
    assert repository.get_by_cnes("FAIL") is None
    with closing(sqlite3.connect(database)) as connection, connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM import_batches WHERE id = 'broken'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM staging_establishments WHERE batch_id = 'broken'"
        ).fetchone()[0] == 0


def test_batch_audit_stores_only_aggregated_rejection_reasons(tmp_path: Path) -> None:
    database = tmp_path / "cnes.sqlite3"
    repository = SQLiteCNESRepository(database)
    summary = LoadSummary(
        1,
        2,
        1,
        0,
        rejection_reasons=(RejectionReason("valor_invalido", 1),),
    )
    repository.replace_all(
        [hospital("0000001")], "C:/private/person-name.csv", summary=summary, batch_id="audit"
    )

    with closing(sqlite3.connect(database)) as connection, connection:
        row = connection.execute(
            "SELECT source_file, accepted_count, rejected_count, rejection_reasons "
            "FROM import_batches WHERE id = 'audit'"
        ).fetchone()
    assert row == ("person-name.csv", 1, 1, '[{"code":"valor_invalido","count":1}]')


def test_completed_batch_retention_purges_old_audit_and_staging_rows(tmp_path: Path) -> None:
    database = tmp_path / "cnes.sqlite3"
    repository = SQLiteCNESRepository(database, batch_retention_count=2)
    started = datetime(2026, 8, 1, tzinfo=UTC)

    for index in range(3):
        repository.replace_all(
            [hospital(f"000000{index + 1}")],
            f"batch-{index + 1}.csv",
            loaded_at=started + timedelta(minutes=index),
            batch_id=f"batch-{index + 1}",
        )

    with closing(sqlite3.connect(database)) as connection, connection:
        batches = {
            row[0] for row in connection.execute("SELECT id FROM import_batches")
        }
        staging_batches = {
            row[0]
            for row in connection.execute("SELECT DISTINCT batch_id FROM staging_establishments")
        }
        active_batch = connection.execute(
            "SELECT DISTINCT batch_id FROM establishments"
        ).fetchone()[0]
    assert batches == {"batch-2", "batch-3"}
    assert staging_batches == batches
    assert active_batch == "batch-3"


def test_reimporting_retained_inactive_batch_reactivates_its_projection(
    tmp_path: Path,
) -> None:
    repository = SQLiteCNESRepository(
        tmp_path / "cnes.sqlite3", batch_retention_count=2
    )
    repository.replace_all([hospital("0000001")], "first.csv", batch_id="first")
    repository.replace_all([hospital("0000002")], "second.csv", batch_id="second")

    repository.replace_all([hospital("0000001")], "first.csv", batch_id="first")

    assert repository.get_by_cnes("0000001") is not None
    assert repository.get_by_cnes("0000002") is None


def test_search_items_and_total_are_returned_by_one_snapshot_query(tmp_path: Path) -> None:
    repository = SQLiteCNESRepository(tmp_path / "cnes.sqlite3")
    repository.replace_all(
        [hospital("0000001"), hospital("0000002"), hospital("0000003", "Belem", "PA")],
        "fixture.csv",
        batch_id="snapshot",
    )

    municipality_items, municipality_total = repository.search_by_municipality_with_count(
        "Mana", None, None, 1
    )
    uf_items, uf_total = repository.search_by_uf_with_count("AM", None, None, 1)

    assert [item.cnes for item in municipality_items] == ["0000001"]
    assert municipality_total == 2
    assert [item.cnes for item in uf_items] == ["0000001"]
    assert uf_total == 2
