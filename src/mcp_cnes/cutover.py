"""Smoke test operacional e manifesto auditável para o cutover MCP."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import MCPError

from mcp_cnes.domain.rules import normalize_search_text

EXPECTED_TOOL_NAMES = (
    "cnes_load_data",
    "cnes_search_municipio",
    "cnes_search_cnes",
    "cnes_search_uf",
    "cnes_statistics",
    "cnes_download_instructions",
)
MCP_REQUEST_TIMEOUT = -32001


@dataclass(frozen=True, slots=True)
class SmokeProbe:
    """Identificadores públicos usados para validar as ferramentas de consulta."""

    municipio: str
    uf: str
    cnes: str


@dataclass(frozen=True, slots=True)
class SourceAttestation:
    """Identidade verificável do checkout que executa o smoke."""

    revision: str
    sha256: str
    dirty: bool


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git(project_root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), *arguments],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Não foi possível identificar a revisão Git do checkout") from exc
    if result.returncode != 0:
        raise RuntimeError("Não foi possível identificar a revisão Git do checkout")
    return result.stdout.strip()


def _source_digest(project_root: Path) -> str:
    candidates = [
        project_root / "pyproject.toml",
        project_root / "uv.lock",
        project_root / "README.md",
        *(project_root / "src" / "mcp_cnes").rglob("*.py"),
    ]
    digest = hashlib.sha256()
    for path in sorted(candidates, key=lambda item: item.as_posix()):
        relative = path.relative_to(project_root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def inspect_source_attestation(project_root: Path | None = None) -> SourceAttestation:
    """Lê HEAD, estado do runtime e digest do código/lockfile executados."""

    root = (project_root or _project_root()).resolve()
    revision = _git(root, "rev-parse", "HEAD")
    status = _git(
        root,
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        "src/mcp_cnes",
        "pyproject.toml",
        "uv.lock",
        "README.md",
    )
    return SourceAttestation(
        revision=revision,
        sha256=_source_digest(root),
        dirty=bool(status),
    )


def _schema_hash(input_schema: dict[str, Any], output_schema: dict[str, Any] | None) -> str:
    payload = json.dumps(
        {"input": input_schema, "output": output_schema},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    except FileExistsError:
        raise RuntimeError("Manifesto de smoke já existe; informe outro --output") from None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _establishments(content: dict[str, Any]) -> list[dict[str, Any]]:
    value = content.get("estabelecimentos")
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        return []
    return value


def _validate_probe_result(
    tool_name: str,
    content: dict[str, Any],
    *,
    probe: SmokeProbe,
    loaded_records: int | None,
) -> None:
    establishments = _establishments(content)
    if tool_name == "cnes_load_data":
        valid = _positive_int(content.get("registros_carregados")) is not None
    elif tool_name == "cnes_search_municipio":
        query = normalize_search_text(probe.municipio)
        valid = _positive_int(content.get("total_encontrados")) is not None and bool(
            establishments
            and all(
                query in normalize_search_text(str(item.get("municipio", "")))
                for item in establishments
            )
        )
    elif tool_name == "cnes_search_cnes":
        establishment = content.get("estabelecimento")
        valid = (
            content.get("encontrado") is True
            and isinstance(establishment, dict)
            and establishment.get("cnes") == probe.cnes
        )
    elif tool_name == "cnes_search_uf":
        valid = _positive_int(content.get("total_encontrados")) is not None and bool(
            establishments
            and all(str(item.get("uf", "")).upper() == probe.uf.upper() for item in establishments)
        )
    elif tool_name == "cnes_statistics":
        valid = (
            loaded_records is not None
            and _positive_int(content.get("total_estabelecimentos")) == loaded_records
        )
    elif tool_name == "cnes_download_instructions":
        url = content.get("url")
        steps = content.get("passos")
        valid = (
            isinstance(url, str)
            and url.startswith(("https://", "http://"))
            and isinstance(steps, list)
            and bool(steps)
        )
    else:
        valid = False
    if not valid:
        raise RuntimeError(f"Smoke do cutover não confirmou dados em {tool_name}")


def _contains_timeout(error: BaseException) -> bool:
    if isinstance(error, TimeoutError):
        return True
    if isinstance(error, MCPError) and error.error.code == MCP_REQUEST_TIMEOUT:
        return True
    if isinstance(error, BaseExceptionGroup):
        return any(_contains_timeout(item) for item in error.exceptions)
    return False


def _validated_revision(revision: str) -> str:
    cleaned = revision.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40}", cleaned):
        raise RuntimeError("revision deve ser o SHA completo do commit aprovado")
    return cleaned.lower()


async def run_stdio_smoke(
    *,
    data_dir: Path,
    database_path: Path,
    csv_path: Path,
    probe: SmokeProbe,
    output: Path,
    revision: str,
    timeout_seconds: float = 30,
    server_parameters: StdioServerParameters | None = None,
) -> dict[str, Any]:
    """Inicia o entrypoint oficial, chama as seis tools e persiste um manifesto."""

    if timeout_seconds <= 0:
        raise RuntimeError("timeout_seconds deve ser maior que zero")
    approved_revision = _validated_revision(revision)
    source = inspect_source_attestation()
    if approved_revision != source.revision.lower():
        raise RuntimeError("revision informada não corresponde ao checkout executado")
    if output.exists():
        raise RuntimeError("Manifesto de smoke já existe; informe outro --output")
    resolved_database = database_path.resolve()
    if resolved_database.exists():
        raise RuntimeError("Banco do smoke já existe; informe um caminho novo e descartável")

    environment = dict(os.environ)
    environment.update(
        {
            "PYTHONUNBUFFERED": "1",
            "MCP_CNES_DATA_DIR": str(data_dir.resolve()),
            "MCP_CNES_DATABASE_PATH": str(resolved_database),
            "MCP_CNES_ALLOWED_CSV_FILES": csv_path.name,
        }
    )
    parameters = server_parameters or StdioServerParameters(
        command=sys.executable, args=["-m", "mcp_cnes"], env=environment
    )
    calls = [
        ("cnes_load_data", {"filepath": str(csv_path.resolve())}),
        ("cnes_search_municipio", {"municipio": probe.municipio, "limit": 1}),
        ("cnes_search_cnes", {"cnes": probe.cnes}),
        ("cnes_search_uf", {"uf": probe.uf, "limit": 1}),
        ("cnes_statistics", {}),
        ("cnes_download_instructions", {}),
    ]

    failure: RuntimeError | None = None
    report: dict[str, Any] | None = None
    try:
        async with Client(stdio_client(parameters), read_timeout_seconds=timeout_seconds) as client:
            listed = await client.list_tools(cache_mode="bypass")
            listed_names = tuple(tool.name for tool in listed.tools)
            if listed_names != EXPECTED_TOOL_NAMES:
                failure = RuntimeError("Catálogo MCP divergiu das seis ferramentas aprovadas")
            call_evidence: list[dict[str, str]] = []
            loaded_records: int | None = None
            for tool_name, arguments in calls:
                if failure is not None:
                    break
                result = await client.call_tool(tool_name, arguments)
                if result.is_error:
                    failure = RuntimeError(f"Smoke do cutover falhou em {tool_name}")
                    break
                content = result.structured_content or {}
                try:
                    _validate_probe_result(
                        tool_name,
                        content,
                        probe=probe,
                        loaded_records=loaded_records,
                    )
                except RuntimeError as exc:
                    failure = exc
                    break
                if tool_name == "cnes_load_data":
                    loaded_records = int(content["registros_carregados"])
                call_evidence.append({"name": tool_name, "status": "ok"})

            if failure is None:
                server_info = client.server_info
                if server_info is None:
                    failure = RuntimeError("Servidor não informou identidade durante o handshake")
                else:
                    report = {
                        "generated_at": datetime.now(UTC).isoformat(),
                        "server": {
                            "name": server_info.name,
                            "version": server_info.version,
                        },
                        "source": {
                            "revision": source.revision,
                            "sha256": source.sha256,
                            "dirty": source.dirty,
                        },
                        "protocol_version": client.protocol_version,
                        "timeout_seconds": timeout_seconds,
                        "schemas": [
                            {
                                "name": tool.name,
                                "sha256": _schema_hash(tool.input_schema, tool.output_schema),
                            }
                            for tool in listed.tools
                        ],
                        "import": {
                            "source_file": csv_path.name,
                            "records_loaded": loaded_records,
                        },
                        "calls": call_evidence,
                    }
    except BaseException as exc:
        if _contains_timeout(exc):
            raise RuntimeError(
                f"Smoke MCP excedeu o timeout de {timeout_seconds:g} segundos"
            ) from None
        raise

    if failure is not None:
        raise failure
    if report is None:
        raise RuntimeError("Smoke do cutover não gerou manifesto")
    _write_report(output, report)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Executa smoke stdio das seis ferramentas e grava evidência JSON."
    )
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--database-path", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--municipio", required=True)
    parser.add_argument("--uf", required=True)
    parser.add_argument("--cnes", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=30)
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    try:
        asyncio.run(
            run_stdio_smoke(
                data_dir=args.data_dir,
                database_path=args.database_path,
                csv_path=args.csv,
                probe=SmokeProbe(municipio=args.municipio, uf=args.uf, cnes=args.cnes),
                output=args.output,
                revision=args.revision,
                timeout_seconds=args.timeout_seconds,
            )
        )
    except RuntimeError as exc:
        parser.exit(1, f"erro: {exc}\n")


if __name__ == "__main__":
    main()
