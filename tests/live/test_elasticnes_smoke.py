from __future__ import annotations

import os
from collections.abc import Sequence

import pytest

from mcp_cnes.domain.models import HospitalInfo
from mcp_cnes.infrastructure.collectors import KibanaHttpCollector
from mcp_cnes.infrastructure.config import Settings


@pytest.mark.live
def test_elasticnes_kibana_contract_is_reachable() -> None:
    if os.getenv("CNES_RUN_LIVE_TESTS") != "1":
        pytest.skip("Defina CNES_RUN_LIVE_TESTS=1 para autorizar acesso externo")

    collector = KibanaHttpCollector(
        Settings(request_timeout=15, max_retries=1, retry_delay=0)
    )
    result = collector.collect("MANAUS", 0, None)

    assert isinstance(result, Sequence)
    assert all(isinstance(item, HospitalInfo) for item in result)
