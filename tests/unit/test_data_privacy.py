from pathlib import Path


def test_production_queries_and_exports_do_not_collect_professional_cns() -> None:
    root = Path(__file__).parents[2]
    production_files = [root / "cnes_scraper.py", *(root / "src").rglob("*.py")]

    violations = [
        str(path.relative_to(root))
        for path in production_files
        if "PROFISSIONAL_CNS" in path.read_text(encoding="utf-8")
    ]

    assert violations == []
