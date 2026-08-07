"""Servidor oficial MCP v2 sobre os casos de uso da aplicação."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.types import CallToolResult, TextContent
from pydantic import Field

from mcp_cnes import __version__
from mcp_cnes.application import (
    GetStatistics,
    LoadData,
    SearchByCNES,
    SearchByMunicipality,
    SearchByUF,
)
from mcp_cnes.application.ports import CNESImporter, CNESRepository
from mcp_cnes.domain.errors import CNESDataLoadError, ImportSecurityError
from mcp_cnes.infrastructure.config import Settings, load_settings
from mcp_cnes.infrastructure.importers import CsvCNESImporter, SecureCsvImporter
from mcp_cnes.infrastructure.persistence import SQLiteCNESRepository

from .schemas import (
    BedFiltersOutput,
    CNESSearchOutput,
    DownloadInstructionsOutput,
    HospitalOutput,
    LoadDataOutput,
    MunicipalitySearchOutput,
    StatisticsOutput,
    UFSearchOutput,
)

MAX_RESULTS_PER_CALL = 500
TOOL_ARGUMENTS = {
    "cnes_load_data": frozenset({"filepath"}),
    "cnes_search_municipio": frozenset(
        {"municipio", "limit", "min_leitos", "max_leitos"}
    ),
    "cnes_search_cnes": frozenset({"cnes"}),
    "cnes_search_uf": frozenset({"uf", "limit", "min_leitos", "max_leitos"}),
    "cnes_statistics": frozenset(),
    "cnes_download_instructions": frozenset(),
}
BedMinimum = Annotated[int | None, Field(ge=0, description="Mínimo inclusivo de leitos")]
BedMaximum = Annotated[int | None, Field(ge=0, description="Máximo inclusivo de leitos")]
ResultLimit = Annotated[
    int,
    Field(ge=1, le=MAX_RESULTS_PER_CALL, description="Quantidade máxima de resultados"),
]


class StrictToolArgumentsMiddleware:
    """Rejeita parâmetros extras e os declara nos schemas anunciados."""

    async def __call__(
        self,
        ctx: ServerRequestContext[Any, Any],
        call_next: CallNext,
    ) -> HandlerResult:
        if ctx.method == "tools/call" and isinstance(ctx.params, Mapping):
            tool_name = ctx.params.get("name")
            arguments = ctx.params.get("arguments") or {}
            allowed = TOOL_ARGUMENTS.get(tool_name) if isinstance(tool_name, str) else None
            if allowed is not None and isinstance(arguments, Mapping):
                extras = sorted(set(arguments) - allowed)
                if extras:
                    names = ", ".join(extras)
                    return CallToolResult(
                        is_error=True,
                        content=[
                            TextContent(
                                type="text",
                                text=(
                                    f"Parâmetros não permitidos em {tool_name}: {names}. "
                                    "Remova-os e tente novamente."
                                ),
                            )
                        ],
                    )
                if tool_name == "cnes_search_cnes":
                    cnes = arguments.get("cnes")
                    if isinstance(cnes, str) and not re.fullmatch(r"\d{7}", cnes):
                        return CallToolResult(
                            is_error=True,
                            content=[
                                TextContent(
                                    type="text",
                                    text=(
                                        "cnes deve conter exatamente sete dígitos. "
                                        "Corrija o código e tente novamente."
                                    ),
                                )
                            ],
                        )

        result = await call_next(ctx)
        if ctx.method == "tools/list" and result is not None:
            tools = result.get("tools", []) if isinstance(result, dict) else getattr(result, "tools", [])
            for tool in tools:
                if isinstance(tool, dict):
                    schema = tool.get("inputSchema") or tool.get("input_schema")
                else:
                    schema = getattr(tool, "input_schema", None)
                if isinstance(schema, dict):
                    schema["additionalProperties"] = False
        return result


def _require_data(repository: CNESRepository) -> None:
    if not repository.has_data():
        raise ValueError("Dados não carregados. Chame cnes_load_data antes de consultar.")


def _safe_load_error(error: CNESDataLoadError) -> str:
    message = str(error)
    safe_messages = ("CSV sem cabeçalho", "CSV sem coluna CNES")
    if message in safe_messages:
        return f"{message}. Corrija o arquivo e tente novamente."
    return "Não foi possível ler o CSV informado. Verifique o formato e as permissões."


def create_mcp_server(
    *,
    settings: Settings | None = None,
    repository: CNESRepository | None = None,
    importer: CNESImporter | None = None,
) -> MCPServer:
    """Compõe o servidor sem iniciar transporte, rede ou leitura de arquivos."""

    runtime_settings = settings or load_settings()
    runtime_repository = repository or SQLiteCNESRepository(runtime_settings.database_path)
    runtime_importer = importer or SecureCsvImporter(
        CsvCNESImporter(),
        runtime_settings.data_dir,
        runtime_settings.max_csv_size_bytes,
        runtime_settings.allowed_csv_files,
    )

    load_data = LoadData(runtime_repository, runtime_importer)
    search_municipality = SearchByMunicipality(runtime_repository)
    search_cnes = SearchByCNES(runtime_repository)
    search_uf = SearchByUF(runtime_repository)
    get_statistics = GetStatistics(runtime_repository)

    server = MCPServer(
        name="mcp-cnes",
        title="MCP CNES",
        description="Consulta dados públicos de estabelecimentos de saúde do CNES.",
        version=__version__,
        log_level="WARNING",
        middleware=[StrictToolArgumentsMiddleware()],
    )

    @server.tool(name="cnes_load_data", structured_output=True)
    def cnes_load_data(
        filepath: Annotated[
            str,
            Field(min_length=1, description="Caminho para um arquivo CSV do CNES"),
        ],
    ) -> LoadDataOutput:
        """Carrega e consolida atomicamente um CSV exportado do CNES."""

        try:
            summary = load_data.execute(Path(filepath))
        except ImportSecurityError as exc:
            raise ValueError(
                f"{exc}. Verifique filepath e a politica configurada."
            ) from None
        except CNESDataLoadError as exc:
            raise ValueError(_safe_load_error(exc)) from None
        if summary.batch_id is None:
            raise RuntimeError("A persistencia nao retornou a identidade do lote")
        return LoadDataOutput(
            success=True,
            lote_id=summary.batch_id,
            registros_carregados=summary.records_loaded,
            linhas_lidas=summary.rows_read,
            linhas_aceitas=summary.records_loaded,
            linhas_rejeitadas=summary.rows_rejected,
            linhas_ignoradas=summary.rows_ignored,
            motivos_rejeicao={
                reason.code: reason.count for reason in summary.rejection_reasons
            },
            mensagem=f"Carregados {summary.records_loaded} estabelecimentos de saúde",
        )

    @server.tool(name="cnes_search_municipio", structured_output=True)
    def cnes_search_municipio(
        municipio: Annotated[
            str,
            Field(min_length=1, description="Nome parcial ou completo do município"),
        ],
        limit: ResultLimit = 50,
        min_leitos: BedMinimum = None,
        max_leitos: BedMaximum = None,
    ) -> MunicipalitySearchOutput:
        """Busca estabelecimentos por município e faixa opcional de leitos."""

        _require_data(runtime_repository)
        if not municipio.strip():
            raise ValueError("municipio não pode conter somente espaços.")
        result = search_municipality.execute(municipio, limit, min_leitos, max_leitos)
        return MunicipalitySearchOutput(
            municipio=municipio,
            total_encontrados=result.total_available,
            total_retornados=len(result.items),
            filtros_leitos=BedFiltersOutput(minimo=min_leitos, maximo=max_leitos),
            estabelecimentos=[HospitalOutput.from_domain(item) for item in result.items],
        )

    @server.tool(name="cnes_search_cnes", structured_output=True)
    def cnes_search_cnes(
        cnes: Annotated[
            str,
            Field(pattern=r"^\d{7}$", description="Código CNES com exatamente sete dígitos"),
        ],
    ) -> CNESSearchOutput:
        """Busca um estabelecimento pelo código CNES de sete dígitos."""

        _require_data(runtime_repository)
        result = search_cnes.execute(cnes)
        if result is None:
            return CNESSearchOutput(
                encontrado=False,
                mensagem=f"CNES {cnes} não encontrado",
            )
        return CNESSearchOutput(
            encontrado=True,
            estabelecimento=HospitalOutput.from_domain(result),
        )

    @server.tool(name="cnes_search_uf", structured_output=True)
    def cnes_search_uf(
        uf: Annotated[
            str,
            Field(
                min_length=2,
                max_length=2,
                pattern=r"^[A-Za-z]{2}$",
                description="Sigla da UF com duas letras",
            ),
        ],
        limit: ResultLimit = 100,
        min_leitos: BedMinimum = None,
        max_leitos: BedMaximum = None,
    ) -> UFSearchOutput:
        """Busca estabelecimentos por UF e faixa opcional de leitos."""

        _require_data(runtime_repository)
        result = search_uf.execute(uf, limit, min_leitos, max_leitos)
        return UFSearchOutput(
            uf=uf.upper(),
            total_encontrados=result.total_available,
            total_retornados=len(result.items),
            filtros_leitos=BedFiltersOutput(minimo=min_leitos, maximo=max_leitos),
            estabelecimentos=[HospitalOutput.from_domain(item) for item in result.items],
        )

    @server.tool(name="cnes_statistics", structured_output=True)
    def cnes_statistics() -> StatisticsOutput:
        """Retorna estatísticas dos dados atualmente carregados."""

        _require_data(runtime_repository)
        return StatisticsOutput.model_validate(get_statistics.execute())

    @server.tool(name="cnes_download_instructions", structured_output=True)
    def cnes_download_instructions() -> DownloadInstructionsOutput:
        """Explica como obter um CSV no dashboard oficial do CNES."""

        return DownloadInstructionsOutput(
            titulo="Instruções para Download de Dados CNES",
            url=runtime_settings.dashboard_url,
            passos=[
                "1. Acesse a URL do dashboard de leitos",
                "2. Aguarde o carregamento completo do dashboard",
                "3. Localize a tabela 'EXTRATO DOS LEITOS'",
                "4. Abra o menu de três pontos da tabela",
                "5. Selecione 'Download CSV'",
                "6. Aguarde o download do arquivo",
            ],
            colunas_disponiveis=[
                "COMPETÊNCIA",
                "UF",
                "MUNICÍPIO",
                "CNES",
                "NOME_FANTASIA",
                "TIPO_ESTABELECIMENTO",
                "NATUREZA_JURÍDICA",
                "GESTÃO",
                "CONVÊNIO_SUS",
                "LEITOS_EXISTENTES",
                "LEITOS_SUS",
            ],
            apos_download="Use cnes_load_data para carregar o CSV baixado.",
        )

    return server
