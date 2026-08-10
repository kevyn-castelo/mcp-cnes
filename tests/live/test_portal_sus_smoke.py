from __future__ import annotations

import os
from urllib.parse import urlparse

import pytest

from mcp_cnes.application import ListRemoteResources
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
