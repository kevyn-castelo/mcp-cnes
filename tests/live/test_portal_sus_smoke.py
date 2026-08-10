from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import pytest

from mcp_cnes.application import ListRemoteCompetences, ListRemoteResources
from mcp_cnes.domain.remote import SourceResource
from mcp_cnes.infrastructure.config import Settings
from mcp_cnes.infrastructure.sources import PortalSUSRemoteSource


@pytest.mark.live
def test_portal_sus_catalog_publishes_at_least_one_valid_dataset() -> None:
    if os.getenv("CNES_RUN_LIVE_TESTS") != "1":
        pytest.skip("Defina CNES_RUN_LIVE_TESTS=1 para autorizar acesso externo")

    settings = Settings(
        request_timeout=30,
        max_retries=2,
        retry_delay=0,
        remote_backoff_base=0,
    )
    source = PortalSUSRemoteSource(settings)

    resources = ListRemoteResources(source).execute()

    assert resources, "O catálogo oficial do Portal SUS não publicou datasets CSV"
    valid_resources = [
        resource
        for resource in resources
        if resource.source == source.name
        and bool(resource.resource_id.strip())
        and bool(resource.name.strip())
        and resource.format == "CSV"
        and resource.year >= 2000
        and (parsed := urlparse(resource.url)).scheme == "https"
        and parsed.hostname == settings.remote_download_host
        and parsed.path.startswith(settings.remote_download_path_prefix)
    ]
    assert valid_resources, "O catálogo oficial não publicou nenhum dataset válido"


@pytest.mark.live
def test_latest_portal_sus_resource_contains_monthly_competences(tmp_path: Path) -> None:
    if os.getenv("CNES_RUN_LIVE_TESTS") != "1":
        pytest.skip("Defina CNES_RUN_LIVE_TESTS=1 para autorizar acesso externo")

    downloaded_resources: list[SourceResource] = []

    class CountingPortalSUSRemoteSource(PortalSUSRemoteSource):
        def _download(
            self, resource: SourceResource, *, if_none_match: str | None = None
        ) -> tuple[Path, str | None, bool]:
            downloaded_resources.append(resource)
            return super()._download(resource, if_none_match=if_none_match)

    settings = Settings(
        request_timeout=30,
        max_retries=2,
        retry_delay=0,
        remote_backoff_base=0,
        remote_dir=tmp_path / "remote",
        remote_cache_dir=tmp_path / "cache",
    )
    source = CountingPortalSUSRemoteSource(settings)
    resources = ListRemoteResources(source).execute()

    result = ListRemoteCompetences(source).execute()

    latest_year = max(resource.year for resource in resources)
    assert result.year == latest_year
    assert result.competences, "O recurso mais recente não publicou competências mensais"
    assert all(
        len(value) == 6
        and value.isdigit()
        and value.startswith(str(latest_year))
        and 1 <= int(value[4:]) <= 12
        for value in result.competences
    )
    assert [resource.year for resource in downloaded_resources] == [latest_year]
