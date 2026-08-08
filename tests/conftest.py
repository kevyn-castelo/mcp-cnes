from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def block_network_in_standard_suite(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Falha imediatamente se um teste não-live tentar abrir uma conexão."""

    if request.node.get_closest_marker("live") is not None:
        yield
        return

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create_connection = socket.create_connection
    network_guard = Path(__file__).parent / "network_guard"
    inherited_path = os.environ.get("PYTHONPATH", "")
    guarded_path = str(network_guard)
    if inherited_path:
        guarded_path += os.pathsep + inherited_path
    monkeypatch.setenv("MCP_CNES_TEST_NETWORK_DISABLED", "1")
    monkeypatch.setenv("PYTHONPATH", guarded_path)

    def is_loopback(address: object) -> bool:
        return isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}

    def denied(*args: object, **kwargs: object) -> None:
        raise RuntimeError("rede externa desabilitada na suíte padrão")

    def guarded_connect(client: socket.socket, address: object) -> None:
        if is_loopback(address):
            original_connect(client, address)  # type: ignore[arg-type]
            return
        denied()

    def guarded_connect_ex(client: socket.socket, address: object) -> int:
        if is_loopback(address):
            return original_connect_ex(client, address)  # type: ignore[arg-type]
        denied()
        return 1

    def guarded_create_connection(address: object, *args: object, **kwargs: object):
        if is_loopback(address):
            return original_create_connection(address, *args, **kwargs)  # type: ignore[arg-type]
        denied()

    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    yield
