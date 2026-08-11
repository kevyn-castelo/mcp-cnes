from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import duckdb
import pytest
from mcp import Client

from mcp_cnes.domain.errors import CollectorError
from mcp_cnes.domain.remote import RemoteFetchRequest
from mcp_cnes.infrastructure.config import Settings
from mcp_cnes.infrastructure.sources import DatasusFullRemoteSource
from mcp_cnes.interfaces.mcp import create_mcp_server

COMPETENCE = "202501"
ARCHIVE = f"BASE_DE_DADOS_CNES_{COMPETENCE}.ZIP"


def _csv(header: str, *rows: str) -> bytes:
    return (header + "\n" + "\n".join(rows) + "\n").encode("latin-1")


def full_archive(latitude: str = "-3,10", first_bed_quantity: str = "30") -> bytes:
    files = {
        f"tbEstabelecimento{COMPETENCE}.csv": _csv(
            "CO_UNIDADE;CO_CNES;CO_MUNICIPIO_GESTOR;NO_RAZAO_SOCIAL;NO_FANTASIA;"
            "NU_CNPJ;NU_CNPJ_MANTENEDORA;TP_PFPJ;NIVEL_DEP;NO_LOGRADOURO;"
            "NU_ENDERECO;NO_COMPLEMENTO;NO_BAIRRO;CO_CEP;NU_LATITUDE;NU_LONGITUDE;"
            "NU_TELEFONE;NO_EMAIL;CO_TIPO_UNIDADE;CO_NATUREZA_JUR;TP_GESTAO;NU_CPF",
            "1300001;1234567;130260;HOSPITAL EXEMPLO SA;HOSPITAL EXEMPLO;"
            "12345678000199;99887766000155;3;3;AV BRASIL;100;BLOCO A;CENTRO;"
            f"69000000;{latitude};-60,02;9233334444;CONTATO@EXEMPLO.COM;05;2062;M;",
            "1300002;7654321;130260;PESSOA SECRETA;PESSOA SECRETA;;;1;1;RUA X;1;"
            ";CENTRO;69000000;-3,1;-60,0;92999999999;pessoal@example.com;05;2062;M;"
            "12345678901",
        ),
        f"tbMunicipio{COMPETENCE}.csv": _csv(
            "CO_MUNICIPIO;NO_MUNICIPIO;CO_SIGLA_ESTADO", "130260;Manaus;AM"
        ),
        f"rlEstabComplementar{COMPETENCE}.csv": _csv(
            "CO_UNIDADE;CO_LEITO;QT_EXIST;QT_SUS",
            f"1300001;1;{first_bed_quantity};20",
            "1300001;2;50;30",
            "1300001;3;20;10",
        ),
        f"tbLeito{COMPETENCE}.csv": _csv(
            "CO_LEITO;DS_LEITO;CO_TIPO_LEITO",
            "1;UTI ADULTO;9",
            "2;CIRURGICO;1",
            "3;CLINICO;2",
        ),
        f"tbTipoLeito{COMPETENCE}.csv": _csv(
            "CO_TIPO_LEITO;DS_TIPO_LEITO",
            "9;COMPLEMENTAR",
            "1;CIRURGICO",
            "2;CLINICO",
        ),
        f"tbTipoUnidade{COMPETENCE}.csv": _csv(
            "CO_TIPO_UNIDADE;DS_TIPO_UNIDADE", "05;HOSPITAL GERAL"
        ),
        f"tbNaturezaJuridica{COMPETENCE}.csv": _csv(
            "CO_NATUREZA_JUR;DS_NATUREZA_JUR", "2062;SOCIEDADE EMPRESARIA"
        ),
        f"rlEstabSipac{COMPETENCE}.csv": _csv(
            "CO_UNIDADE;COD_SUB_GRUPO_HABILITACAO;CMTP_INICIO;CMTP_FIM",
            "1300001;0101;012020;999999",
            "1300001;0202;062023;999999",
        ),
        f"tbSubGruposHabilitacao{COMPETENCE}.csv": _csv(
            "CO_CODIGO_GRUPO;NO_DESCRICAO_GRUPO",
            "0101;ALTA COMPLEXIDADE CARDIOVASCULAR",
            "0202;UNIDADE DE ASSISTENCIA DE ALTA COMPLEXIDADE EM ONCOLOGIA",
        ),
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
    return output.getvalue()


class FakeFTP:
    retr_count = 0
    content = full_archive()

    def connect(self, host: str, timeout: int) -> None:
        assert host == "ftp.datasus.gov.br"
        assert timeout > 0

    def login(self) -> None:
        return None

    def cwd(self, directory: str) -> None:
        assert directory == "/cnes"

    def voidcmd(self, command: str) -> str:
        assert command == "TYPE I"
        return "200"

    def nlst(self) -> list[str]:
        return [ARCHIVE, "DICIONARIO_DE_DADOS.ZIP"]

    def size(self, name: str) -> int:
        assert name == ARCHIVE
        return len(self.content)

    def sendcmd(self, command: str) -> str:
        assert command == f"MDTM {ARCHIVE}"
        return "213 20250201010203"

    def retrbinary(self, command: str, callback, blocksize: int) -> None:
        assert command == f"RETR {ARCHIVE}"
        type(self).retr_count += 1
        for index in range(0, len(self.content), blocksize):
            callback(self.content[index : index + blocksize])

    def quit(self) -> None:
        return None

    def close(self) -> None:
        return None


def settings(tmp_path: Path) -> Settings:
    return Settings(
        remote_dir=tmp_path / "remote",
        remote_cache_dir=tmp_path / "cache",
        datasus_max_download_bytes=10 * 1024 * 1024,
    )


def test_full_source_builds_allowlisted_v2_parquet_and_reuses_zip(tmp_path: Path) -> None:
    FakeFTP.retr_count = 0
    source = DatasusFullRemoteSource(settings(tmp_path), ftp_factory=FakeFTP)

    assert source.list_competences(2025).competences == (COMPETENCE,)
    first = source.fetch(
        RemoteFetchRequest(
            COMPETENCE,
            municipality="Manaus",
            establishment_type="HOSPITAL",
        )
    )
    second = source.fetch(RemoteFetchRequest(COMPETENCE, municipality="Manaus"))
    cached = source.fetch(
        RemoteFetchRequest(
            COMPETENCE,
            municipality="Manaus",
            establishment_type="HOSPITAL",
        )
    )

    assert first.contract_version == "v2"
    assert first.records == 1
    assert first.etag is None
    assert first.download_cache_hit is False
    assert second.download_cache_hit is True
    assert cached.from_cache is True
    assert FakeFTP.retr_count == 1
    with duckdb.connect() as connection:
        row = connection.execute("SELECT * FROM read_parquet(?)", [str(first.filepath)]).fetchone()
        columns = [item[0] for item in connection.description]
    assert row is not None
    record = dict(zip(columns, row, strict=True))
    assert record["razao_social"] == "HOSPITAL EXEMPLO SA"
    assert record["cnpj"] == "12345678000199"
    assert record["cnpj_mantenedora"] == "99887766000155"
    assert record["telefone"] == "9233334444"
    assert record["geo_confiavel"] is True
    assert record["leitos_existentes"] == 100
    assert record["leitos_uti_adulto"] == 30
    assert record["leitos_complementares"] == 30
    assert record["total_habilitacoes"] == 2
    assert record["habilitacoes"] == [
        "ALTA COMPLEXIDADE CARDIOVASCULAR",
        "UNIDADE DE ASSISTENCIA DE ALTA COMPLEXIDADE EM ONCOLOGIA",
    ]
    assert record["campos_ausentes"] == []
    assert record["cnes"] == "1234567"
    assert "cpf" not in {name.casefold() for name in columns}


def test_full_source_rebuilds_corrupt_parquet_without_redownloading_zip(
    tmp_path: Path,
) -> None:
    FakeFTP.retr_count = 0
    source = DatasusFullRemoteSource(settings(tmp_path), ftp_factory=FakeFTP)
    request = RemoteFetchRequest(COMPETENCE, municipality="Manaus")
    first = source.fetch(request)
    first.filepath.write_bytes(b"not-a-parquet")

    rebuilt = source.fetch(request)

    assert rebuilt.from_cache is False
    assert rebuilt.download_cache_hit is True
    assert FakeFTP.retr_count == 1
    with duckdb.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM read_parquet(?)", [str(rebuilt.filepath)]
        ).fetchone() == (1,)


def test_full_source_rebuilds_cache_with_unexpected_parquet_schema(tmp_path: Path) -> None:
    FakeFTP.retr_count = 0
    configured = settings(tmp_path)
    source = DatasusFullRemoteSource(configured, ftp_factory=FakeFTP)
    request = RemoteFetchRequest(COMPETENCE, municipality="Manaus")
    first = source.fetch(request)
    first.filepath.unlink()
    with duckdb.connect() as connection:
        connection.execute(
            "COPY (SELECT 1 AS unexpected) TO ? (FORMAT PARQUET)",
            [str(first.filepath)],
        )
    metadata_path = next(configured.remote_cache_dir.glob("full-result-*.json"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["sha256"] = hashlib.sha256(first.filepath.read_bytes()).hexdigest()
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    rebuilt = source.fetch(request)

    assert rebuilt.from_cache is False
    assert FakeFTP.retr_count == 1
    with duckdb.connect() as connection:
        columns = connection.execute(
            "SELECT * FROM read_parquet(?) LIMIT 0", [str(rebuilt.filepath)]
        ).description
    assert "cnes" in {item[0] for item in columns}
    assert "unexpected" not in {item[0] for item in columns}


def test_full_source_purge_removes_download_and_normalized_cache(tmp_path: Path) -> None:
    FakeFTP.retr_count = 0
    configured = settings(tmp_path)
    source = DatasusFullRemoteSource(configured, ftp_factory=FakeFTP)
    result = source.fetch(RemoteFetchRequest(COMPETENCE))

    removed, released = source.purge_cache()

    assert removed >= 3
    assert released > 0
    assert result.filepath.exists() is False
    assert list(configured.remote_cache_dir.iterdir()) == []


@pytest.mark.parametrize(
    "destination_factory",
    [
        lambda configured, tmp_path: tmp_path / "outside",
        lambda configured, tmp_path: Path(".."),
    ],
    ids=["absolute", "parent-traversal"],
)
def test_full_source_rejects_destination_outside_remote_root(
    tmp_path: Path, destination_factory
) -> None:
    configured = settings(tmp_path)
    source = DatasusFullRemoteSource(configured, ftp_factory=FakeFTP)

    with pytest.raises(CollectorError, match="diretório remoto configurado") as raised:
        source.fetch(
            RemoteFetchRequest(COMPETENCE),
            destination_factory(configured, tmp_path),
        )

    assert raised.value.code == "remote_destination_not_allowed"
    assert not (tmp_path / "outside").exists()


def test_full_source_rejects_symlink_escape_from_remote_root(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    configured.remote_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = configured.remote_dir / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink indisponível neste ambiente: {exc}")
    source = DatasusFullRemoteSource(configured, ftp_factory=FakeFTP)

    with pytest.raises(CollectorError, match="diretório remoto configurado") as raised:
        source.fetch(RemoteFetchRequest(COMPETENCE), Path("escape"))

    assert raised.value.code == "remote_destination_not_allowed"


def test_full_source_marks_out_of_state_coordinate_without_dropping_values(
    tmp_path: Path,
) -> None:
    content = full_archive("-30,0")
    original = FakeFTP.content
    FakeFTP.content = content
    FakeFTP.retr_count = 0
    try:
        result = DatasusFullRemoteSource(settings(tmp_path), ftp_factory=FakeFTP).fetch(
            RemoteFetchRequest(COMPETENCE)
        )
    finally:
        FakeFTP.content = original
    with duckdb.connect() as connection:
        coordinate_row = connection.execute(
            "SELECT latitude, geo_confiavel FROM read_parquet(?)", [str(result.filepath)]
        ).fetchone()
    assert coordinate_row is not None
    latitude, reliable = coordinate_row
    assert latitude == -30.0
    assert reliable is False


def test_full_source_marks_invalid_bed_quantity_as_absent(tmp_path: Path) -> None:
    original = FakeFTP.content
    FakeFTP.content = full_archive(first_bed_quantity="")
    FakeFTP.retr_count = 0
    try:
        result = DatasusFullRemoteSource(settings(tmp_path), ftp_factory=FakeFTP).fetch(
            RemoteFetchRequest(COMPETENCE)
        )
    finally:
        FakeFTP.content = original

    with duckdb.connect() as connection:
        row = connection.execute(
            "SELECT leitos_existentes, leitos_uti_adulto, campos_ausentes FROM read_parquet(?)",
            [str(result.filepath)],
        ).fetchone()
    assert row is not None
    legacy_total, uti_adult, missing = row
    assert legacy_total == 0
    assert uti_adult is None
    assert "leitos_existentes" in missing
    assert "leitos_uti_adulto" in missing


@pytest.mark.asyncio
async def test_full_source_loads_v2_without_changing_v1_projection(tmp_path: Path) -> None:
    source = DatasusFullRemoteSource(settings(tmp_path), ftp_factory=FakeFTP)
    server = create_mcp_server(
        settings=Settings(
            database_path=tmp_path / "legacy.sqlite3",
            columnar_database_path=tmp_path / "cnes.duckdb",
            columnar_dir=tmp_path / "parquet",
            remote_dir=tmp_path / "remote",
            remote_cache_dir=tmp_path / "cache",
            datasus_max_download_bytes=10 * 1024 * 1024,
        ),
        remote_sources={source.name: source},
    )

    async with Client(server) as client:
        fetched = await client.call_tool(
            "cnes_fetch",
            {"competencia": COMPETENCE, "fonte": source.name},
        )
        legacy = await client.call_tool("cnes_search_advanced", {"limit": 1})
        enriched = await client.call_tool("cnes_search_advanced_v2", {"limit": 1})
        validation = await client.call_tool("cnes_validate_dataset", {})

    assert fetched.is_error is False
    assert legacy.is_error is False
    assert enriched.is_error is False
    legacy_row = legacy.structured_content["estabelecimentos"][0]
    v2_row = enriched.structured_content["estabelecimentos"][0]
    assert set(legacy_row) == {
        "competencia",
        "uf",
        "municipio",
        "cnes",
        "nome_fantasia",
        "tipo_estabelecimento",
        "natureza_juridica",
        "gestao",
        "convenio_sus",
        "leitos_existentes",
        "leitos_sus",
    }
    assert v2_row["razao_social"] == "HOSPITAL EXEMPLO SA"
    assert v2_row["cnpj_mantenedora"] == "99887766000155"
    assert v2_row["leitos_uti_adulto"] == 30
    assert v2_row["total_habilitacoes"] == 2
    assert len(v2_row["habilitacoes"]) == 2
    assert "cpf" not in str(enriched.structured_content).casefold()
    assert validation.structured_content["cnes_duplicados"] == 0
    assert validation.structured_content["competencias_mistas"] is False
    assert validation.structured_content["valido"] is True
