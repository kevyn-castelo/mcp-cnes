from __future__ import annotations

from pathlib import Path

from mcp_cnes.domain.models import HospitalInfo
from mcp_cnes.infrastructure.persistence import DuckDBCNESRepository


def hospital(cnes: str, beds: int) -> HospitalInfo:
    return HospitalInfo(
        cnes=cnes,
        nome_fantasia=f"Hospital {cnes}",
        municipio="Manaus",
        uf="AM",
        tipo_estabelecimento="HOSPITAL GERAL",
        natureza_juridica="SOCIEDADE EMPRESARIA",
        gestao="M",
        convenio_sus=True,
        leitos_existentes=beds,
        leitos_sus=beds // 2,
        competencia="202501",
    )


def test_duckdb_repository_keeps_v1_projection_in_parquet(tmp_path: Path) -> None:
    repository = DuckDBCNESRepository(
        tmp_path / "cnes.duckdb", columnar_dir=tmp_path / "parquet"
    )
    batch_id = repository.replace_all(
        [hospital("0000001", 20), hospital("0000002", 80)], "fixture.csv"
    )

    rows, total = repository.advanced_search(
        {"municipio": "Manaus"}, "leitos_existentes", 0, 1
    )
    validation = repository.validate_dataset(batch_id)

    assert total == 2
    assert rows[0].cnes == "0000002"
    assert tuple(rows[0].to_dict()) == (
        "cnes", "nome_fantasia", "municipio", "uf", "tipo_estabelecimento",
        "natureza_juridica", "gestao", "convenio_sus", "leitos_existentes",
        "leitos_sus", "competencia",
    )
    assert validation["cnes_duplicados"] == 0
    assert validation["competencias_mistas"] is False
    assert validation["valido"] is True
