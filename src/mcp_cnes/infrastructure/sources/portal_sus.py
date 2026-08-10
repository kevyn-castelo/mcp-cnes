"""Descoberta e normalização streaming dos arquivos do Portal SUS."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
import time
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any
from urllib.parse import urlparse

from mcp_cnes.domain.errors import CollectorError
from mcp_cnes.domain.remote import (
    RemoteCompetenceResult,
    RemoteFetchRequest,
    RemoteFetchResult,
    SourceResource,
)
from mcp_cnes.domain.rules import (
    is_within_bed_range,
    normalize_column_name,
    normalize_search_text,
    parse_non_negative_int,
)
from mcp_cnes.infrastructure.config import Settings

from .http import ResilientHttpClient

CANONICAL_COLUMNS = (
    "COMPETENCIA", "UF", "MUNICIPIO", "CNES", "NOME_FANTASIA",
    "TIPO_ESTABELECIMENTO", "NATUREZA_JURIDICA", "GESTAO", "CONVENIO_SUS",
    "LEITOS_EXISTENTES", "LEITOS_SUS",
)
REQUIRED_SOURCE_COLUMNS = {
    "COMP", "UF", "MUNICIPIO", "CNES", "NOME_ESTABELECIMENTO", "TP_GESTAO",
    "DS_TIPO_UNIDADE", "NATUREZA_JURIDICA", "LEITOS_EXISTENTES", "LEITOS_SUS",
}


class PortalSUSRemoteSource:
    """Usa o HTML oficial como catálogo e processa artefatos sem materializá-los."""

    name = "portal_sus_hospitais_leitos"

    def __init__(
        self,
        settings: Settings,
        *,
        session: Any | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self._http = ResilientHttpClient(settings, session=session, sleeper=sleeper)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._resources: tuple[SourceResource, ...] | None = None
        self._resources_checked_at: datetime | None = None
        self._competences: dict[str, tuple[str, ...]] = {}

    def list_resources(self) -> tuple[SourceResource, ...]:
        now = self._clock()
        if (
            self._resources is not None
            and self._resources_checked_at is not None
            and (now - self._resources_checked_at).total_seconds()
            <= self.settings.remote_cache_ttl_seconds
        ):
            return self._resources
        self._validate_catalog_url(self.settings.remote_catalog_url)
        response = self._http.get(
            self.settings.remote_catalog_url,
            validate_url=self._is_allowed_catalog,
        )
        try:
            self._validate_catalog_url(
                str(getattr(response, "url", self.settings.remote_catalog_url))
            )
            html = self._decode_response(response)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        resources = self._parse_resources(html)
        if not resources:
            raise CollectorError(
                "remote_catalog_empty", "catalog_parse",
                "O catálogo oficial não publicou recursos CSV de leitos",
            )
        self._resources = resources
        self._resources_checked_at = now
        self._competences.clear()
        return resources

    def list_competences(self, year: int | None = None) -> RemoteCompetenceResult:
        resources = self.list_resources()
        selected_year = year if year is not None else max(item.year for item in resources)
        resource = self._select_resource(resources, selected_year)
        version = hashlib.sha256(
            f"{resource.resource_id}\0{resource.last_modified or ''}".encode()
        ).hexdigest()[:16]
        memory_key = f"{selected_year}:{version}"
        cached = self._competences.get(memory_key)
        if cached is None:
            cache = self.settings.remote_cache_dir / f"competences-{version}.json"
            cached = self._read_competence_cache(cache, selected_year)
            if cached is None:
                downloaded, _ = self._download(resource)
                try:
                    scanned = self._scan_competences(downloaded)
                finally:
                    downloaded.unlink(missing_ok=True)
                cached = tuple(
                    competence
                    for competence in scanned
                    if self._is_competence_for_year(competence, selected_year)
                )
                if not cached:
                    raise CollectorError(
                        "remote_competence_unavailable",
                        "remote_normalize",
                        (
                            "O recurso oficial não contém competências mensais "
                            f"para {selected_year}"
                        ),
                        status_code=404,
                    )
                self._write_json_atomic(
                    cache,
                    {"year": selected_year, "competences": list(cached)},
                )
            self._competences[memory_key] = cached
        return RemoteCompetenceResult(year=selected_year, competences=cached)

    def fetch(
        self, request: RemoteFetchRequest, destination: Path | None = None
    ) -> RemoteFetchResult:
        resource = self._resource_for_year(int(request.competence[:4]))
        output_dir = self._resolve_destination(destination)
        output_dir.mkdir(parents=True, exist_ok=True)
        cache_key = self._cache_key(resource, request)
        output = output_dir / f"cnes-{request.competence}-{cache_key[:12]}.csv"
        metadata = self.settings.remote_cache_dir / f"{cache_key}.json"
        cached = self._read_cache(metadata, output, int(request.competence[:4]))
        if cached is not None:
            return cached
        downloaded, etag = self._download(resource)
        try:
            records = self._normalize(downloaded, request, output)
        finally:
            downloaded.unlink(missing_ok=True)
        result = RemoteFetchResult(
            filepath=output,
            source=self.name,
            competence=request.competence,
            records=records,
            native_filters=(),
            local_filters=self._local_filters(request),
            missing_fields=(),
            derived_fields=("CONVENIO_SUS",),
            from_cache=False,
            resource_id=resource.resource_id,
            etag=etag,
        )
        self._write_cache(metadata, result)
        return result

    def purge_cache(self) -> tuple[int, int]:
        removed = released = 0
        for root in (self.settings.remote_dir, self.settings.remote_cache_dir):
            base = root.resolve(strict=False)
            if not base.exists():
                continue
            for path in base.rglob("*"):
                resolved = path.resolve(strict=False)
                try:
                    resolved.relative_to(base)
                except ValueError:
                    continue
                if not resolved.is_file() or resolved.suffix.casefold() not in {
                    ".csv", ".json", ".tmp", ".zip"
                }:
                    continue
                size = resolved.stat().st_size
                resolved.unlink()
                removed += 1
                released += size
        self._resources = None
        self._resources_checked_at = None
        self._competences.clear()
        return removed, released

    def _resource_for_year(self, year: int) -> SourceResource:
        return self._select_resource(self.list_resources(), year)

    @staticmethod
    def _select_resource(
        resources: tuple[SourceResource, ...], year: int
    ) -> SourceResource:
        candidates = [item for item in resources if item.year == year]
        if not candidates:
            raise CollectorError(
                "remote_competence_unavailable", "catalog_select",
                f"A fonte oficial não publicou arquivo CSV para {year}", status_code=404,
            )
        return max(candidates, key=lambda item: item.last_modified or "")

    def _parse_resources(self, html: str) -> tuple[SourceResource, ...]:
        decoded = json.JSONDecoder()
        candidates: list[dict[str, Any]] = []
        for match in re.finditer(r'"resources"\s*:', html):
            try:
                value, _ = decoded.raw_decode(html[match.end() :].lstrip())
            except json.JSONDecodeError:
                continue
            if isinstance(value, list):
                candidates.extend(item for item in value if isinstance(item, dict))
        resources: dict[tuple[int, str], SourceResource] = {}
        for item in candidates:
            url = str(item.get("url", ""))
            if (
                str(item.get("state", "active")) != "active"
                or str(item.get("format", "")).upper() != "CSV"
                or not self._is_allowed_download(url)
            ):
                continue
            year_match = re.search(
                r"(?:19|20)\d{2}", str(item.get("name", "")) + " " + url
            )
            if year_match is None:
                continue
            year = int(year_match.group())
            resource = SourceResource(
                source=self.name,
                resource_id=str(item.get("id", "")),
                name=str(item.get("name", f"Leitos {year}")),
                format="CSV",
                url=url,
                year=year,
                last_modified=(str(item["last_modified"]) if item.get("last_modified") else None),
            )
            resources[year, resource.resource_id] = resource
        return tuple(sorted(resources.values(), key=lambda item: (item.year, item.name)))

    def _download(self, resource: SourceResource) -> tuple[Path, str | None]:
        if not self._is_allowed_download(resource.url):
            raise CollectorError(
                "remote_url_not_allowed", "remote_security",
                "O catálogo retornou uma URL fora do domínio oficial permitido",
            )
        self.settings.remote_cache_dir.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            prefix=".download-", suffix=".tmp", dir=self.settings.remote_cache_dir
        )
        os.close(descriptor)
        temporary = Path(name)
        try:
            result = self._http.download(
                resource.url,
                temporary,
                validate_url=self._is_allowed_download,
            )
            if not self._is_allowed_download(result.final_url):
                raise CollectorError(
                    "remote_redirect_not_allowed", "remote_security",
                    "O download oficial redirecionou para um domínio não permitido",
                )
            etag = result.headers.get("ETag")
            if etag is not None and (len(etag) > 512 or any(char in etag for char in "\r\n")):
                raise CollectorError(
                    "remote_etag_invalid", "remote_security",
                    "A fonte oficial retornou um ETag inválido",
                )
            return temporary, etag
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def _normalize(
        self, source: Path, request: RemoteFetchRequest, output: Path
    ) -> int:
        records = 0
        temporary: Path | None = None
        with self._csv_reader(source) as reader:
            source_columns = {
                normalize_column_name(name) for name in (reader.fieldnames or [])
            }
            missing = sorted(REQUIRED_SOURCE_COLUMNS - source_columns)
            if missing:
                raise CollectorError(
                    "remote_schema_mismatch", "remote_normalize",
                    "O recurso oficial não contém colunas obrigatórias: " + ", ".join(missing),
                )
            try:
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", newline="", prefix=f".{output.name}.",
                    suffix=".tmp", dir=output.parent, delete=False,
                ) as handle:
                    temporary = Path(handle.name)
                    fieldnames: list[str] = list(CANONICAL_COLUMNS)
                    writer = csv.DictWriter(handle, fieldnames=fieldnames)
                    writer.writeheader()
                    for raw in reader:
                        row = self._normalize_row(raw, request)
                        if row is not None:
                            writer.writerow(row)
                            records += 1
                    handle.flush()
                    os.fsync(handle.fileno())
                if records == 0:
                    raise CollectorError(
                        "remote_competence_not_found", "remote_filter",
                        "A competência e os filtros informados não retornaram registros",
                        status_code=404,
                    )
                temporary.replace(output)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return records

    def _normalize_row(
        self, raw: dict[str | None, str | None], request: RemoteFetchRequest
    ) -> dict[str, Any] | None:
        row = {
            normalize_column_name(key): (value or "").strip()
            for key, value in raw.items() if key is not None
        }
        if row.get("COMP") != request.competence:
            return None
        if request.uf and row.get("UF", "").upper() != request.uf:
            return None
        if request.municipality and normalize_search_text(request.municipality) not in normalize_search_text(row.get("MUNICIPIO", "")):
            return None
        establishment_type = row.get("DS_TIPO_UNIDADE", "")
        if request.establishment_type and normalize_search_text(request.establishment_type) not in normalize_search_text(establishment_type):
            return None
        existing = parse_non_negative_int(row.get("LEITOS_EXISTENTES"), "LEITOS_EXISTENTES")
        sus = parse_non_negative_int(row.get("LEITOS_SUS"), "LEITOS_SUS")
        if not is_within_bed_range(existing, request.min_beds, request.max_beds):
            return None
        nature = " - ".join(
            part for part in (row.get("NATUREZA_JURIDICA", ""), row.get("DESC_NATUREZA_JURIDICA", "")) if part
        )
        canonical_type = " - ".join(
            part for part in (row.get("CO_TIPO_UNIDADE", ""), establishment_type) if part
        )
        return {
            "COMPETENCIA": request.competence, "UF": row.get("UF", ""),
            "MUNICIPIO": row.get("MUNICIPIO", ""), "CNES": row.get("CNES", ""),
            "NOME_FANTASIA": row.get("NOME_ESTABELECIMENTO", ""),
            "TIPO_ESTABELECIMENTO": canonical_type, "NATUREZA_JURIDICA": nature,
            "GESTAO": row.get("TP_GESTAO", ""), "CONVENIO_SUS": "Sim" if sus > 0 else "Não",
            "LEITOS_EXISTENTES": existing, "LEITOS_SUS": sus,
        }

    @contextmanager
    def _open_csv_binary(self, source: Path) -> Iterator[IO[bytes]]:
        if not zipfile.is_zipfile(source):
            with source.open("rb") as handle:
                yield handle
            return
        with zipfile.ZipFile(source) as archive:
            members = [
                item for item in archive.infolist()
                if not item.is_dir() and item.filename.casefold().endswith(".csv")
            ]
            if len(members) != 1:
                raise CollectorError(
                    "remote_archive_invalid", "remote_normalize",
                    "O ZIP oficial deve conter exatamente um CSV",
                )
            member = members[0]
            if member.file_size > self.settings.remote_max_download_bytes * 5:
                raise CollectorError(
                    "remote_archive_too_large", "remote_security",
                    "O CSV descompactado excede o limite de segurança",
                )
            with archive.open(member) as handle:
                yield handle

    @contextmanager
    def _csv_reader(self, source: Path) -> Iterator[Any]:
        with self._open_csv_binary(source) as binary:
            sample_bytes = binary.read(65_536)
        try:
            sample = sample_bytes.decode("utf-8-sig")
            encoding = "utf-8-sig"
        except UnicodeDecodeError:
            sample = sample_bytes.decode("latin-1")
            encoding = "latin-1"
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=";,|").delimiter
        except csv.Error:
            delimiter = ";"
        with self._open_csv_binary(source) as binary:
            with io.TextIOWrapper(binary, encoding=encoding, newline="") as text:
                yield csv.DictReader(text, delimiter=delimiter)

    def _scan_competences(self, source: Path) -> tuple[str, ...]:
        found: set[str] = set()
        with self._csv_reader(source) as reader:
            fields = {normalize_column_name(name): name for name in reader.fieldnames or []}
            field = fields.get("COMP") or fields.get("COMPETENCIA")
            if field is None:
                raise CollectorError(
                    "remote_schema_mismatch", "remote_normalize",
                    "O recurso oficial não contém a coluna de competência",
                )
            for row in reader:
                value = (row.get(field) or "").strip()
                if len(value) == 6 and value.isdigit() and 1 <= int(value[4:]) <= 12:
                    found.add(value)
        return tuple(sorted(found))

    def _read_cache(
        self, metadata: Path, output: Path, year: int
    ) -> RemoteFetchResult | None:
        if not metadata.is_file() or not output.is_file():
            return None
        try:
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            fetched_at = datetime.fromisoformat(str(payload["fetched_at"]))
            if year >= self._clock().year and (
                self._clock() - fetched_at
            ).total_seconds() > self.settings.remote_cache_ttl_seconds:
                return None
            if self._file_sha256(output) != payload["sha256"]:
                return None
            return RemoteFetchResult(
                filepath=output, source=str(payload["source"]),
                competence=str(payload["competence"]), records=int(payload["records"]),
                native_filters=tuple(payload["native_filters"]),
                local_filters=tuple(payload["local_filters"]),
                missing_fields=tuple(payload["missing_fields"]),
                derived_fields=tuple(payload["derived_fields"]), from_cache=True,
                resource_id=str(payload["resource_id"]),
                etag=str(payload["etag"]) if payload.get("etag") is not None else None,
            )
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _write_cache(self, metadata: Path, result: RemoteFetchResult) -> None:
        payload = {
            "source": result.source, "competence": result.competence,
            "records": result.records, "native_filters": list(result.native_filters),
            "local_filters": list(result.local_filters),
            "missing_fields": list(result.missing_fields),
            "derived_fields": list(result.derived_fields),
            "resource_id": result.resource_id, "etag": result.etag,
            "fetched_at": self._clock().isoformat(),
            "sha256": self._file_sha256(result.filepath),
        }
        self._write_json_atomic(metadata, payload)

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _read_competence_cache(
        cls, path: Path, expected_year: int
    ) -> tuple[str, ...] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                cached_year = value.get("year")
                if (
                    isinstance(cached_year, bool)
                    or not isinstance(cached_year, int)
                    or cached_year != expected_year
                ):
                    return None
                competences = value.get("competences")
            else:
                competences = value
            if (
                not isinstance(competences, list)
                or not competences
                or not all(isinstance(item, str) for item in competences)
                or not all(
                    cls._is_competence_for_year(item, expected_year)
                    for item in competences
                )
            ):
                return None
            return tuple(sorted(set(competences)))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _is_competence_for_year(value: str, year: int) -> bool:
        return (
            len(value) == 6
            and value.isdigit()
            and int(value[:4]) == year
            and 1 <= int(value[4:]) <= 12
        )

    @staticmethod
    def _write_json_atomic(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _resolve_destination(self, destination: Path | None) -> Path:
        base = self.settings.remote_dir.resolve(strict=False)
        candidate = base if destination is None else destination
        if not candidate.is_absolute():
            candidate = base / candidate
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(base)
        except ValueError as exc:
            raise CollectorError(
                "remote_destination_not_allowed", "remote_security",
                "O destino deve permanecer dentro do diretório remoto configurado",
            ) from exc
        return resolved

    def _is_allowed_download(self, url: str) -> bool:
        parsed = urlparse(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == self.settings.remote_download_host
            and parsed.path.startswith(self.settings.remote_download_path_prefix)
        )

    @staticmethod
    def _validate_catalog_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "dadosabertos.saude.gov.br":
            raise CollectorError(
                "remote_catalog_not_allowed", "remote_security",
                "O catálogo remoto deve usar o domínio oficial do Portal SUS",
            )

    @staticmethod
    def _is_allowed_catalog(url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme == "https" and parsed.hostname == "dadosabertos.saude.gov.br"

    @staticmethod
    def _decode_response(response: Any) -> str:
        try:
            return bytes(response.content).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CollectorError(
                "remote_catalog_encoding", "catalog_parse",
                "O catálogo oficial retornou HTML inválido",
            ) from exc

    @staticmethod
    def _cache_key(resource: SourceResource, request: RemoteFetchRequest) -> str:
        payload = {
            "source": resource.source, "resource_id": resource.resource_id,
            "competence": request.competence, "uf": request.uf,
            "municipality": request.municipality,
            "establishment_type": request.establishment_type,
            "min_beds": request.min_beds, "max_beds": request.max_beds,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _local_filters(request: RemoteFetchRequest) -> tuple[str, ...]:
        names = ["competencia"]
        for name, value in (
            ("uf", request.uf), ("municipio", request.municipality),
            ("tipo_estabelecimento", request.establishment_type),
            ("min_leitos", request.min_beds), ("max_leitos", request.max_beds),
        ):
            if value is not None:
                names.append(name)
        return tuple(names)
