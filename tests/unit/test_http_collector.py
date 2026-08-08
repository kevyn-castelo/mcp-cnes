from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import requests

from mcp_cnes.application.ports import CNESCollector
from mcp_cnes.domain.errors import CollectorError
from mcp_cnes.infrastructure.collectors import KibanaHttpCollector
from mcp_cnes.infrastructure.config import Settings


class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class FakeSession:
    def __init__(self, effects: list[FakeResponse | Exception]) -> None:
        self.effects = effects
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        effect = self.effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


def settings(**overrides: Any) -> Settings:
    values = {
        "competence": "202607",
        "min_beds": 50,
        "max_beds": 150,
        "max_retries": 2,
        "retry_delay": 0,
        **overrides,
    }
    return Settings(**values)


def test_http_collector_maps_successful_aggregation_to_domain() -> None:
    payload = {
        "aggregations": {
            "por_cnes": {
                "buckets": [
                    {
                        "key": "1234567",
                        "total_leitos": {"value": 75},
                        "nome_fantasia": {"buckets": [{"key": "Hospital Norte"}]},
                        "gestao": {"buckets": [{"key": "M"}]},
                        "natureza": {"buckets": [{"key": "2062"}]},
                        "uf": {"buckets": [{"key": "AM"}]},
                    },
                    {
                        "key": "9999999",
                        "total_leitos": {"value": 80},
                        "natureza": {"buckets": [{"key": "1000 - PÚBLICA"}]},
                    },
                    {"key": "7654321", "total_leitos": {"value": 49}},
                ]
            }
        }
    }
    session = FakeSession([FakeResponse(200, payload)])

    collector: CNESCollector = KibanaHttpCollector(settings(), session=session)
    result = collector.collect("Manaus", 50, 150)

    assert [hospital.cnes for hospital in result] == ["1234567"]
    assert result[0].municipio == "Manaus"
    assert result[0].leitos_existentes == 75
    assert session.calls[0]["timeout"] == 60
    request_params = session.calls[0]["json"]["batch"][0]["request"]["params"]
    assert request_params["index"] == "cnes-leitos*"
    assert request_params["body"]["aggs"]["por_cnes"]


def test_http_collector_unwraps_bsearch_response() -> None:
    raw_response = {"aggregations": {"por_cnes": {"buckets": []}}}
    session = FakeSession(
        [FakeResponse(200, {"result": {"rawResponse": raw_response}})]
    )

    result = KibanaHttpCollector(settings(), session=session).collect("Manaus")

    assert result == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"aggregations": None},
        {"aggregations": {"por_cnes": {"buckets": None}}},
    ],
)
def test_http_collector_rejects_malformed_success_payload(payload: Any) -> None:
    session = FakeSession([FakeResponse(200, payload)])

    with pytest.raises(CollectorError) as raised:
        KibanaHttpCollector(settings(), session=session).collect("Manaus")

    assert raised.value.code == "http_invalid_response"
    assert raised.value.stage == "http_decode"


@pytest.mark.parametrize(
    "effect, expected_code, expected_status, retryable",
    [
        (requests.Timeout("slow"), "http_timeout", None, True),
        (FakeResponse(429), "http_rate_limited", 429, True),
        (FakeResponse(503), "http_server_error", 503, True),
    ],
)
def test_http_collector_reports_predictable_external_failures(
    effect: FakeResponse | Exception,
    expected_code: str,
    expected_status: int | None,
    retryable: bool,
) -> None:
    effects = [effect, effect]
    session = FakeSession(effects)
    sleeps: list[float] = []
    sleeper: Callable[[float], None] = sleeps.append

    with pytest.raises(CollectorError) as raised:
        KibanaHttpCollector(settings(), session=session, sleeper=sleeper).collect("Manaus")

    assert raised.value.code == expected_code
    assert raised.value.stage == "http_request"
    assert raised.value.status_code == expected_status
    assert raised.value.retryable is retryable
    assert len(session.calls) == 2
    assert sleeps == [0]
