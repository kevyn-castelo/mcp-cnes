from __future__ import annotations

from pathlib import Path

import pytest
from mcp import Client

from mcp_cnes.domain.errors import ImportSecurityError
from mcp_cnes.infrastructure.config import Settings
from mcp_cnes.infrastructure.importers import CsvCNESImporter, SecureCsvImporter
from mcp_cnes.interfaces.mcp import create_mcp_server


def policy(data_dir: Path, *, max_size: int = 1024, allowed: tuple[str, ...] = ()):
    return SecureCsvImporter(CsvCNESImporter(), data_dir, max_size, allowed)


def write_valid(path: Path) -> None:
    path.write_text("CNES,MUNICIPIO,UF\n1234567,Manaus,AM\n", encoding="utf-8")


def test_accepts_csv_inside_configured_directory(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    valid = data_dir / "valid.csv"
    write_valid(valid)

    batch = policy(data_dir, allowed=("valid.csv",)).import_file(valid)

    assert batch.summary.records_loaded == 1
    assert batch.source_sha256 is not None


@pytest.mark.parametrize("candidate_name", ["invalid.txt", "not-allowed.csv"])
def test_rejects_invalid_extension_and_allowlist(tmp_path: Path, candidate_name: str) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    candidate = data_dir / candidate_name
    write_valid(candidate)

    with pytest.raises(ImportSecurityError):
        policy(data_dir, allowed=("valid.csv",)).import_file(candidate)


def test_rejects_outside_path_traversal_and_oversized_file(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    outside = tmp_path / "outside.csv"
    write_valid(outside)
    oversized = data_dir / "oversized.csv"
    write_valid(oversized)

    with pytest.raises(ImportSecurityError):
        policy(data_dir).import_file(outside)
    with pytest.raises(ImportSecurityError):
        policy(data_dir).import_file(Path("..") / "outside.csv")
    with pytest.raises(ImportSecurityError, match="tamanho maximo"):
        policy(data_dir, max_size=1).import_file(oversized)


def test_rejects_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    outside = tmp_path / "outside.csv"
    write_valid(outside)
    link = data_dir / "link.csv"
    original_resolve = Path.resolve

    def resolve_like_external_symlink(path: Path, strict: bool = False) -> Path:
        if path.name == "link.csv":
            return original_resolve(outside, strict=strict)
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve_like_external_symlink)

    with pytest.raises(ImportSecurityError):
        policy(data_dir).import_file(link)


@pytest.mark.asyncio
async def test_mcp_load_reports_batch_and_aggregated_rejections(tmp_path: Path) -> None:
    fixtures = Path(__file__).parents[1] / "fixtures" / "csv"
    server = create_mcp_server(
        settings=Settings(
            data_dir=fixtures,
            database_path=tmp_path / "cnes.sqlite3",
            allowed_csv_files=("invalid_rows.csv",),
        )
    )

    async with Client(server) as client:
        first = await client.call_tool(
            "cnes_load_data", {"filepath": str(fixtures / "invalid_rows.csv")}
        )
        second = await client.call_tool(
            "cnes_load_data", {"filepath": str(fixtures / "invalid_rows.csv")}
        )

    assert first.is_error is False
    assert first.structured_content["lote_id"] == second.structured_content["lote_id"]
    assert first.structured_content["linhas_aceitas"] == 1
    assert first.structured_content["linhas_rejeitadas"] == 1
    assert first.structured_content["motivos_rejeicao"] == {"valor_invalido": 1}
    assert "valor_invalido" in str(first.structured_content)
    assert "não-numérico" not in str(first.structured_content)
