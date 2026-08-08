from __future__ import annotations

import pytest

from mcp_cnes.domain.errors import ConfigurationError
from mcp_cnes.infrastructure.config import Settings, load_settings
from mcp_server import MCPServer


def test_settings_externalize_runtime_values() -> None:
    settings = Settings.from_env(
        {
            "MCP_CNES_COMPETENCE": "202607",
            "MCP_CNES_MIN_BEDS": "20",
            "MCP_CNES_MAX_BEDS": "300",
            "MCP_CNES_TARGET_CITIES": '{"NORTE":["MANAUS"]}',
            "MCP_CNES_PRIVATE_NATURE_CODES": "2062,2240",
            "MCP_CNES_DIRECTOR_CBO_CODES": "121010",
            "MCP_CNES_DATA_DIR": "data",
            "MCP_CNES_DATABASE_PATH": "data/catalog.sqlite3",
            "MCP_CNES_MAX_CSV_SIZE_BYTES": "2048",
            "MCP_CNES_ALLOWED_CSV_FILES": "valid.csv,monthly.csv",
            "MCP_CNES_BATCH_RETENTION_COUNT": "3",
            "MCP_CNES_OUTPUT_DIR": "output",
            "MCP_CNES_BASE_URL": "https://example.test",
            "MCP_CNES_KIBANA_API": "https://example.test/api",
            "MCP_CNES_KIBANA_INDEX": "cnes_custom_*",
            "MCP_CNES_DASHBOARD_URL": "https://example.test/dashboard",
            "MCP_CNES_REQUEST_TIMEOUT": "30",
            "MCP_CNES_BROWSER_TIMEOUT_MS": "45000",
            "MCP_CNES_MIN_DELAY": "0.5",
            "MCP_CNES_MAX_DELAY": "1.5",
            "MCP_CNES_MAX_RETRIES": "2",
            "MCP_CNES_RETRY_DELAY": "3",
        }
    )

    assert settings.competence == "202607"
    assert (settings.min_beds, settings.max_beds) == (20, 300)
    assert settings.target_cities == {"NORTE": ("MANAUS",)}
    assert settings.data_dir.name == "data"
    assert settings.database_path.name == "catalog.sqlite3"
    assert settings.max_csv_size_bytes == 2048
    assert settings.allowed_csv_files == ("valid.csv", "monthly.csv")
    assert settings.batch_retention_count == 3
    assert settings.request_timeout == 30
    assert settings.kibana_index == "cnes_custom_*"


@pytest.mark.parametrize(
    "environment, message",
    [
        ({"MCP_CNES_COMPETENCE": "202613"}, "mês inválido"),
        (
            {"MCP_CNES_MIN_BEDS": "200", "MCP_CNES_MAX_BEDS": "100"},
            "Faixa de leitos inválida",
        ),
        ({"MCP_CNES_REQUEST_TIMEOUT": "rápido"}, "deve ser um inteiro"),
        ({"MCP_CNES_MAX_CSV_SIZE_BYTES": "0"}, "maior que zero"),
        ({"MCP_CNES_BATCH_RETENTION_COUNT": "0"}, "maior que zero"),
        ({"MCP_CNES_BASE_URL": "not-a-url"}, r"URL HTTP\(S\) válida"),
        ({"MCP_CNES_KIBANA_INDEX": "../secret"}, "índice Kibana inválido"),
    ],
)
def test_invalid_settings_fail_with_clear_message(
    environment: dict[str, str], message: str
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        load_settings(environment)


def test_invalid_environment_fails_during_server_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_CNES_REQUEST_TIMEOUT", "zero")

    with pytest.raises(ConfigurationError, match="MCP_CNES_REQUEST_TIMEOUT deve ser um inteiro"):
        MCPServer()
