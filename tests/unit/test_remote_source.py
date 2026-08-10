from __future__ import annotations

import csv
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


def annual_csv() -> bytes:
    return (
        "COMP;UF;MUNICIPIO;CNES;NOME_ESTABELECIMENTO;TP_GESTAO;"
        "CO_TIPO_UNIDADE;DS_TIPO_UNIDADE;NATUREZA_JURIDICA;"
        "DESC_NATUREZA_JURIDICA;LEITOS_EXISTENTES;LEITOS_SUS\n"
        "202501;SP;S\u00e3o Paulo;0000001;Hospital A;M;05;Hospital Geral;2062;"
        "Sociedade Empres\u00e1ria;80;60\n"
        "202501;SP;Santos;0000002;Hospital B;E;05;Hospital Geral;2062;"
        "Sociedade EmpresÃ¡ria;40;0\n"
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

    assert source.list_competences() == ("202501", "202502")


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
