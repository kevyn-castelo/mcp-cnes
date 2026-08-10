"""Modelos puros para descoberta e ingestão de fontes remotas do CNES."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceResource:
    """Recurso oficial descoberto no catálogo do Ministério da Saúde."""

    source: str
    resource_id: str
    name: str
    format: str
    url: str
    year: int
    last_modified: str | None = None


@dataclass(frozen=True)
class RemoteCompetenceResult:
    """Competências mensais encontradas em um único recurso anual."""

    year: int
    competences: tuple[str, ...]


@dataclass(frozen=True)
class RemoteFetchRequest:
    """Filtros solicitados para gerar um CSV canônico local."""

    competence: str
    uf: str | None = None
    municipality: str | None = None
    establishment_type: str | None = None
    legal_nature: str | None = None
    management: str | None = None
    sus_agreement: bool | None = None
    min_beds: int | None = None
    max_beds: int | None = None


@dataclass(frozen=True)
class RemoteFetchResult:
    """Resultado normalizado, pronto para importação no catálogo local."""

    filepath: Path
    source: str
    competence: str
    records: int
    native_filters: tuple[str, ...]
    local_filters: tuple[str, ...]
    missing_fields: tuple[str, ...]
    derived_fields: tuple[str, ...]
    from_cache: bool
    resource_id: str
    etag: str | None = None
    download_cache_hit: bool = False
    contract_version: str = "v1"
    resource_version: str | None = None


@dataclass(frozen=True)
class RemoteLoadResult:
    """Resultado da coleta, opcionalmente acompanhado do lote carregado."""

    fetch: RemoteFetchResult
    batch_id: str | None = None
