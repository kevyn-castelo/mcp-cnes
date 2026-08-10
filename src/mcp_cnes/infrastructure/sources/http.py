"""Cliente HTTP resiliente compartilhado pelas fontes remotas."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

from mcp_cnes.domain.errors import CollectorError
from mcp_cnes.infrastructure.config import Settings


@dataclass(frozen=True)
class DownloadResult:
    final_url: str
    headers: dict[str, str]
    bytes_written: int
    not_modified: bool = False


class ResilientHttpClient:
    """GET com timeout, backoff exponencial e concorrência limitada."""

    def __init__(
        self,
        settings: Settings,
        *,
        session: Any | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._session = session or requests.Session()
        self._sleep = sleeper
        self._semaphore = threading.BoundedSemaphore(settings.remote_max_concurrency)

    _REDIRECT_STATUSES = {301, 302, 303, 307, 308}
    _MAX_REDIRECTS = 5

    def get(
        self,
        url: str,
        *,
        validate_url: Callable[[str], bool] | None = None,
    ) -> Any:
        last_error: CollectorError | None = None
        for attempt in range(self._settings.max_retries):
            response: Any | None = None
            try:
                with self._semaphore:
                    response, _ = self._request_following_redirects(
                        url,
                        accept="text/html,application/zip,text/csv;q=0.9,*/*;q=0.1",
                        stream=False,
                        validate_url=validate_url,
                        extra_headers=None,
                    )
            except requests.Timeout as exc:
                last_error = CollectorError(
                    "remote_timeout",
                    "remote_request",
                    "A fonte oficial excedeu o tempo limite",
                    retryable=True,
                )
                last_error.__cause__ = exc
            except requests.RequestException as exc:
                last_error = CollectorError(
                    "remote_transport_error",
                    "remote_request",
                    "Não foi possível acessar a fonte oficial",
                    retryable=True,
                )
                last_error.__cause__ = exc
            else:
                if response is None:
                    raise AssertionError("sessao HTTP retornou resposta nula")
                status = int(response.status_code)
                if status == 200:
                    return response
                if status == 429 or status >= 500:
                    last_error = CollectorError(
                        "remote_rate_limited" if status == 429 else "remote_server_error",
                        "remote_request",
                        (
                            "A fonte oficial limitou temporariamente as requisições"
                            if status == 429
                            else "A fonte oficial está temporariamente indisponível"
                        ),
                        retryable=True,
                        status_code=status,
                    )
                    close = getattr(response, "close", None)
                    if callable(close):
                        close()
                else:
                    raise CollectorError(
                        "remote_client_error",
                        "remote_request",
                        "A fonte oficial rejeitou a requisição",
                        status_code=status,
                    )
            if attempt < self._settings.max_retries - 1:
                delay = self._settings.remote_backoff_base * (2**attempt)
                if (
                    response is not None
                    and last_error is not None
                    and last_error.status_code == 429
                ):
                    retry_after = getattr(response, "headers", {}).get("Retry-After")
                    if retry_after and str(retry_after).isdigit():
                        delay = max(delay, float(retry_after))
                self._sleep(delay)
        if last_error is None:
            raise AssertionError("retry remoto terminou sem resposta ou erro")
        raise last_error

    def download(
        self,
        url: str,
        destination: Path,
        *,
        validate_url: Callable[[str], bool] | None = None,
        if_none_match: str | None = None,
    ) -> DownloadResult:
        """Transfere o corpo sob o limite de concorrência e repete falhas de stream."""

        last_error: CollectorError | None = None
        for attempt in range(self._settings.max_retries):
            response: Any | None = None
            destination.unlink(missing_ok=True)
            try:
                with self._semaphore:
                    response, final_url = self._request_following_redirects(
                        url,
                        accept="application/zip,text/csv;q=0.9,*/*;q=0.1",
                        stream=True,
                        validate_url=validate_url,
                        extra_headers=(
                            {"If-None-Match": if_none_match}
                            if if_none_match is not None
                            else None
                        ),
                    )
                    if response is None:
                        raise AssertionError("sessão HTTP retornou resposta nula")
                    status = int(response.status_code)
                    if status == 304:
                        return DownloadResult(
                            final_url=final_url,
                            headers={
                                str(key): str(value)
                                for key, value in getattr(response, "headers", {}).items()
                            },
                            bytes_written=0,
                            not_modified=True,
                        )
                    if status != 200:
                        if status == 429 or status >= 500:
                            raise CollectorError(
                                "remote_rate_limited" if status == 429 else "remote_server_error",
                                "remote_request",
                                "A fonte oficial está temporariamente indisponível",
                                retryable=True,
                                status_code=status,
                            )
                        raise CollectorError(
                            "remote_client_error",
                            "remote_request",
                            "A fonte oficial rejeitou a requisição",
                            status_code=status,
                        )
                    headers = {
                        str(key): str(value)
                        for key, value in getattr(response, "headers", {}).items()
                    }
                    declared = headers.get("Content-Length")
                    if declared is not None:
                        try:
                            declared_size = int(declared)
                        except ValueError:
                            declared_size = 0
                        if declared_size > self._settings.remote_max_download_bytes:
                            raise CollectorError(
                                "remote_file_too_large",
                                "remote_security",
                                "O recurso remoto excede o tamanho máximo configurado",
                            )
                    total = 0
                    iterator = getattr(response, "iter_content", None)
                    chunks: Any = (
                        iterator(chunk_size=1024 * 1024)
                        if callable(iterator)
                        else (bytes(response.content),)
                    )
                    with destination.open("wb") as handle:
                        for chunk in chunks:
                            if not chunk:
                                continue
                            total += len(chunk)
                            if total > self._settings.remote_max_download_bytes:
                                raise CollectorError(
                                    "remote_file_too_large",
                                    "remote_security",
                                    "O recurso remoto excede o tamanho máximo configurado",
                                )
                            handle.write(chunk)
                    return DownloadResult(
                        final_url=final_url,
                        headers=headers,
                        bytes_written=total,
                    )
            except CollectorError as exc:
                if not exc.retryable:
                    raise
                last_error = exc
            except requests.Timeout as exc:
                last_error = CollectorError(
                    "remote_timeout",
                    "remote_request",
                    "A fonte oficial excedeu o tempo limite",
                    retryable=True,
                )
                last_error.__cause__ = exc
            except requests.RequestException as exc:
                last_error = CollectorError(
                    "remote_transport_error",
                    "remote_request",
                    "Não foi possível acessar a fonte oficial",
                    retryable=True,
                )
                last_error.__cause__ = exc
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
            destination.unlink(missing_ok=True)
            if attempt < self._settings.max_retries - 1:
                self._sleep(self._settings.remote_backoff_base * (2**attempt))
        if last_error is None:
            raise AssertionError("retry remoto terminou sem resposta ou erro")
        raise last_error

    def _request_following_redirects(
        self,
        url: str,
        *,
        accept: str,
        stream: bool,
        validate_url: Callable[[str], bool] | None,
        extra_headers: dict[str, str] | None,
    ) -> tuple[Any, str]:
        """Segue redirects somente depois de validar o próximo destino."""

        current_url = url
        for redirect_count in range(self._MAX_REDIRECTS + 1):
            if validate_url is not None and not validate_url(current_url):
                raise CollectorError(
                    "remote_redirect_not_allowed",
                    "remote_security",
                    "A fonte remota redirecionou para um domínio não permitido",
                )
            response = self._session.request(
                "GET",
                current_url,
                headers={
                    "Accept": accept,
                    "User-Agent": self._settings.remote_user_agent,
                    **(extra_headers or {}),
                },
                timeout=self._settings.request_timeout,
                allow_redirects=False,
                stream=stream,
            )
            if int(response.status_code) not in self._REDIRECT_STATUSES:
                return response, current_url
            location = getattr(response, "headers", {}).get("Location")
            close = getattr(response, "close", None)
            if callable(close):
                close()
            if not location:
                raise CollectorError(
                    "remote_redirect_invalid",
                    "remote_security",
                    "A fonte remota retornou um redirect sem destino",
                )
            if redirect_count >= self._MAX_REDIRECTS:
                raise CollectorError(
                    "remote_redirect_limit",
                    "remote_security",
                    "A fonte remota excedeu o limite de redirects",
                )
            current_url = urljoin(current_url, str(location))
        raise AssertionError("controle de redirects terminou sem resposta")
