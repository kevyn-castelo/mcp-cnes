from benchmarks.benchmark_sqlite_import import run


def test_benchmark_exercises_the_real_import_pipeline() -> None:
    result = run(50)

    assert result["rows_requested"] == 50
    assert result["rows_read"] == 50
    assert result["rows_loaded"] == 50
    assert result["rows_persisted"] == 50
    assert result["source_size_mib"] > 0
    assert result["peak_python_memory_mib"] > 0
    assert result["staging_strategy"] == "temporary SQLite"
    assert result["pipeline"] == [
        "SecureCsvImporter",
        "CsvCNESImporter",
        "LoadData",
        "SQLiteCNESRepository",
    ]
