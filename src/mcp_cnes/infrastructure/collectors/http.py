"""Coletor HTTP do Kibana isolado atrás da porta CNESCollector."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any, NoReturn

import requests

from mcp_cnes.domain.errors import CollectorError
from mcp_cnes.domain.models import HospitalInfo
from mcp_cnes.domain.rules import matches_nature_code, validate_bed_range
from mcp_cnes.infrastructure.config import Settings


class KibanaHttpCollector:
    """Consulta a projeção de leitos com retry e erros externos estáveis."""

    def __init__(
        self,
        settings: Settings,
        *,
        session: Any | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self._session = session or self._create_session()
        self._sleep = sleeper

    def collect(
        self,
        municipality: str,
        min_beds: int | None = None,
        max_beds: int | None = None,
    ) -> Sequence[HospitalInfo]:
        effective_min, effective_max = validate_bed_range(min_beds, max_beds)
        response = self._request(self._build_query(municipality))
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise CollectorError(
                "http_invalid_response",
                "http_decode",
                "A fonte CNES retornou JSON inválido",
            ) from exc
        if isinstance(payload, list):
            payload = payload[0] if payload else None
        if isinstance(payload, dict) and "result" in payload:
            result = payload.get("result")
            payload = result.get("rawResponse") if isinstance(result, dict) else None
        if not isinstance(payload, dict):
            raise CollectorError(
                "http_invalid_response",
                "http_decode",
                "A fonte CNES retornou uma estrutura inesperada",
            )
        try:
            return self._extract(payload, municipality, effective_min, effective_max)
        except CollectorError:
            raise
        except (AttributeError, TypeError, ValueError, OverflowError) as exc:
            raise CollectorError(
                "http_invalid_response",
                "http_decode",
                "A fonte CNES retornou uma estrutura inesperada",
            ) from exc

    def _request(self, query: dict[str, Any]) -> Any:
        last_error: CollectorError | None = None
        for attempt in range(self.settings.max_retries):
            try:
                response = self._session.request(
                    "POST",
                    self.settings.kibana_api,
                    json={
                        "batch": [
                            {
                                "request": {
                                    "params": {
                                        "index": self.settings.kibana_index,
                                        "body": query,
                                    }
                                }
                            }
                        ]
                    },
                    timeout=self.settings.request_timeout,
                )
            except requests.Timeout as exc:
                last_error = CollectorError(
                    "http_timeout",
                    "http_request",
                    "Timeout ao consultar a fonte CNES",
                    retryable=True,
                )
                last_error.__cause__ = exc
            except requests.RequestException as exc:
                last_error = CollectorError(
                    "http_transport_error",
                    "http_request",
                    "Falha de transporte ao consultar a fonte CNES",
                    retryable=True,
                )
                last_error.__cause__ = exc
            else:
                status = int(response.status_code)
                if status == 200:
                    return response
                if status == 429:
                    last_error = CollectorError(
                        "http_rate_limited",
                        "http_request",
                        "Fonte CNES limitou temporariamente as requisições",
                        retryable=True,
                        status_code=status,
                    )
                elif status >= 500:
                    last_error = CollectorError(
                        "http_server_error",
                        "http_request",
                        "Fonte CNES indisponível por erro de servidor",
                        retryable=True,
                        status_code=status,
                    )
                else:
                    raise CollectorError(
                        "http_client_error",
                        "http_request",
                        "Fonte CNES rejeitou a requisição",
                        status_code=status,
                    )
            if attempt < self.settings.max_retries - 1:
                self._sleep(self.settings.retry_delay * (attempt + 1))
        if last_error is None:
            raise AssertionError("retry HTTP terminou sem resposta ou erro")
        raise last_error

    def _build_query(self, municipality: str) -> dict[str, Any]:
        return {
            "size": 0,
            "query": {
                "bool": {
                    "must": [
                        {"match": {"MUNICIPIO.keyword": municipality}},
                        {"match": {"COMPETENCIA": self.settings.competence}},
                    ],
                    "should": [
                        {"prefix": {"NATUREZA_JURIDICA.keyword": code}}
                        for code in self.settings.private_nature_codes
                    ],
                    "minimum_should_match": 1,
                }
            },
            "aggs": {
                "por_cnes": {
                    "terms": {"field": "CNES.keyword", "size": 10_000},
                    "aggs": {
                        "total_leitos": {"sum": {"field": "QT_EXIST"}},
                        "total_leitos_sus": {"sum": {"field": "QT_SUS"}},
                        "nome_fantasia": {
                            "terms": {"field": "NOME_FANTASIA.keyword", "size": 1}
                        },
                        "gestao": {"terms": {"field": "GESTAO.keyword", "size": 1}},
                        "natureza": {
                            "terms": {"field": "NATUREZA_JURIDICA.keyword", "size": 1}
                        },
                        "uf": {"terms": {"field": "UF.keyword", "size": 1}},
                    },
                }
            },
        }

    def _extract(
        self,
        payload: dict[str, Any],
        municipality: str,
        min_beds: int | None,
        max_beds: int | None,
    ) -> list[HospitalInfo]:
        aggregations = payload.get("aggregations")
        if not isinstance(aggregations, dict):
            self._invalid_payload()
        grouped = aggregations.get("por_cnes")
        if not isinstance(grouped, dict):
            self._invalid_payload()
        buckets = grouped.get("buckets")
        if not isinstance(buckets, list) or any(
            not isinstance(bucket, dict) for bucket in buckets
        ):
            self._invalid_payload()
        hospitals: list[HospitalInfo] = []
        for bucket in buckets:
            beds = int(bucket.get("total_leitos", {}).get("value", 0) or 0)
            if min_beds is not None and beds < min_beds:
                continue
            if max_beds is not None and beds > max_beds:
                continue
            nature = self._first_key(bucket, "natureza")
            if not matches_nature_code(nature, self.settings.private_nature_codes):
                continue
            hospitals.append(
                HospitalInfo(
                    cnes=str(bucket.get("key", "")),
                    nome_fantasia=self._first_key(bucket, "nome_fantasia"),
                    municipio=municipality,
                    uf=self._first_key(bucket, "uf"),
                    natureza_juridica=nature,
                    gestao=self._first_key(bucket, "gestao"),
                    leitos_existentes=beds,
                    leitos_sus=int(
                        bucket.get("total_leitos_sus", {}).get("value", 0) or 0
                    ),
                    competencia=self.settings.competence,
                )
            )
        return hospitals

    @staticmethod
    def _invalid_payload() -> NoReturn:
        raise CollectorError(
            "http_invalid_response",
            "http_decode",
            "A fonte CNES retornou uma estrutura inesperada",
        )

    @staticmethod
    def _first_key(bucket: dict[str, Any], name: str) -> str:
        values = bucket.get(name, {}).get("buckets", [])
        return str(values[0].get("key", "")) if values else ""

    def _create_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "kbn-xsrf": "kibana",
                "Referer": self.settings.dashboard_url,
            }
        )
        return session
