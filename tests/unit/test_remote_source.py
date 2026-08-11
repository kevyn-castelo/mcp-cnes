from __future__ import annotations

import csv
import hashlib
import json
import threading
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import requests

from mcp_cnes.domain.errors import CollectorError
from mcp_cnes.domain.remote import RemoteFetchRequest
from mcp_cnes.infrastructure.config import Settings
from mcp_cnes.infrastructure.sources import PortalSUSRemoteSource
from mcp_cnes.infrastructure.sources.http import ResilientHttpClient

CATALOG_URL = "https://dadosabertos.saude.gov.br/dataset/hospitais-e-leitos"
RESOURCE_URL = (
    "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/Leitos_SUS/Leitos_2025.csv"
)
RESOURCE_URL_2024 = (
    "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/Leitos_SUS/Leitos_2024.csv"
)


class Response:
    def __init__(
        self,
        content: bytes = b"",
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}

    def iter_content(self, chunk_size: int) -> Any:
        del chunk_size
        yield self.content

    def close(self) -> None:
        return None


class Session:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = deque(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        response = self.responses.popleft()
        if isinstance(response, BaseException):
            raise response
        return response


def catalog_html(*, url: str = RESOURCE_URL, state: str = "active") -> bytes:
    return (
        '<script>window.__data={"resources":['
        '{"id":"resource-2025","name":"Leitos 2025","format":"CSV",'
        f'"state":"{state}","url":"{url}",'
        '"last_modified":"2026-01-02T00:00:00"}]};</script>'
    ).encode()


def catalog_resources(*resources: tuple[int, str, str, str]) -> bytes:
    payload = ",".join(
        (
            f'{{"id":"{resource_id}","name":"Leitos {year}","format":"CSV",'
            f'"state":"active","url":"{url}","last_modified":"{last_modified}"}}'
        )
        for year, url, resource_id, last_modified in resources
    )
    return f'<script>window.__data={{"resources":[{payload}]}};</script>'.encode()


def annual_csv() -> bytes:
    return (
        "COMP;UF;MUNICIPIO;CNES;NOME_ESTABELECIMENTO;TP_GESTAO;"
        "CO_TIPO_UNIDADE;DS_TIPO_UNIDADE;NATUREZA_JURIDICA;"
        "DESC_NATUREZA_JURIDICA;LEITOS_EXISTENTES;LEITOS_SUS\n"
        "202501;SP;S\u00e3o Paulo;0000001;Hospital A;M;05;Hospital Geral;2062;"
        "Sociedade Empres\u00e1ria;80;60\n"
        "202501;SP;Santos;0000002;Hospital B;E;05;Hospital Geral;2062;"
        "Sociedade Empresária;40;0\n"
        "202502;SP;S\u00e3o Paulo;0000003;Hospital C;M;05;Hospital Geral;2062;"
        "Sociedade Empres\u00e1ria;90;70\n"
    ).encode("latin-1")


def settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_path": tmp_path / "cnes.sqlite3",
        "remote_dir": tmp_path / "remote",
        "remote_cache_dir": tmp_path / "cache",
        "remote_backoff_base": 0,
    }
    values.update(overrides)
    return Settings(**values)


def competence_cache_path(
    config: Settings,
    *,
    resource_id: str = "resource-2025",
    last_modified: str = "2026-01-02T00:00:00",
) -> Path:
    version = hashlib.sha256(
        f"{resource_id}\0{last_modified}".encode()
    ).hexdigest()[:16]
    return config.remote_cache_dir / f"competences-{version}.json"


def test_discovers_official_resource_and_normalizes_local_filters(tmp_path: Path) -> None:
    session = Session(
        [Response(catalog_html()), Response(annual_csv(), headers={"ETag": '"fixture"'})]
    )
    source = PortalSUSRemoteSource(
        settings(tmp_path),
        session=session,
        sleeper=lambda _: None,
        clock=lambda: datetime(2026, 8, 9, tzinfo=UTC),
    )

    resources = source.list_resources()
    result = source.fetch(
        RemoteFetchRequest(
            competence="202501",
            uf="SP",
            municipality="sao",
            establishment_type="geral",
            legal_nature="sociedade",
            management="M",
            sus_agreement=True,
            min_beds=50,
            max_beds=100,
        )
    )

    assert [(item.year, item.resource_id) for item in resources] == [
        (2025, "resource-2025")
    ]
    assert result.records == 1
    assert result.native_filters == ()
    assert result.local_filters == (
        "competencia",
        "uf",
        "municipio",
        "tipo_estabelecimento",
        "natureza_juridica",
        "gestao",
        "convenio_sus",
        "min_leitos",
        "max_leitos",
    )
    assert result.derived_fields == ("CONVENIO_SUS",)
    assert result.etag == '"fixture"'
    with result.filepath.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["MUNICIPIO"] == "S\u00e3o Paulo"
    assert rows[0]["GESTAO"] == "M"
    assert rows[0]["CONVENIO_SUS"] == "Sim"
    assert session.calls[0]["headers"]["User-Agent"].startswith("mcp-cnes/")


def test_closed_competence_uses_verified_disk_cache_without_network(tmp_path: Path) -> None:
    session = Session([Response(catalog_html()), Response(annual_csv())])
    source = PortalSUSRemoteSource(
        settings(tmp_path),
        session=session,
        sleeper=lambda _: None,
        clock=lambda: datetime(2026, 8, 9, tzinfo=UTC),
    )
    request = RemoteFetchRequest(competence="202501")

    first = source.fetch(request)
    second = source.fetch(request)

    assert first.from_cache is False
    assert second.from_cache is True
    assert len(session.calls) == 2
    assert first.filepath == second.filepath


def test_different_municipality_reuses_same_closed_annual_download(
    tmp_path: Path,
) -> None:
    session = Session(
        [Response(catalog_html()), Response(annual_csv(), headers={"ETag": '"annual"'})]
    )
    source = PortalSUSRemoteSource(
        settings(tmp_path),
        session=session,
        sleeper=lambda _: None,
        clock=lambda: datetime(2026, 8, 9, tzinfo=UTC),
    )

    first = source.fetch(
        RemoteFetchRequest(competence="202501", municipality="São Paulo")
    )
    second = source.fetch(
        RemoteFetchRequest(competence="202501", municipality="Santos")
    )

    assert first.download_cache_hit is False
    assert second.download_cache_hit is True
    assert second.etag == '"annual"'
    assert [call["url"] for call in session.calls] == [CATALOG_URL, RESOURCE_URL]


def test_open_year_annual_cache_is_revalidated_with_etag_without_redownload(
    tmp_path: Path,
) -> None:
    resource_url = RESOURCE_URL.replace("2025", "2026")
    catalog = catalog_resources(
        (2026, resource_url, "resource-2026", "2026-08-01T00:00:00")
    )
    content = annual_csv().replace(b"2025", b"2026")
    session = Session(
        [
            Response(catalog),
            Response(content, headers={"ETag": '"open-year"'}),
            Response(status_code=304, headers={"ETag": '"open-year"'}),
        ]
    )
    source = PortalSUSRemoteSource(
        settings(tmp_path),
        session=session,
        sleeper=lambda _: None,
        clock=lambda: datetime(2026, 8, 9, tzinfo=UTC),
    )

    first = source.fetch(
        RemoteFetchRequest(competence="202601", municipality="São Paulo")
    )
    second = source.fetch(
        RemoteFetchRequest(competence="202601", municipality="Santos")
    )

    assert first.download_cache_hit is False
    assert second.download_cache_hit is True
    assert session.calls[-1]["headers"]["If-None-Match"] == '"open-year"'


def test_catalog_rejects_download_outside_the_approved_host(tmp_path: Path) -> None:
    session = Session([Response(catalog_html(url="https://attacker.test/leitos.csv"))])
    source = PortalSUSRemoteSource(settings(tmp_path), session=session, sleeper=lambda _: None)

    with pytest.raises(CollectorError) as captured:
        source.list_resources()

    assert captured.value.code == "remote_catalog_empty"


def test_catalog_does_not_follow_redirect_outside_allowlist(tmp_path: Path) -> None:
    session = Session(
        [
            Response(
                status_code=302,
                headers={"Location": "https://attacker.test/catalog"},
            )
        ]
    )
    source = PortalSUSRemoteSource(settings(tmp_path), session=session, sleeper=lambda _: None)

    with pytest.raises(CollectorError) as captured:
        source.list_resources()

    assert captured.value.code == "remote_redirect_not_allowed"
    assert [call["url"] for call in session.calls] == [CATALOG_URL]
    assert session.calls[0]["allow_redirects"] is False


def test_download_does_not_follow_redirect_outside_allowlist(tmp_path: Path) -> None:
    session = Session(
        [
            Response(catalog_html()),
            Response(
                status_code=302,
                headers={"Location": "https://attacker.test/leitos.csv"},
            ),
        ]
    )
    source = PortalSUSRemoteSource(settings(tmp_path), session=session, sleeper=lambda _: None)

    with pytest.raises(CollectorError) as captured:
        source.fetch(RemoteFetchRequest(competence="202501"))

    assert captured.value.code == "remote_redirect_not_allowed"
    assert [call["url"] for call in session.calls] == [CATALOG_URL, RESOURCE_URL]
    assert all(call["allow_redirects"] is False for call in session.calls)


def test_catalog_follows_relative_redirect_within_allowlist(tmp_path: Path) -> None:
    redirected = "https://dadosabertos.saude.gov.br/dataset/hospitais-e-leitos/"
    session = Session(
        [
            Response(status_code=302, headers={"Location": "/dataset/hospitais-e-leitos/"}),
            Response(catalog_html()),
        ]
    )
    source = PortalSUSRemoteSource(settings(tmp_path), session=session, sleeper=lambda _: None)

    resources = source.list_resources()

    assert resources[0].resource_id == "resource-2025"
    assert [call["url"] for call in session.calls] == [CATALOG_URL, redirected]


def test_missing_competence_is_actionable_and_does_not_download(tmp_path: Path) -> None:
    session = Session([Response(catalog_html())])
    source = PortalSUSRemoteSource(settings(tmp_path), session=session, sleeper=lambda _: None)

    with pytest.raises(CollectorError) as captured:
        source.fetch(RemoteFetchRequest(competence="202401"))

    assert captured.value.code == "remote_competence_unavailable"
    assert len(session.calls) == 1


def test_http_retries_timeout_and_server_error_with_exponential_backoff(
    tmp_path: Path,
) -> None:
    delays: list[float] = []
    session = Session(
        [requests.Timeout("slow"), Response(status_code=503), Response(b"ok")]
    )
    client = ResilientHttpClient(
        settings(tmp_path, remote_backoff_base=0.25),
        session=session,
        sleeper=delays.append,
    )

    response = client.get(CATALOG_URL)

    assert response.content == b"ok"
    assert delays == [0.25, 0.5]
    assert len(session.calls) == 3


def test_http_treats_non_retryable_4xx_as_terminal(tmp_path: Path) -> None:
    session = Session([Response(status_code=404)])
    client = ResilientHttpClient(settings(tmp_path), session=session, sleeper=lambda _: None)

    with pytest.raises(CollectorError) as captured:
        client.get(CATALOG_URL)

    assert captured.value.code == "remote_client_error"
    assert captured.value.retryable is False
    assert len(session.calls) == 1


def test_lists_exact_monthly_competences_from_annual_resource(tmp_path: Path) -> None:
    source = PortalSUSRemoteSource(
        settings(tmp_path),
        session=Session([Response(catalog_html()), Response(annual_csv())]),
        sleeper=lambda _: None,
    )

    result = source.list_competences()

    assert result.year == 2025
    assert result.competences == ("202501", "202502")


def test_competence_discovery_defaults_to_latest_and_downloads_one_resource(
    tmp_path: Path,
) -> None:
    catalog = catalog_resources(
        (2024, RESOURCE_URL_2024, "resource-2024", "2025-01-01T00:00:00"),
        (2025, RESOURCE_URL, "resource-2025", "2026-01-02T00:00:00"),
    )
    session = Session([Response(catalog), Response(annual_csv())])
    source = PortalSUSRemoteSource(settings(tmp_path), session=session, sleeper=lambda _: None)

    result = source.list_competences()

    assert result.year == 2025
    assert result.competences == ("202501", "202502")
    assert [call["url"] for call in session.calls] == [CATALOG_URL, RESOURCE_URL]


def test_competence_discovery_explicit_year_does_not_read_other_resources(
    tmp_path: Path,
) -> None:
    catalog = catalog_resources(
        (2024, RESOURCE_URL_2024, "resource-2024", "2025-01-01T00:00:00"),
        (2025, RESOURCE_URL, "resource-2025", "2026-01-02T00:00:00"),
    )
    session = Session([Response(catalog), Response(annual_csv().replace(b"2025", b"2024"))])
    source = PortalSUSRemoteSource(settings(tmp_path), session=session, sleeper=lambda _: None)

    result = source.list_competences(2024)

    assert result.year == 2024
    assert result.competences == ("202401", "202402")
    assert [call["url"] for call in session.calls] == [CATALOG_URL, RESOURCE_URL_2024]


def test_competence_discovery_rejects_unavailable_year_without_download(
    tmp_path: Path,
) -> None:
    session = Session([Response(catalog_html())])
    source = PortalSUSRemoteSource(settings(tmp_path), session=session, sleeper=lambda _: None)

    with pytest.raises(CollectorError) as captured:
        source.list_competences(2024)

    assert captured.value.code == "remote_competence_unavailable"
    assert "2024" in str(captured.value)
    assert [call["url"] for call in session.calls] == [CATALOG_URL]


def test_new_source_instance_reuses_versioned_competence_cache(tmp_path: Path) -> None:
    first_session = Session([Response(catalog_html()), Response(annual_csv())])
    first = PortalSUSRemoteSource(
        settings(tmp_path), session=first_session, sleeper=lambda _: None
    )
    assert first.list_competences().competences == ("202501", "202502")

    second_session = Session([Response(catalog_html())])
    second = PortalSUSRemoteSource(
        settings(tmp_path), session=second_session, sleeper=lambda _: None
    )

    assert second.list_competences().competences == ("202501", "202502")
    assert [call["url"] for call in second_session.calls] == [CATALOG_URL]


def test_legacy_valid_competence_cache_is_reused_without_download(tmp_path: Path) -> None:
    config = settings(tmp_path)
    cache = competence_cache_path(config)
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps(["202501", "202502"]), encoding="utf-8")
    session = Session([Response(catalog_html())])
    source = PortalSUSRemoteSource(config, session=session, sleeper=lambda _: None)

    result = source.list_competences(2025)

    assert result.competences == ("202501", "202502")
    assert [call["url"] for call in session.calls] == [CATALOG_URL]


@pytest.mark.parametrize(
    "cached_payload",
    [
        [],
        ["202401"],
        {"year": 2024, "competences": ["202401"]},
        {"year": 2025, "competences": []},
    ],
)
def test_semantically_invalid_competence_cache_is_refreshed_once(
    tmp_path: Path, cached_payload: object
) -> None:
    config = settings(tmp_path)
    cache = competence_cache_path(config)
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps(cached_payload), encoding="utf-8")
    session = Session([Response(catalog_html()), Response(annual_csv())])
    source = PortalSUSRemoteSource(config, session=session, sleeper=lambda _: None)

    result = source.list_competences(2025)

    assert result.competences == ("202501", "202502")
    assert [call["url"] for call in session.calls] == [CATALOG_URL, RESOURCE_URL]
    assert json.loads(cache.read_text(encoding="utf-8")) == {
        "competences": ["202501", "202502"],
        "year": 2025,
    }


def test_invalid_utf8_competence_cache_is_refreshed_once(tmp_path: Path) -> None:
    config = settings(tmp_path)
    cache = competence_cache_path(config)
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"\xff\xfe\x00")
    session = Session([Response(catalog_html()), Response(annual_csv())])
    source = PortalSUSRemoteSource(config, session=session, sleeper=lambda _: None)

    result = source.list_competences(2025)

    assert result.competences == ("202501", "202502")
    assert [call["url"] for call in session.calls] == [CATALOG_URL, RESOURCE_URL]
    assert json.loads(cache.read_text(encoding="utf-8")) == {
        "competences": ["202501", "202502"],
        "year": 2025,
    }


def test_invalid_cache_rescan_without_selected_year_returns_actionable_error(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)
    cache = competence_cache_path(config)
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps(["202401"]), encoding="utf-8")
    session = Session(
        [Response(catalog_html()), Response(annual_csv().replace(b"2025", b"2024"))]
    )
    source = PortalSUSRemoteSource(config, session=session, sleeper=lambda _: None)

    with pytest.raises(CollectorError) as captured:
        source.list_competences(2025)

    assert captured.value.code == "remote_competence_unavailable"
    assert "2025" in str(captured.value)
    assert [call["url"] for call in session.calls] == [CATALOG_URL, RESOURCE_URL]
    assert json.loads(cache.read_text(encoding="utf-8")) == ["202401"]


@pytest.mark.parametrize("changed_field", ["resource_id", "last_modified"])
def test_changed_resource_version_invalidates_competence_cache(
    tmp_path: Path, changed_field: str
) -> None:
    now = [datetime(2026, 8, 9, tzinfo=UTC)]
    initial_catalog = catalog_resources(
        (2025, RESOURCE_URL, "resource-2025", "2026-01-02T00:00:00"),
    )
    resource_id = "resource-republished" if changed_field == "resource_id" else "resource-2025"
    modified = (
        "2026-02-03T00:00:00"
        if changed_field == "last_modified"
        else "2026-01-02T00:00:00"
    )
    changed_catalog = catalog_resources((2025, RESOURCE_URL, resource_id, modified))
    session = Session(
        [
            Response(initial_catalog),
            Response(annual_csv()),
            Response(changed_catalog),
            Response(annual_csv().replace(b"202502", b"202503")),
        ]
    )
    source = PortalSUSRemoteSource(
        settings(tmp_path, remote_cache_ttl_seconds=1),
        session=session,
        sleeper=lambda _: None,
        clock=lambda: now[0],
    )

    assert source.list_competences().competences == ("202501", "202502")
    now[0] += timedelta(seconds=2)
    assert source.list_competences().competences == ("202501", "202503")
    assert [call["url"] for call in session.calls] == [
        CATALOG_URL,
        RESOURCE_URL,
        CATALOG_URL,
        RESOURCE_URL,
    ]


def test_competence_cache_is_isolated_by_year(tmp_path: Path) -> None:
    catalog = catalog_resources(
        (2024, RESOURCE_URL_2024, "resource-2024", "2025-01-01T00:00:00"),
        (2025, RESOURCE_URL, "resource-2025", "2026-01-02T00:00:00"),
    )
    session = Session(
        [
            Response(catalog),
            Response(annual_csv().replace(b"2025", b"2024")),
            Response(annual_csv()),
        ]
    )
    source = PortalSUSRemoteSource(settings(tmp_path), session=session, sleeper=lambda _: None)

    assert source.list_competences(2024).competences == ("202401", "202402")
    assert source.list_competences(2025).competences == ("202501", "202502")
    assert source.list_competences(2024).competences == ("202401", "202402")
    assert [call["url"] for call in session.calls] == [
        CATALOG_URL,
        RESOURCE_URL_2024,
        RESOURCE_URL,
    ]


def test_competence_discovery_uses_latest_version_within_selected_year(
    tmp_path: Path,
) -> None:
    catalog = catalog_resources(
        (2025, RESOURCE_URL_2024, "resource-old", "2026-01-01T00:00:00"),
        (2025, RESOURCE_URL, "resource-current", "2026-02-01T00:00:00"),
    )
    session = Session([Response(catalog), Response(annual_csv())])
    source = PortalSUSRemoteSource(settings(tmp_path), session=session, sleeper=lambda _: None)

    result = source.list_competences(2025)

    assert result.competences == ("202501", "202502")
    assert [call["url"] for call in session.calls] == [CATALOG_URL, RESOURCE_URL]


def test_download_retries_stream_failure_and_removes_partial_file(tmp_path: Path) -> None:
    class BrokenResponse(Response):
        def iter_content(self, chunk_size: int) -> Any:
            del chunk_size
            yield b"partial"
            raise requests.ConnectionError("stream interrupted")

    session = Session([BrokenResponse(), Response(b"complete")])
    client = ResilientHttpClient(settings(tmp_path), session=session, sleeper=lambda _: None)
    destination = tmp_path / "download.csv"

    result = client.download(RESOURCE_URL, destination)

    assert result.bytes_written == 8
    assert destination.read_bytes() == b"complete"
    assert len(session.calls) == 2


def test_download_concurrency_limit_covers_the_entire_response_body(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    active = 0
    maximum = 0

    class BlockingResponse(Response):
        def iter_content(self, chunk_size: int) -> Any:
            nonlocal active, maximum
            del chunk_size
            with lock:
                active += 1
                maximum = max(maximum, active)
            entered.set()
            assert release.wait(timeout=5)
            yield b"ok"
            with lock:
                active -= 1

    session = Session([BlockingResponse(), BlockingResponse()])
    client = ResilientHttpClient(
        settings(tmp_path, remote_max_concurrency=1),
        session=session,
        sleeper=lambda _: None,
    )
    threads = [
        threading.Thread(target=client.download, args=(RESOURCE_URL, tmp_path / f"{i}.csv"))
        for i in range(2)
    ]
    for thread in threads:
        thread.start()
    assert entered.wait(timeout=5)
    assert len(session.calls) == 1
    release.set()
    for thread in threads:
        thread.join(timeout=5)

    assert maximum == 1
    assert all(not thread.is_alive() for thread in threads)


def test_catalog_refreshes_after_ttl_and_purge_removes_nested_cache(tmp_path: Path) -> None:
    now = [datetime(2026, 8, 9, tzinfo=UTC)]
    refreshed = catalog_html().replace(b"resource-2025", b"resource-refreshed")
    session = Session([Response(catalog_html()), Response(refreshed)])
    source = PortalSUSRemoteSource(
        settings(tmp_path, remote_cache_ttl_seconds=1),
        session=session,
        sleeper=lambda _: None,
        clock=lambda: now[0],
    )
    assert source.list_resources()[0].resource_id == "resource-2025"
    now[0] += timedelta(seconds=2)
    assert source.list_resources()[0].resource_id == "resource-refreshed"

    nested = source.settings.remote_cache_dir / "nested" / "cache.json"
    nested.parent.mkdir(parents=True)
    nested.write_text("{}", encoding="utf-8")
    removed, released = source.purge_cache()
    assert removed == 1
    assert released == 2
    assert not nested.exists()
