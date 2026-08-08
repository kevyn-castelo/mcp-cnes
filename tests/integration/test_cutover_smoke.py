from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from mcp import StdioServerParameters

from mcp_cnes.cutover import (
    SmokeProbe,
    _validate_probe_result,
    inspect_source_attestation,
    run_stdio_smoke,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "csv"
PROBE = SmokeProbe(municipio="Manaus", uf="AM", cnes="1234567")
SOURCE = inspect_source_attestation()


@pytest.mark.asyncio
async def test_stdio_smoke_records_version_schemas_volume_and_all_six_calls(
    tmp_path: Path,
) -> None:
    output = tmp_path / "cutover-smoke.json"

    report = await run_stdio_smoke(
        data_dir=FIXTURES,
        database_path=tmp_path / "cnes.sqlite3",
        csv_path=FIXTURES / "valid.csv",
        probe=PROBE,
        output=output,
        revision=SOURCE.revision,
        timeout_seconds=5,
    )

    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted == report
    assert report["server"]["name"] == "mcp-cnes"
    assert report["server"]["version"]
    assert report["source"]["revision"] == SOURCE.revision
    assert report["source"]["sha256"] == SOURCE.sha256
    assert report["source"]["dirty"] is SOURCE.dirty
    assert report["protocol_version"]
    assert report["import"]["records_loaded"] == 1
    assert report["import"]["source_file"] == "valid.csv"
    assert {tool["name"] for tool in report["schemas"]} == {
        "cnes_load_data",
        "cnes_search_municipio",
        "cnes_search_cnes",
        "cnes_search_uf",
        "cnes_statistics",
        "cnes_download_instructions",
    }
    assert all(len(tool["sha256"]) == 64 for tool in report["schemas"])
    assert all(call["status"] == "ok" for call in report["calls"])
    assert str(tmp_path) not in output.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_stdio_smoke_fails_when_probe_does_not_resolve_real_data(
    tmp_path: Path,
) -> None:
    output = tmp_path / "invalid-smoke.json"

    with pytest.raises(RuntimeError, match="cnes_search_cnes"):
        await run_stdio_smoke(
            data_dir=FIXTURES,
            database_path=tmp_path / "invalid.sqlite3",
            csv_path=FIXTURES / "valid.csv",
            probe=SmokeProbe(municipio="Manaus", uf="AM", cnes="9999999"),
            output=output,
            revision=SOURCE.revision,
            timeout_seconds=5,
        )

    assert not output.exists()


@pytest.mark.asyncio
async def test_stdio_smoke_refuses_to_modify_an_existing_database(tmp_path: Path) -> None:
    database = tmp_path / "production.sqlite3"
    database.write_bytes(b"do-not-modify")
    output = tmp_path / "unsafe-smoke.json"

    with pytest.raises(RuntimeError, match="já existe"):
        await run_stdio_smoke(
            data_dir=FIXTURES,
            database_path=database,
            csv_path=FIXTURES / "valid.csv",
            probe=PROBE,
            output=output,
            revision=SOURCE.revision,
            timeout_seconds=5,
        )

    assert database.read_bytes() == b"do-not-modify"
    assert not output.exists()


@pytest.mark.asyncio
async def test_stdio_smoke_times_out_without_writing_a_manifest(tmp_path: Path) -> None:
    hanging_server = Path(__file__).parents[1] / "fixtures" / "servers" / "hanging_mcp.py"
    parameters = StdioServerParameters(command=sys.executable, args=[str(hanging_server)])
    output = tmp_path / "timeout-smoke.json"

    with pytest.raises(RuntimeError, match="timeout"):
        await run_stdio_smoke(
            data_dir=FIXTURES,
            database_path=tmp_path / "timeout.sqlite3",
            csv_path=FIXTURES / "valid.csv",
            probe=PROBE,
            output=output,
            revision=SOURCE.revision,
            timeout_seconds=0.1,
            server_parameters=parameters,
        )

    assert not output.exists()


@pytest.mark.asyncio
async def test_stdio_smoke_rejects_revision_that_does_not_match_source(
    tmp_path: Path,
) -> None:
    output = tmp_path / "wrong-revision.json"

    with pytest.raises(RuntimeError, match="não corresponde"):
        await run_stdio_smoke(
            data_dir=FIXTURES,
            database_path=tmp_path / "wrong-revision.sqlite3",
            csv_path=FIXTURES / "valid.csv",
            probe=PROBE,
            output=output,
            revision="0" * 40,
            timeout_seconds=5,
        )

    assert not output.exists()
    assert not (tmp_path / "wrong-revision.sqlite3").exists()


@pytest.mark.asyncio
async def test_stdio_smoke_refuses_to_overwrite_existing_manifest(tmp_path: Path) -> None:
    output = tmp_path / "existing-manifest.json"
    output.write_text("preserve-me", encoding="utf-8")
    database = tmp_path / "unused.sqlite3"

    with pytest.raises(RuntimeError, match="Manifesto.*já existe"):
        await run_stdio_smoke(
            data_dir=FIXTURES,
            database_path=database,
            csv_path=FIXTURES / "valid.csv",
            probe=PROBE,
            output=output,
            revision=SOURCE.revision,
            timeout_seconds=5,
        )

    assert output.read_text(encoding="utf-8") == "preserve-me"
    assert not database.exists()


@pytest.mark.parametrize(
    "tool_name, content, loaded_records",
    [
        (
            "cnes_search_municipio",
            {
                "total_encontrados": 1,
                "estabelecimentos": [{"municipio": "Belém"}],
            },
            1,
        ),
        (
            "cnes_search_cnes",
            {"encontrado": True, "estabelecimento": {"cnes": "7654321"}},
            1,
        ),
        (
            "cnes_search_uf",
            {"total_encontrados": 1, "estabelecimentos": [{"uf": "PA"}]},
            1,
        ),
        ("cnes_statistics", {"total_estabelecimentos": 2}, 1),
    ],
)
def test_probe_validation_rejects_results_that_do_not_match_loaded_data(
    tool_name: str,
    content: dict[str, object],
    loaded_records: int,
) -> None:
    with pytest.raises(RuntimeError, match=tool_name):
        _validate_probe_result(
            tool_name,
            content,
            probe=PROBE,
            loaded_records=loaded_records,
        )
