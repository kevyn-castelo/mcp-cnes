"""Bloqueio herdável de rede para processos Python iniciados pela suíte padrão."""

from __future__ import annotations

import os
import socket
from typing import Any, NoReturn

if os.getenv("MCP_CNES_TEST_NETWORK_DISABLED") == "1":
    _original_connect = socket.socket.connect
    _original_connect_ex = socket.socket.connect_ex
    _original_create_connection = socket.create_connection

    def _is_loopback(address: object) -> bool:
        return isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}

    def _denied() -> NoReturn:
        raise RuntimeError("rede externa desabilitada na suíte padrão")

    def _guarded_connect(client: socket.socket, address: Any) -> None:
        if _is_loopback(address):
            _original_connect(client, address)
            return
        _denied()

    def _guarded_connect_ex(client: socket.socket, address: Any) -> int:
        if _is_loopback(address):
            return _original_connect_ex(client, address)
        _denied()

    def _guarded_create_connection(
        address: Any, *args: Any, **kwargs: Any
    ) -> socket.socket:
        if _is_loopback(address):
            return _original_create_connection(address, *args, **kwargs)
        _denied()

    socket.socket.connect = _guarded_connect
    socket.socket.connect_ex = _guarded_connect_ex
    socket.create_connection = _guarded_create_connection
