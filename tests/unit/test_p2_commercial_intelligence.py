from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from mcp import Client

from mcp_cnes.domain.models import HospitalInfoV2
from mcp_cnes.infrastructure.config import Settings
from mcp_cnes.infrastructure.persistence.duckdb import (
    STORAGE_COLUMNS,
    DuckDBCNESRepository,
)
from mcp_cnes.interfaces.mcp import create_mcp_server


def hospital(
    cnes: str,
    competence: str,
    beds: int,
    sus_beds: int,
    uti_beds: int,
    maintainer: str,
    *,
    uf: str = "AM",
    habilitacoes: tuple[str, ...] = (),
) -> HospitalInfoV2:
    return HospitalInfoV2(
        cnes=cnes,
        nome_fantasia=f"Hospital {cnes}",
        razao_social=f"Empresa Hospitalar {cnes}",
        cnpj=f"{int(cnes):014d}",
        cnpj_mantenedora=maintainer,
        tipo_pessoa="J",
        municipio="Manaus" if uf == "AM" else "Belém",
        uf=uf,
        tipo_estabelecimento="HOSPITAL GERAL",
        natureza_juridica="SOCIEDADE EMPRESARIA",
        gestao="M",
        convenio_sus=sus_beds > 0,
        leitos_existentes=beds,
        leitos_sus=sus_beds,
        competencia=competence,
        logradouro="Avenida Principal",
        numero="100",
        bairro="Centro",
        cep="69000000",
        telefone="9230000000",
        email="contato@hospital.example",
        leitos_uti_adulto=uti_beds,
        leitos_uti_pediatrica=0,
        leitos_uti_neonatal=0,
        habilitacoes=habilitacoes,
        total_habilitacoes=len(habilitacoes),
    )


def register_batch(
    repository: DuckDBCNESRepository,
    tmp_path: Path,
    competence: str,
    hospitals: list[HospitalInfoV2],
    *,
    filters: dict[str, object] | None = None,
) -> str:
    rows = []
    for item in hospitals:
        row = item.to_dict()
        row["municipio_normalizado"] = item.municipio.casefold()
        rows.append(row)
    frame = pd.DataFrame.from_records(rows, columns=STORAGE_COLUMNS)
    parquet = tmp_path / f"fixture-{competence}.parquet"
    with duckdb.connect() as connection:
        connection.register("fixture", frame)
        connection.execute("COPY fixture TO ? (FORMAT PARQUET)", [str(parquet)])
    return repository.register_parquet_batch(
        parquet,
        source_file=parquet.name,
        source="datasus_base_completa",
        competence=competence,
        filters=filters or {"uf": "AM"},
        records=len(hospitals),
        etag=f"etag-{competence}",
        contract_version="v2",
        resource_version=f"fixture-{competence}",
    )


def prepared_repository(tmp_path: Path) -> DuckDBCNESRepository:
    repository = DuckDBCNESRepository(
        tmp_path / "cnes.duckdb",
        columnar_dir=tmp_path / "parquet",
        batch_retention_count=5,
    )
    register_batch(
        repository,
        tmp_path,
        "202501",
        [
            hospital("0000001", "202501", 100, 70, 10, "11111111000100"),
            hospital("0000002", "202501", 50, 50, 0, "22222222000100"),
        ],
    )
    register_batch(
        repository,
        tmp_path,
        "202502",
        [
            hospital(
                "0000001",
                "202502",
                130,
                60,
                30,
                "11111111000100",
                habilitacoes=("ONCOLOGIA",),
            ),
            hospital(
                "0000003",
                "202502",
                80,
                0,
                5,
                "11111111000100",
                uf="PA",
                habilitacoes=("CARDIOLOGIA", "NEUROLOGIA", "TRANSPLANTES"),
            ),
        ],
    )
    return repository


def test_repository_groups_triggers_and_scores_without_hidden_weights(
    tmp_path: Path,
) -> None:
    repository = prepared_repository(tmp_path)

    group_result = repository.group_by_maintainer({}, 10)
    groups = group_result["redes"]
    triggers = repository.lead_triggers("202501", "202502", 20)
    score_by_size_result = repository.score_leads(
        "202501",
        "202502",
        {"porte": 1, "complexidade": 0, "mix_pagador": 0, "tendencia": 0},
        {},
        10,
    )
    score_by_payer_result = repository.score_leads(
        "202501",
        "202502",
        {"porte": 0, "complexidade": 0, "mix_pagador": 1, "tendencia": 0},
        {},
        10,
    )
    score_by_complexity_result = repository.score_leads(
        "202501",
        "202502",
        {"porte": 0, "complexidade": 1, "mix_pagador": 0, "tendencia": 0},
        {},
        10,
    )

    score_by_size = score_by_size_result["leads"]
    score_by_payer = score_by_payer_result["leads"]
    score_by_complexity = score_by_complexity_result["leads"]

    assert group_result["lote_id"] is not None
    assert groups == [
        {
            "cnpj_mantenedora": "11111111000100",
            "rede": None,
            "unidades": 2,
            "leitos_existentes": 210,
            "leitos_sus": 60,
            "mix_sus": pytest.approx(60 / 210, abs=1e-6),
            "mix_nao_sus": pytest.approx(150 / 210, abs=1e-6),
            "distribuicao_uf": {"AM": 1, "PA": 1},
            "campos_ausentes": ["nome_mantenedora"],
            "alertas": [],
        }
    ]
    assert {item["motivo"] for item in triggers["gatilhos"]} == {
        "expansao",
        "entrada",
        "saida",
    }
    assert score_by_size[0]["cnes"] == "0000001"
    assert score_by_payer[0]["cnes"] == "0000003"
    assert score_by_size[0]["score_total"] == score_by_size[0]["score_porte"]
    assert score_by_payer[0]["score_total"] == score_by_payer[0]["score_mix_pagador"]
    complexity_by_cnes = {item["cnes"]: item for item in score_by_complexity}
    assert complexity_by_cnes["0000003"]["total_habilitacoes"] == 3
    assert complexity_by_cnes["0000003"]["score_complexidade_habilitacoes"] == 100
    assert complexity_by_cnes["0000001"]["score_complexidade_uti"] == 100
    assert all(item["score_total"] == item["score_complexidade"] for item in score_by_complexity)
    assert score_by_size_result["lote_a"] == triggers["lote_a"]
    assert score_by_size_result["lote_b"] == triggers["lote_b"]
    assert score_by_size_result["avisos"] == []


def test_delta_min_applies_to_every_trigger_reason(tmp_path: Path) -> None:
    repository = prepared_repository(tmp_path)

    result = repository.lead_triggers("202501", "202502", 100)

    assert result["gatilhos"] == []


def test_competence_comparison_requires_explicit_batch_when_ambiguous(
    tmp_path: Path,
) -> None:
    repository = prepared_repository(tmp_path)
    extra_batch = register_batch(
        repository,
        tmp_path,
        "202501",
        [hospital("0000004", "202501", 40, 20, 0, "33333333000100")],
        filters={"uf": "PA"},
    )
    batches = {
        item["lote_id"]: item
        for item in repository.list_batches()
        if item["competencia"] == "202501"
    }
    original_batch = next(identifier for identifier in batches if identifier != extra_batch)
    right_batch = next(
        item["lote_id"]
        for item in repository.list_batches()
        if item["competencia"] == "202502"
    )

    with pytest.raises(ValueError, match="Informe lote_a/lote_b explicitamente"):
        repository.lead_triggers("202501", "202502", 1)

    result = repository.score_leads(
        "202501",
        "202502",
        {"porte": 1, "complexidade": 0, "mix_pagador": 0, "tendencia": 0},
        {},
        10,
        original_batch,
        right_batch,
    )
    assert result["lote_a"] == original_batch
    assert result["lote_b"] == right_batch
    assert result["avisos"] == []


def test_comparison_warns_about_different_source_filters(tmp_path: Path) -> None:
    repository = DuckDBCNESRepository(
        tmp_path / "cnes.duckdb", columnar_dir=tmp_path / "parquet"
    )
    left = register_batch(
        repository,
        tmp_path,
        "202501",
        [hospital("0000001", "202501", 10, 5, 0, "11111111000100")],
        filters={"uf": "AM"},
    )
    right = register_batch(
        repository,
        tmp_path,
        "202502",
        [hospital("0000001", "202502", 20, 5, 0, "11111111000100")],
        filters={"uf": "PA"},
    )

    result = repository.score_leads(
        "202501",
        "202502",
        {"porte": 1, "complexidade": 0, "mix_pagador": 0, "tendencia": 0},
        {},
        10,
        left,
        right,
    )

    assert result["avisos"]


def test_invalid_sus_bed_total_invalidates_dataset_and_mix(tmp_path: Path) -> None:
    repository = DuckDBCNESRepository(
        tmp_path / "cnes.duckdb", columnar_dir=tmp_path / "parquet"
    )
    batch_id = register_batch(
        repository,
        tmp_path,
        "202502",
        [hospital("0000001", "202502", 10, 20, 0, "11111111000100")],
    )

    validation = repository.validate_dataset(batch_id)
    group = repository.group_by_maintainer({}, 10, batch_id)["redes"][0]

    assert validation["leitos_invalidos"] == 1
    assert validation["valido"] is False
    assert group["mix_sus"] is None
    assert group["mix_nao_sus"] is None
    assert group["alertas"] == ["leitos_sus_maior_que_leitos_existentes"]


@pytest.mark.asyncio
async def test_p2_tools_and_crm_jsonl_export(tmp_path: Path) -> None:
    repository = prepared_repository(tmp_path)
    output_dir = tmp_path / "exports"
    server = create_mcp_server(
        settings=Settings(
            database_path=tmp_path / "unused.sqlite3",
            output_dir=output_dir,
        ),
        repository=repository,
    )

    async with Client(server) as client:
        grouped = await client.call_tool("cnes_group_by_mantenedora", {})
        triggered = await client.call_tool(
            "cnes_leads_triggers",
            {
                "competencia_a": "202501",
                "competencia_b": "202502",
                "delta_min": 20,
            },
        )
        scored = await client.call_tool(
            "cnes_score_leads",
            {
                "competencia_a": "202501",
                "competencia_b": "202502",
                "pesos": {
                    "porte": 1,
                    "complexidade": 1,
                    "mix_pagador": 1,
                    "tendencia": 1,
                },
            },
        )
        exported = await client.call_tool(
            "cnes_export",
            {
                "formato": "jsonl",
                "perfil_saida": "crm_generico",
                "limit": 2,
            },
        )

    assert grouped.is_error is False
    assert grouped.structured_content["lote_id"] is not None
    assert grouped.structured_content["redes"][0]["unidades"] == 2
    assert triggered.is_error is False
    assert scored.is_error is False
    assert scored.structured_content["lote_a"] == triggered.structured_content["lote_a"]
    assert scored.structured_content["lote_b"] == triggered.structured_content["lote_b"]
    assert scored.structured_content["avisos"] == []
    assert scored.structured_content["campos_ausentes"] == []
    assert scored.structured_content["leads"][0]["total_habilitacoes"] >= 1
    assert exported.is_error is False
    assert exported.structured_content["registros"] == 2

    rows = [
        json.loads(line)
        for line in Path(exported.structured_content["filepath"])
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(rows) == 2
    assert rows[0]["chave_deduplicacao"] == f"{rows[0]['cnes']}:{rows[0]['cnpj']}"
    assert rows[0]["_metadados"]["versao_contrato"] == "v2"
    assert rows[0]["_metadados"]["perfil_saida"] == "crm_generico"
    assert "cpf" not in json.dumps(rows, ensure_ascii=False).casefold()
