from __future__ import annotations

import os

import pytest
import requests


@pytest.mark.live
def test_elasticnes_homepage_is_reachable() -> None:
    if os.getenv("CNES_RUN_LIVE_TESTS") != "1":
        pytest.skip("Defina CNES_RUN_LIVE_TESTS=1 para autorizar acesso externo")

    response = requests.get("https://elasticnes.saude.gov.br/", timeout=15)

    assert response.status_code < 500
