"""Fonte mensal BASE_DE_DADOS_CNES do FTP oficial do DATASUS."""

from __future__ import annotations

import csv
import ftplib
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import zipfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import duckdb

from mcp_cnes.domain.errors import CollectorError
from mcp_cnes.domain.geo import UF_BOUNDS
from mcp_cnes.domain.remote import (
    RemoteCompetenceResult,
    RemoteFetchRequest,
    RemoteFetchResult,
    SourceResource,
)
from mcp_cnes.domain.rules import normalize_search_text
from mcp_cnes.infrastructure.config import Settings

ARCHIVE_PATTERN = re.compile(r"^BASE_DE_DADOS_CNES_(\d{6})\.ZIP$", re.IGNORECASE)
NORMALIZER_VERSION = "v2.3"
MEMBER_PREFIXES = {
    "estabelecimento": "tbestabelecimento",
    "municipio": "tbmunicipio",
    "leitos": "rlestabcomplementar",
    "leito": "tbleito",
    "tipo_leito": "tbtipoleito",
    "tipo_unidade": "tbtipounidade",
    "natureza": "tbnaturezajuridica",
    "habilitacoes": "rlestabsipac",
    "habilitacao": "tbsubgruposhabilitacao",
}
OPTIONAL_MEMBER_PREFIXES = {"tipo_leito"}

ESTABLISHMENT_FIELDS: Mapping[str, tuple[str, ...]] = {
    "co_unidade": ("CO_UNIDADE",),
    "co_cnes": ("CO_CNES",),
    "municipio": ("CO_MUNICIPIO_GESTOR", "CO_MUNICIPIO"),
    "razao_social": ("NO_RAZAO_SOCIAL",),
    "nome_fantasia": ("NO_FANTASIA",),
    "cnpj": ("NU_CNPJ",),
    "cnpj_mantenedora": ("NU_CNPJ_MANTENEDORA",),
    "tipo_pessoa": ("TP_PFPJ",),
    "nivel_dependencia": ("NIVEL_DEP",),
    "logradouro": ("NO_LOGRADOURO",),
    "numero": ("NU_ENDERECO",),
    "complemento": ("NO_COMPLEMENTO",),
    "bairro": ("NO_BAIRRO",),
    "cep": ("CO_CEP",),
    "latitude": ("NU_LATITUDE",),
    "longitude": ("NU_LONGITUDE",),
    "telefone": ("NU_TELEFONE",),
    "email": ("NO_EMAIL",),
    "tipo_unidade": ("TP_UNIDADE", "CO_TIPO_UNIDADE", "CO_TIPO_ESTABELECIMENTO"),
    "natureza": ("CO_NATUREZA_JUR", "CO_NATUREZA_JURIDICA"),
    "gestao": ("TP_GESTAO",),
}
TABLE_FIELDS: Mapping[str, Mapping[str, tuple[str, ...]]] = {
    "municipio": {
        "codigo": ("CO_MUNICIPIO",),
        "nome": ("NO_MUNICIPIO",),
        "uf": ("CO_SIGLA_ESTADO", "SG_UF"),
    },
    "leitos": {
        "co_unidade": ("CO_UNIDADE",),
        "co_leito": ("CO_LEITO",),
        "qt_exist": ("QT_EXIST",),
        "qt_sus": ("QT_SUS",),
    },
    "leito": {
        "co_leito": ("CO_LEITO",),
        "descricao": ("DS_LEITO",),
        "tipo": ("CO_TIPO_LEITO", "TP_LEITO"),
    },
    "tipo_leito": {
        "codigo": ("CO_TIPO_LEITO",),
        "descricao": ("DS_TIPO_LEITO",),
    },
    "tipo_unidade": {
        "codigo": ("CO_TIPO_UNIDADE",),
        "descricao": ("DS_TIPO_UNIDADE",),
    },
    "natureza": {
        "codigo": ("CO_NATUREZA_JUR", "CO_NATUREZA_JURIDICA"),
        "descricao": ("DS_NATUREZA_JUR", "DS_NATUREZA_JURIDICA"),
    },
    "habilitacoes": {
        "co_unidade": ("CO_UNIDADE",),
        "codigo": ("COD_SUB_GRUPO_HABILITACAO",),
        "inicio": ("CMTP_INICIO",),
        "fim": ("CMTP_FIM",),
    },
    "habilitacao": {
        "codigo": ("CO_CODIGO_GRUPO",),
        "descricao": ("NO_DESCRICAO_GRUPO",),
    },
}


class DatasusFullRemoteSource:
    """Baixa um ZIP mensal e projeta somente campos institucionais em Parquet."""

    name = "datasus_base_completa"

    def __init__(
        self,
        settings: Settings,
        *,
        ftp_factory: Callable[[], Any] = ftplib.FTP,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self._ftp_factory = ftp_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._resources: tuple[SourceResource, ...] | None = None
        self._resources_checked_at: datetime | None = None
        self._download_lock = threading.Lock()

    def _open_ftp(self) -> Any:
        try:
            ftp = self._ftp_factory()
            ftp.connect(self.settings.datasus_ftp_host, timeout=self.settings.request_timeout)
            ftp.login()
            ftp.cwd(self.settings.datasus_ftp_directory)
            ftp.voidcmd("TYPE I")
            return ftp
        except (OSError, ftplib.Error) as exc:
            raise CollectorError(
                "datasus_ftp_unavailable",
                "ftp_connect",
                f"O FTP oficial do DATASUS está indisponível: {exc}",
                retryable=True,
            ) from exc

    def list_resources(self) -> tuple[SourceResource, ...]:
        now = self._clock()
        if (
            self._resources is not None
            and self._resources_checked_at is not None
            and (now - self._resources_checked_at).total_seconds()
            <= self.settings.remote_cache_ttl_seconds
        ):
            return self._resources
        ftp = self._open_ftp()
        try:
            names = ftp.nlst()
            resources: list[SourceResource] = []
            for raw_name in names:
                name = PurePosixPath(raw_name).name
                match = ARCHIVE_PATTERN.fullmatch(name)
                if match is None:
                    continue
                competence = match.group(1)
                modified = self._mdtm(ftp, name)
                resources.append(
                    SourceResource(
                        source=self.name,
                        resource_id=name,
                        name=f"Base completa CNES {competence}",
                        format="ZIP",
                        url=(
                            f"ftp://{self.settings.datasus_ftp_host}"
                            f"{self.settings.datasus_ftp_directory.rstrip('/')}/{name}"
                        ),
                        year=int(competence[:4]),
                        last_modified=modified,
                    )
                )
        except (OSError, ftplib.Error) as exc:
            raise CollectorError(
                "datasus_catalog_unavailable",
                "ftp_list",
                f"Não foi possível listar as bases completas do DATASUS: {exc}",
                retryable=True,
            ) from exc
        finally:
            try:
                ftp.quit()
            except (OSError, ftplib.Error):
                ftp.close()
        if not resources:
            raise CollectorError(
                "datasus_catalog_empty",
                "ftp_list",
                "O FTP oficial não publicou arquivos BASE_DE_DADOS_CNES mensais",
            )
        self._resources = tuple(sorted(resources, key=lambda item: item.resource_id))
        self._resources_checked_at = now
        return self._resources

    def list_competences(self, year: int | None = None) -> RemoteCompetenceResult:
        resources = self.list_resources()
        selected_year = year if year is not None else max(item.year for item in resources)
        competences = tuple(
            sorted(
                match.group(1)
                for item in resources
                if item.year == selected_year
                if (match := ARCHIVE_PATTERN.fullmatch(item.resource_id)) is not None
            )
        )
        if not competences:
            raise CollectorError(
                "remote_competence_unavailable",
                "catalog_select",
                f"A base completa não publicou competência para {selected_year}",
                status_code=404,
            )
        return RemoteCompetenceResult(selected_year, competences)

    def fetch(
        self, request: RemoteFetchRequest, destination: Path | None = None
    ) -> RemoteFetchResult:
        resource = self._resource(request.competence)
        size, modified = self._remote_version(resource.resource_id)
        resource_version = f"size={size};mdtm={modified or ''}"
        archive, download_hit = self._download(resource.resource_id, size, resource_version)
        output_dir = self._destination(destination)
        output_dir.mkdir(parents=True, exist_ok=True)
        cache_key = hashlib.sha256(
            json.dumps(
                {
                    "resource": resource_version,
                    "normalizer": NORMALIZER_VERSION,
                    "filters": request.__dict__,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        output = output_dir / f"cnes-completa-{request.competence}-{cache_key[:12]}.parquet"
        metadata_path = self.settings.remote_cache_dir / f"full-result-{cache_key}.json"
        cached_records = self._cached_result(metadata_path, output, resource_version)
        if cached_records is not None:
            return self._result(
                output,
                request,
                resource,
                cached_records,
                resource_version,
                from_cache=True,
                download_cache_hit=True,
            )
        try:
            with tempfile.TemporaryDirectory(prefix="mcp-cnes-full-") as temporary:
                members = self._extract_allowlisted(archive, Path(temporary), request.competence)
                records = self._build_parquet(members, request, output)
        except CollectorError:
            raise
        except (OSError, ValueError, zipfile.BadZipFile, duckdb.Error) as exc:
            output.unlink(missing_ok=True)
            raise CollectorError(
                "datasus_normalization_failed",
                "full_normalize",
                f"A base completa não pôde ser normalizada: {exc}",
            ) from exc
        self._write_json_atomic(
            metadata_path,
            {
                "resource_version": resource_version,
                "records": records,
                "sha256": self._sha256_file(output),
                "columns": self._parquet_columns(output),
            },
        )
        return self._result(
            output,
            request,
            resource,
            records,
            resource_version,
            from_cache=False,
            download_cache_hit=download_hit,
        )

    def _resource(self, competence: str) -> SourceResource:
        name = f"BASE_DE_DADOS_CNES_{competence}.ZIP"
        for resource in self.list_resources():
            if resource.resource_id.casefold() == name.casefold():
                return resource
        raise CollectorError(
            "remote_competence_unavailable",
            "catalog_select",
            f"A base completa não publicou a competência {competence}",
            status_code=404,
        )

    def _remote_version(self, name: str) -> tuple[int, str | None]:
        ftp = self._open_ftp()
        try:
            size = ftp.size(name)
            if size is None:
                raise ftplib.error_reply("SIZE sem resposta")
            return int(size), self._mdtm(ftp, name)
        except (OSError, ftplib.Error) as exc:
            raise CollectorError(
                "datasus_resource_unavailable",
                "ftp_metadata",
                f"Não foi possível validar o arquivo mensal {name}: {exc}",
                retryable=True,
            ) from exc
        finally:
            try:
                ftp.quit()
            except (OSError, ftplib.Error):
                ftp.close()

    @staticmethod
    def _mdtm(ftp: Any, name: str) -> str | None:
        try:
            response = ftp.sendcmd(f"MDTM {name}")
        except ftplib.Error:
            return None
        return response.removeprefix("213 ").strip() or None

    def _download(self, name: str, size: int, resource_version: str) -> tuple[Path, bool]:
        if size > self.settings.datasus_max_download_bytes:
            raise CollectorError(
                "datasus_download_too_large",
                "ftp_download",
                f"O ZIP mensal possui {size} bytes e excede o limite configurado",
            )
        self.settings.remote_cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.settings.remote_cache_dir / name
        metadata = target.with_suffix(".metadata.json")
        with self._download_lock:
            cached = self._read_json(metadata)
            if (
                target.is_file()
                and target.stat().st_size == size
                and cached.get("resource_version") == resource_version
                and zipfile.is_zipfile(target)
            ):
                return target, True
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target.stem}-", suffix=".zip", dir=target.parent
            )
            os.close(descriptor)
            temporary = Path(temporary_name)
            written = 0
            ftp = self._open_ftp()
            try:
                with temporary.open("wb") as handle:

                    def consume(chunk: bytes) -> None:
                        nonlocal written
                        written += len(chunk)
                        if written > self.settings.datasus_max_download_bytes:
                            raise CollectorError(
                                "datasus_download_too_large",
                                "ftp_download",
                                "O download ultrapassou o limite configurado",
                            )
                        handle.write(chunk)

                    ftp.retrbinary(f"RETR {name}", consume, blocksize=1024 * 1024)
                    handle.flush()
                    os.fsync(handle.fileno())
                if written != size:
                    raise CollectorError(
                        "datasus_download_incomplete",
                        "ftp_download",
                        f"Download incompleto: esperado {size}, recebido {written} bytes",
                        retryable=True,
                    )
                if not zipfile.is_zipfile(temporary):
                    raise CollectorError(
                        "datasus_download_corrupt",
                        "ftp_download",
                        "O FTP entregou o tamanho anunciado, mas o artefato não é um ZIP válido",
                        retryable=True,
                    )
                os.replace(temporary, target)
                self._write_json_atomic(metadata, {"resource_version": resource_version})
            except CollectorError:
                raise
            except (OSError, ftplib.Error) as exc:
                raise CollectorError(
                    "datasus_download_failed",
                    "ftp_download",
                    f"Falha ao baixar {name}: {exc}",
                    retryable=True,
                ) from exc
            finally:
                temporary.unlink(missing_ok=True)
                try:
                    ftp.quit()
                except (OSError, ftplib.Error):
                    ftp.close()
        return target, False

    def _extract_allowlisted(
        self, archive: Path, destination: Path, competence: str
    ) -> dict[str, Path]:
        selected: dict[str, zipfile.ZipInfo] = {}
        expected_suffix = f"{competence}.csv"
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                path = PurePosixPath(member.filename.replace("\\", "/"))
                if member.is_dir() or path.is_absolute() or ".." in path.parts:
                    continue
                compact = re.sub(r"[^a-z0-9.]", "", path.name.casefold())
                for key, prefix in MEMBER_PREFIXES.items():
                    if compact.startswith(prefix) and compact.endswith(expected_suffix.casefold()):
                        if key in selected:
                            raise CollectorError(
                                "datasus_duplicate_member",
                                "zip_validate",
                                f"O ZIP contém mais de um arquivo para {key}",
                            )
                        selected[key] = member
            required = set(MEMBER_PREFIXES) - OPTIONAL_MEMBER_PREFIXES
            missing = sorted(required - set(selected))
            if missing:
                raise CollectorError(
                    "datasus_missing_tables",
                    "zip_validate",
                    "Tabelas obrigatórias ausentes no ZIP: " + ", ".join(missing),
                )
            total_size = sum(member.file_size for member in selected.values())
            if total_size > self.settings.datasus_max_download_bytes * 6:
                raise CollectorError(
                    "datasus_extracted_too_large",
                    "zip_validate",
                    "As tabelas selecionadas excedem o limite seguro de extração",
                )
            result: dict[str, Path] = {}
            for key, member in selected.items():
                output = destination / f"{key}.csv"
                with bundle.open(member) as source, output.open("wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                result[key] = output
        return result

    def _build_parquet(
        self, members: Mapping[str, Path], request: RemoteFetchRequest, output: Path
    ) -> int:
        resolved = {
            "estabelecimento": self._resolve_fields(
                members["estabelecimento"], ESTABLISHMENT_FIELDS
            )
        }
        for table, fields in TABLE_FIELDS.items():
            if table in members:
                resolved[table] = self._resolve_fields(members[table], fields)
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.stem}-", suffix=".parquet", dir=output.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with duckdb.connect() as connection:
                connection.execute(
                    "CREATE TEMP TABLE uf_bounds(uf VARCHAR, min_lat DOUBLE, max_lat DOUBLE, "
                    "min_lon DOUBLE, max_lon DOUBLE)"
                )
                connection.executemany(
                    "INSERT INTO uf_bounds VALUES (?, ?, ?, ?, ?)",
                    [(uf, *bounds) for uf, bounds in UF_BOUNDS.items()],
                )
                self._create_views(connection, members, resolved)
                clauses, params = self._sql_filters(request)
                where = " WHERE " + " AND ".join(clauses) if clauses else ""
                connection.execute(
                    "CREATE TEMP VIEW final_unfiltered AS "
                    + self._projection_sql(request.competence)
                )
                count_row = connection.execute(
                    f"SELECT COUNT(*) FROM final_unfiltered{where}", params
                ).fetchone()
                assert count_row is not None
                records = int(count_row[0])
                connection.execute(
                    f"COPY (SELECT * FROM final_unfiltered{where}) TO {self._literal(temporary)} "
                    "(FORMAT PARQUET, COMPRESSION ZSTD)",
                    params,
                )
            os.replace(temporary, output)
            return records
        finally:
            temporary.unlink(missing_ok=True)

    def _create_views(
        self,
        connection: duckdb.DuckDBPyConnection,
        members: Mapping[str, Path],
        resolved: Mapping[str, Mapping[str, str]],
    ) -> None:
        for table, columns in resolved.items():
            selection = ", ".join(
                f"src.{self._identifier(source)} AS {self._identifier(alias)}"
                for alias, source in columns.items()
            )
            connection.execute(
                f"CREATE TEMP VIEW raw_{table} AS SELECT {selection} FROM read_csv("
                f"{self._literal(members[table])}, delim=';', header=true, "
                "all_varchar=true, encoding='latin-1', strict_mode=true) AS src"
            )
        if "tipo_leito" not in resolved:
            connection.execute(
                """
                CREATE TEMP VIEW raw_tipo_leito AS SELECT * FROM (VALUES
                    ('1', 'CIRURGICO'), ('2', 'CLINICO'), ('3', 'COMPLEMENTAR'),
                    ('4', 'OBSTETRICO'), ('5', 'PEDIATRICO'),
                    ('6', 'HOSPITAL DIA'), ('7', 'OUTRAS ESPECIALIDADES')
                ) AS mapped(codigo, descricao)
                """
            )

    @staticmethod
    def _projection_sql(competence: str) -> str:
        return f"""
            WITH bed_rows AS (
                SELECT
                    b.co_unidade,
                    try_cast(trim(b.qt_exist) AS BIGINT) AS qt_exist,
                    try_cast(trim(b.qt_sus) AS BIGINT) AS qt_sus,
                    upper(strip_accents(COALESCE(l.descricao, ''))) AS ds_leito,
                    upper(strip_accents(COALESCE(t.descricao, ''))) AS ds_tipo
                FROM raw_leitos b
                LEFT JOIN raw_leito l ON trim(l.co_leito) = trim(b.co_leito)
                LEFT JOIN raw_tipo_leito t ON trim(t.codigo) = trim(l.tipo)
            ),
            beds AS (
                SELECT
                    co_unidade,
                    CASE WHEN COUNT(qt_exist) = COUNT(*) THEN SUM(qt_exist) END
                        AS total_exist,
                    CASE WHEN COUNT(qt_sus) = COUNT(*) THEN SUM(qt_sus) END
                        AS total_sus,
                    CASE WHEN COUNT(qt_exist) = COUNT(*) THEN COALESCE(SUM(qt_exist) FILTER (
                        WHERE contains(ds_leito, 'UTI') AND contains(ds_leito, 'ADULT')
                    ), 0) END AS uti_adulto,
                    CASE WHEN COUNT(qt_exist) = COUNT(*) THEN COALESCE(SUM(qt_exist) FILTER (
                        WHERE contains(ds_leito, 'UTI') AND contains(ds_leito, 'PEDIATR')
                    ), 0) END AS uti_pediatrica,
                    CASE WHEN COUNT(qt_exist) = COUNT(*) THEN COALESCE(SUM(qt_exist) FILTER (
                        WHERE contains(ds_leito, 'UTI') AND contains(ds_leito, 'NEONAT')
                    ), 0) END AS uti_neonatal,
                    CASE WHEN COUNT(qt_exist) = COUNT(*) THEN COALESCE(
                        SUM(qt_exist) FILTER (WHERE contains(ds_tipo, 'CIRURG')), 0
                    ) END AS cirurgicos,
                    CASE WHEN COUNT(qt_exist) = COUNT(*) THEN COALESCE(
                        SUM(qt_exist) FILTER (WHERE contains(ds_tipo, 'CLINIC')), 0
                    ) END AS clinicos,
                    CASE WHEN COUNT(qt_exist) = COUNT(*) THEN COALESCE(
                        SUM(qt_exist) FILTER (WHERE contains(ds_tipo, 'OBSTET')), 0
                    ) END AS obstetricos,
                    CASE WHEN COUNT(qt_exist) = COUNT(*) THEN COALESCE(
                        SUM(qt_exist) FILTER (WHERE contains(ds_tipo, 'COMPLEMENT')), 0
                    ) END AS complementares
                FROM bed_rows GROUP BY co_unidade
            ),
            habilitacao_rows AS (
                SELECT
                    h.co_unidade,
                    h.codigo,
                    COALESCE(NULLIF(trim(d.descricao), ''), trim(h.codigo)) AS descricao
                FROM raw_habilitacoes h
                LEFT JOIN raw_habilitacao d ON trim(d.codigo) = trim(h.codigo)
                WHERE regexp_matches(trim(h.inicio), '^[0-9]{{6}}$')
                  AND right(trim(h.inicio), 4) || left(trim(h.inicio), 2) <= '{competence}'
                  AND (
                      trim(h.fim) = '999999'
                      OR (
                          regexp_matches(trim(h.fim), '^[0-9]{{6}}$')
                          AND right(trim(h.fim), 4) || left(trim(h.fim), 2) >= '{competence}'
                      )
                  )
            ),
            habilitacoes AS (
                SELECT
                    co_unidade,
                    list(DISTINCT descricao ORDER BY descricao) AS habilitacoes,
                    COUNT(DISTINCT codigo)::BIGINT AS total_habilitacoes
                FROM habilitacao_rows GROUP BY co_unidade
            ),
            establishment AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY lpad(trim(co_cnes), 7, '0') ORDER BY trim(co_unidade)
                ) AS position
                FROM raw_estabelecimento
                WHERE trim(tipo_pessoa) = '3' AND COALESCE(trim(co_cnes), '') <> ''
            ),
            typed AS (
                SELECT
                    '{competence}'::VARCHAR AS competencia,
                    COALESCE(upper(trim(m.uf)), '') AS uf,
                    COALESCE(trim(m.nome), '') AS municipio,
                    lpad(trim(e.co_cnes), 7, '0') AS cnes,
                    COALESCE(NULLIF(trim(e.nome_fantasia), ''), trim(e.razao_social)) AS nome_fantasia,
                    COALESCE(trim(tu.descricao), '') AS tipo_estabelecimento,
                    COALESCE(trim(nj.descricao), '') AS natureza_juridica,
                    COALESCE(trim(e.gestao), '') AS gestao,
                    COALESCE(b.total_exist, 0)::BIGINT AS leitos_existentes,
                    COALESCE(b.total_sus, 0)::BIGINT AS leitos_sus,
                    COALESCE(b.total_sus > 0, FALSE) AS convenio_sus,
                    NULLIF(trim(e.razao_social), '') AS razao_social,
                    NULLIF(regexp_replace(trim(e.cnpj), '[^0-9]', '', 'g'), '') AS cnpj,
                    NULLIF(regexp_replace(trim(e.cnpj_mantenedora), '[^0-9]', '', 'g'), '') AS cnpj_mantenedora,
                    'PESSOA_JURIDICA'::VARCHAR AS tipo_pessoa,
                    CASE trim(e.nivel_dependencia)
                        WHEN '1' THEN 'INDIVIDUAL'
                        WHEN '3' THEN 'MANTIDO'
                        ELSE NULLIF(trim(e.nivel_dependencia), '')
                    END AS nivel_dependencia,
                    NULLIF(trim(e.logradouro), '') AS logradouro,
                    NULLIF(trim(e.numero), '') AS numero,
                    NULLIF(trim(e.complemento), '') AS complemento,
                    NULLIF(trim(e.bairro), '') AS bairro,
                    NULLIF(regexp_replace(trim(e.cep), '[^0-9]', '', 'g'), '') AS cep,
                    try_cast(replace(trim(e.latitude), ',', '.') AS DOUBLE) AS latitude,
                    try_cast(replace(trim(e.longitude), ',', '.') AS DOUBLE) AS longitude,
                    NULLIF(trim(e.telefone), '') AS telefone,
                    NULLIF(lower(trim(e.email)), '') AS email,
                    b.uti_adulto::BIGINT AS leitos_uti_adulto,
                    b.uti_pediatrica::BIGINT AS leitos_uti_pediatrica,
                    b.uti_neonatal::BIGINT AS leitos_uti_neonatal,
                    b.cirurgicos::BIGINT AS leitos_cirurgicos,
                    b.clinicos::BIGINT AS leitos_clinicos,
                    b.obstetricos::BIGINT AS leitos_obstetricos,
                    b.complementares::BIGINT AS leitos_complementares,
                    COALESCE(h.habilitacoes, []::VARCHAR[]) AS habilitacoes,
                    COALESCE(h.total_habilitacoes, 0)::BIGINT AS total_habilitacoes,
                    list_concat(
                        CASE WHEN b.total_exist IS NULL THEN [
                            'leitos_existentes', 'leitos_uti_adulto',
                            'leitos_uti_pediatrica', 'leitos_uti_neonatal',
                            'leitos_cirurgicos', 'leitos_clinicos',
                            'leitos_obstetricos', 'leitos_complementares'
                        ] ELSE [] END,
                        CASE WHEN b.total_sus IS NULL THEN [
                            'leitos_sus', 'convenio_sus'
                        ] ELSE [] END
                    ) AS campos_ausentes
                FROM establishment e
                LEFT JOIN raw_municipio m ON trim(m.codigo) = trim(e.municipio)
                LEFT JOIN raw_tipo_unidade tu ON trim(tu.codigo) = trim(e.tipo_unidade)
                LEFT JOIN raw_natureza nj ON trim(nj.codigo) = trim(e.natureza)
                LEFT JOIN beds b ON trim(b.co_unidade) = trim(e.co_unidade)
                LEFT JOIN habilitacoes h ON trim(h.co_unidade) = trim(e.co_unidade)
                WHERE e.position = 1
            )
            SELECT
                competencia, uf, municipio, cnes, nome_fantasia,
                tipo_estabelecimento, natureza_juridica, gestao,
                leitos_existentes, leitos_sus, convenio_sus,
                razao_social, cnpj, cnpj_mantenedora, tipo_pessoa,
                nivel_dependencia, logradouro, numero, complemento, bairro, cep,
                CASE WHEN latitude BETWEEN -90 AND 90 THEN latitude ELSE NULL END AS latitude,
                CASE WHEN longitude BETWEEN -180 AND 180 THEN longitude ELSE NULL END AS longitude,
                COALESCE((
                    latitude BETWEEN bounds.min_lat AND bounds.max_lat
                    AND longitude BETWEEN bounds.min_lon AND bounds.max_lon
                ), FALSE) AS geo_confiavel,
                telefone, email, leitos_uti_adulto, leitos_uti_pediatrica,
                leitos_uti_neonatal, leitos_cirurgicos, leitos_clinicos,
                leitos_obstetricos, leitos_complementares, habilitacoes,
                total_habilitacoes, campos_ausentes,
                upper(strip_accents(municipio)) AS municipio_normalizado
            FROM typed
            LEFT JOIN uf_bounds bounds USING (uf)
        """

    @staticmethod
    def _sql_filters(request: RemoteFetchRequest) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        exact = (
            (request.uf, "uf"),
            (request.management, "gestao"),
            (request.sus_agreement, "convenio_sus"),
        )
        for value, column in exact:
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value.upper() if isinstance(value, str) else value)
        partial = (
            (request.municipality, "municipio_normalizado"),
            (request.establishment_type, "tipo_estabelecimento"),
            (request.legal_nature, "natureza_juridica"),
        )
        for value, column in partial:
            if value:
                clauses.append(f"contains(lower({column}), lower(?))")
                params.append(
                    normalize_search_text(value) if column == "municipio_normalizado" else value
                )
        if request.min_beds is not None:
            clauses.append("leitos_existentes >= ?")
            params.append(request.min_beds)
        if request.max_beds is not None:
            clauses.append("leitos_existentes <= ?")
            params.append(request.max_beds)
        return clauses, params

    @staticmethod
    def _resolve_fields(path: Path, expected: Mapping[str, Sequence[str]]) -> dict[str, str]:
        with path.open("r", encoding="latin-1", newline="") as stream:
            header = next(csv.reader(stream, delimiter=";", quotechar='"'))
        available = {name.strip().lstrip("\ufeff").upper(): name.strip() for name in header}
        resolved: dict[str, str] = {}
        missing: list[str] = []
        for canonical, aliases in expected.items():
            match = next((available[name] for name in aliases if name in available), None)
            if match is None:
                missing.append("/".join(aliases))
            else:
                resolved[canonical] = match
        if missing:
            raise CollectorError(
                "datasus_missing_fields",
                "csv_schema",
                f"{path.name} não contém campos obrigatórios: {', '.join(missing)}",
            )
        return resolved

    def _destination(self, destination: Path | None) -> Path:
        return (destination or self.settings.remote_dir).resolve(strict=False)

    @staticmethod
    def _identifier(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    @staticmethod
    def _literal(value: Path) -> str:
        return "'" + str(value).replace("'", "''").replace("\\", "/") + "'"

    def _result(
        self,
        output: Path,
        request: RemoteFetchRequest,
        resource: SourceResource,
        records: int,
        resource_version: str,
        *,
        from_cache: bool,
        download_cache_hit: bool,
    ) -> RemoteFetchResult:
        filters = (
            ("uf", request.uf),
            ("municipio", request.municipality),
            ("tipo_estabelecimento", request.establishment_type),
            ("natureza_juridica", request.legal_nature),
            ("gestao", request.management),
            ("convenio_sus", request.sus_agreement),
            ("min_leitos", request.min_beds),
            ("max_leitos", request.max_beds),
        )
        return RemoteFetchResult(
            filepath=output,
            source=self.name,
            competence=request.competence,
            records=records,
            native_filters=("tipo_pessoa=PESSOA_JURIDICA (proteção de dados)",),
            local_filters=tuple(name for name, value in filters if value is not None),
            missing_fields=("etag",),
            derived_fields=(
                "CONVENIO_SUS",
                "GEO_CONFIAVEL",
                "LEITOS_UTI_ADULTO",
                "LEITOS_UTI_PEDIATRICA",
                "LEITOS_UTI_NEONATAL",
                "LEITOS_CIRURGICOS",
                "LEITOS_CLINICOS",
                "LEITOS_OBSTETRICOS",
                "LEITOS_COMPLEMENTARES",
                "HABILITACOES_ATIVAS",
            ),
            from_cache=from_cache,
            resource_id=resource.resource_id,
            etag=None,
            download_cache_hit=download_cache_hit,
            contract_version="v2",
            resource_version=resource_version,
        )

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _cached_result(self, metadata: Path, output: Path, resource_version: str) -> int | None:
        payload = self._read_json(metadata)
        expected_columns = payload.get("columns")
        if (
            output.is_file()
            and payload.get("resource_version") == resource_version
            and isinstance(payload.get("records"), int)
            and isinstance(payload.get("sha256"), str)
            and isinstance(expected_columns, list)
            and all(isinstance(column, str) for column in expected_columns)
            and self._sha256_file(output) == payload["sha256"]
            and self._parquet_columns(output) == expected_columns
        ):
            return int(payload["records"])
        return None

    @staticmethod
    def _sha256_file(path: Path) -> str:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    @staticmethod
    def _parquet_columns(path: Path) -> list[str] | None:
        try:
            with duckdb.connect() as connection:
                connection.execute("SELECT * FROM read_parquet(?) LIMIT 0", [str(path)])
                return [str(item[0]) for item in connection.description]
        except (duckdb.Error, OSError):
            return None

    @staticmethod
    def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.stem}-", suffix=".json", dir=path.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def purge_cache(self) -> tuple[int, int]:
        removed = released = 0
        base = self.settings.remote_cache_dir.resolve(strict=False)
        if base.exists():
            for path in base.iterdir():
                if not path.is_file() or not (
                    ARCHIVE_PATTERN.fullmatch(path.name)
                    or path.name.startswith("full-result-")
                    or path.name.startswith("BASE_DE_DADOS_CNES_")
                ):
                    continue
                size = path.stat().st_size
                path.unlink()
                removed += 1
                released += size
        output_base = self.settings.remote_dir.resolve(strict=False)
        if output_base.exists():
            for path in output_base.glob("cnes-completa-*.parquet"):
                resolved = path.resolve(strict=False)
                if not resolved.is_relative_to(output_base) or not resolved.is_file():
                    continue
                size = resolved.stat().st_size
                resolved.unlink()
                removed += 1
                released += size
        self._resources = None
        self._resources_checked_at = None
        return removed, released
