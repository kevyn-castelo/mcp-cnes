import os
import socket
import subprocess
import sys

import pytest


def test_standard_suite_rejects_network_connections() -> None:
    with socket.socket() as client, pytest.raises(
        RuntimeError, match="rede externa desabilitada"
    ):
        client.connect(("192.0.2.1", 9))


def test_standard_suite_rejects_network_in_python_subprocess() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import socket; socket.create_connection(('192.0.2.1', 9))",
        ],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode != 0
    assert "rede externa desabilitada" in completed.stderr
