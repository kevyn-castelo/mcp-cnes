"""Casos de uso para descoberta e ingestão remota."""

from __future__ import annotations

from pathlib import Path

from mcp_cnes.domain.models import LoadSummary
from mcp_cnes.domain.remote import (
    RemoteCompetenceResult,
    RemoteFetchRequest,
    RemoteLoadResult,
    SourceResource,
)
from mcp_cnes.domain.rules import validate_bed_range

from .load_data import LoadData
from .ports import CNESRemoteSource


def validate_competence(value: str) -> str:
    """Valida uma competência mensal no formato YYYYMM."""

    if len(value) != 6 or not value.isdigit() or not 1 <= int(value[4:]) <= 12:
        raise ValueError("competencia deve usar o formato YYYYMM com mês válido")
    return value


class ListRemoteCompetences:
    def __init__(self, source: CNESRemoteSource) -> None:
        self._source = source

    def execute(self, year: int | None = None) -> RemoteCompetenceResult:
        result = self._source.list_competences(year)
        return RemoteCompetenceResult(
            year=result.year,
            competences=tuple(sorted(set(result.competences))),
        )


class ListRemoteResources:
    def __init__(self, source: CNESRemoteSource) -> None:
        self._source = source

    def execute(self) -> tuple[SourceResource, ...]:
        return tuple(sorted(self._source.list_resources(), key=lambda item: item.year))


class FetchRemoteData:
    def __init__(
        self,
        source: CNESRemoteSource,
        *,
        loader: LoadData | None = None,
    ) -> None:
        self._source = source
        self._loader = loader

    def execute(
        self,
        *,
        competence: str,
        uf: str | None = None,
        municipality: str | None = None,
        establishment_type: str | None = None,
        min_beds: int | None = None,
        max_beds: int | None = None,
        auto_load: bool = True,
        destination: Path | None = None,
    ) -> RemoteLoadResult:
        validate_competence(competence)
        validate_bed_range(min_beds, max_beds)
        normalized_uf = uf.upper() if uf else None
        if normalized_uf is not None and (
            len(normalized_uf) != 2 or not normalized_uf.isalpha()
        ):
            raise ValueError("uf deve conter exatamente duas letras")
        request = RemoteFetchRequest(
            competence=competence,
            uf=normalized_uf,
            municipality=municipality.strip() if municipality else None,
            establishment_type=(
                establishment_type.strip() if establishment_type else None
            ),
            min_beds=min_beds,
            max_beds=max_beds,
        )
        fetched = self._source.fetch(request, destination)
        if not auto_load:
            return RemoteLoadResult(fetched)
        if self._loader is None:
            raise ValueError("auto_load requer um carregador configurado")
        summary: LoadSummary = self._loader.execute(
            fetched.filepath,
            source=fetched.source,
            competence=fetched.competence,
            filters={
                "uf": normalized_uf,
                "municipio": request.municipality,
                "tipo_estabelecimento": request.establishment_type,
                "min_leitos": min_beds,
                "max_leitos": max_beds,
            },
        )
        return RemoteLoadResult(fetched, summary.batch_id)
