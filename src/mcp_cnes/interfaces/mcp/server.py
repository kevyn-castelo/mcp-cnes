"""Servidor oficial MCP v2 sobre os casos de uso da aplicação."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Annotated, Any, ParamSpec, TypeVar, cast

from mcp.server import MCPServer
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.types import CallToolResult, TextContent
from pydantic import Field

from mcp_cnes import __version__
from mcp_cnes.application import (
    AdvancedSearch,
    AdvancedSearchV2,
    AggregateData,
    DiffBatches,
    ExportData,
    FetchRemoteData,
    GetStatistics,
    GroupByMaintainer,
    LeadTriggers,
    ListBatches,
    ListRemoteCompetences,
    ListRemoteResources,
    LoadData,
    NormalizeData,
    PurgeBatch,
    ScoreLeads,
    SearchByCNES,
    SearchByMunicipality,
    SearchByUF,
    TimeSeries,
    UseBatch,
    ValidateDataset,
)
from mcp_cnes.application.ports import (
    CNESCatalogRepository,
    CNESColumnarRepository,
    CNESImporter,
    CNESRemoteSource,
    CNESRepository,
)
from mcp_cnes.domain.errors import (
    CNESDataLoadError,
    CollectorError,
    DomainValidationError,
    ImportSecurityError,
)
from mcp_cnes.infrastructure.config import Settings, load_settings
from mcp_cnes.infrastructure.exports import LocalDatasetExporter
from mcp_cnes.infrastructure.importers import CsvCNESImporter, SecureCsvImporter
from mcp_cnes.infrastructure.persistence import DuckDBCNESRepository
from mcp_cnes.infrastructure.sources import DatasusFullRemoteSource, PortalSUSRemoteSource

from .schemas import (
    ActiveBatchOutput,
    AdvancedFiltersInput,
    AdvancedSearchOutput,
    AdvancedSearchV2Output,
    AggregateOutput,
    AggregatePointOutput,
    BatchListOutput,
    BatchOutput,
    BedFiltersOutput,
    CNESSearchOutput,
    CompetenceListOutput,
    DatasetValidationOutput,
    DiffOutput,
    DownloadInstructionsOutput,
    ExportOutput,
    HospitalOutput,
    HospitalV2Output,
    LeadScoreOutput,
    LeadScoresOutput,
    LeadScoreWeightsInput,
    LeadTriggersOutput,
    LoadDataOutput,
    MaintainerGroupOutput,
    MaintainerGroupsOutput,
    MunicipalitySearchOutput,
    NormalizeOutput,
    PurgeOutput,
    RemoteFetchOutput,
    SourceListOutput,
    SourceOutput,
    StatisticsOutput,
    TimeSeriesOutput,
    TimeSeriesPointOutput,
    UFSearchOutput,
)

MAX_RESULTS_PER_CALL = 500
PUBLIC_INTERNAL_ERROR = "Não foi possível concluir a operação solicitada."
MAX_PUBLIC_ERROR_LENGTH = 500
PUBLIC_ERROR_MARKER = "mcp_cnes_public_error_v1"
P = ParamSpec("P")
T = TypeVar("T")
TOOL_ARGUMENTS = {
    "cnes_load_data": frozenset({"filepath"}),
    "cnes_search_municipio": frozenset(
        {
            "municipio",
            "uf",
            "tipo_estabelecimento",
            "natureza_juridica",
            "gestao",
            "convenio_sus",
            "min_leitos",
            "max_leitos",
            "order_by",
            "limit",
        }
    ),
    "cnes_search_cnes": frozenset({"cnes"}),
    "cnes_search_uf": frozenset(
        {
            "uf",
            "municipio",
            "tipo_estabelecimento",
            "natureza_juridica",
            "gestao",
            "convenio_sus",
            "min_leitos",
            "max_leitos",
            "order_by",
            "limit",
        }
    ),
    "cnes_statistics": frozenset(),
    "cnes_download_instructions": frozenset(),
    "cnes_list_sources": frozenset(),
    "cnes_list_competencias": frozenset({"fonte", "ano"}),
    "cnes_fetch": frozenset(
        {
            "competencia",
            "uf",
            "municipio",
            "tipo_estabelecimento",
            "natureza_juridica",
            "gestao",
            "convenio_sus",
            "min_leitos",
            "max_leitos",
            "fonte",
            "auto_load",
            "dest_dir",
        }
    ),
    "cnes_validate_dataset": frozenset({"lote_id"}),
    "cnes_list_lotes": frozenset(),
    "cnes_use_lote": frozenset({"lote_id"}),
    "cnes_purge": frozenset({"lote_id", "confirmacao"}),
    "cnes_aggregate": frozenset({"group_by", "metrica", "filtros", "lote_id"}),
    "cnes_timeseries": frozenset({"chave", "tipo_chave", "de", "ate"}),
    "cnes_diff": frozenset({"lote_a", "lote_b"}),
    "cnes_search_advanced": frozenset({"filtros", "order_by", "offset", "limit", "lote_id"}),
    "cnes_search_advanced_v2": frozenset({"filtros", "order_by", "offset", "limit", "lote_id"}),
    "cnes_group_by_mantenedora": frozenset({"filtros", "limit", "lote_id"}),
    "cnes_leads_triggers": frozenset(
        {
            "competencia_a",
            "competencia_b",
            "delta_min",
            "tipo_estabelecimento",
            "lote_a",
            "lote_b",
        }
    ),
    "cnes_score_leads": frozenset(
        {"competencia_a", "competencia_b", "pesos", "filtros", "limit", "lote_a", "lote_b"}
    ),
    "cnes_normalize": frozenset({"filepath", "origem", "destino"}),
    "cnes_export": frozenset(
        {
            "formato",
            "filtros",
            "destino",
            "lote_id",
            "cnes_list",
            "limit",
            "offset",
            "order_by",
            "perfil_saida",
        }
    ),
}
BedMinimum = Annotated[int | None, Field(ge=0, description="Mínimo inclusivo de leitos")]
BedMaximum = Annotated[int | None, Field(ge=0, description="Máximo inclusivo de leitos")]
ResultLimit = Annotated[
    int,
    Field(ge=1, le=MAX_RESULTS_PER_CALL, description="Quantidade máxima de resultados"),
]


SearchOrder = Annotated[
    str,
    Field(
        pattern=r"^(cnes|municipio|leitos_existentes|leitos_sus)$",
        description="Crescente para cnes/municipio; decrescente para campos de leitos",
    ),
]


def _sanitize_public_text(message: str, *, fallback: str = PUBLIC_INTERNAL_ERROR) -> str:
    """Limita e remove detalhes de infraestrutura de mensagens públicas."""

    collapsed = " ".join(message.split())
    if not collapsed:
        return fallback
    collapsed = re.sub(
        r"(?i)\b(https?://)[^/\s@]+@",
        r"\1[credenciais omitidas]@",
        collapsed,
    )
    collapsed = re.sub(
        r'''(?i)["'](?:[A-Z]:[\\/]|/|\\\\)[^"']+["']''',
        "[caminho omitido]",
        collapsed,
    )
    collapsed = re.sub(
        r"(?i)\b[A-Z]:[\\/][^:;,]+",
        "[caminho omitido]",
        collapsed,
    )
    collapsed = re.sub(
        r"(?<![:/\w])/(?!/)[^:;,]+",
        "[caminho omitido]",
        collapsed,
    )
    collapsed = re.sub(
        r"\\\\[^:;,]+",
        "[caminho omitido]",
        collapsed,
    )
    sensitive_markers = (
        "traceback (most recent call last)",
        "file \"",
        "duckdb.",
        "sqlite3.",
        "binder error",
        "catalog error",
        "database error",
        "operationalerror",
        "integrityerror",
        "select * from",
        "insert into",
    )
    if any(marker in collapsed.casefold() for marker in sensitive_markers):
        return fallback
    if len(collapsed) > MAX_PUBLIC_ERROR_LENGTH:
        return collapsed[: MAX_PUBLIC_ERROR_LENGTH - 1].rstrip() + "…"
    return collapsed


def _public_error_text(
    cause: str,
    *,
    code: str = "invalid_request",
    suggestion: str = "Revise os parâmetros informados e tente novamente.",
    include_marker: bool = False,
) -> str:
    public_cause = _sanitize_public_text(cause)
    public_code = code if re.fullmatch(r"[a-z0-9_]{1,64}", code) else "invalid_request"
    if public_cause == PUBLIC_INTERNAL_ERROR:
        public_code = "internal_error"
    payload = {
        "erro": public_code,
        "causa": public_cause,
        "sugestao": _sanitize_public_text(
            suggestion,
            fallback="Revise os parâmetros informados e tente novamente.",
        ),
    }
    if include_marker:
        payload["_mcp_cnes_public_error"] = PUBLIC_ERROR_MARKER
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class PublicToolError(ValueError):
    """Erro deliberadamente seguro para publicação na fronteira MCP."""

    def __init__(
        self,
        cause: str,
        *,
        code: str = "invalid_request",
        suggestion: str = "Revise os parâmetros informados e tente novamente.",
    ) -> None:
        super().__init__(
            _public_error_text(
                cause,
                code=code,
                suggestion=suggestion,
                include_marker=True,
            )
        )


def _execute_public(
    operation: Callable[..., T], /, *args: Any, **kwargs: Any
) -> T:
    """Publica somente validações tipadas do contrato da aplicação."""

    try:
        return operation(*args, **kwargs)
    except DomainValidationError as exc:
        raise PublicToolError(str(exc)) from None


def _guard_tool(operation: Callable[P, T]) -> Callable[P, T]:
    """Converte falhas não classificadas antes que o SDK forme a resposta MCP."""

    @wraps(operation)
    def guarded(*args: Any, **kwargs: Any) -> Any:
        try:
            return operation(*args, **kwargs)
        except PublicToolError:
            raise
        except Exception:
            raise PublicToolError(
                PUBLIC_INTERNAL_ERROR,
                code="internal_error",
                suggestion="Tente novamente; se o problema persistir, contate o operador.",
            ) from None

    return cast(Callable[P, T], guarded)


def _structured_error_text(message: str, *, code: str = "invalid_request") -> str:
    start = message.find("{")
    if start >= 0:
        try:
            existing = json.JSONDecoder().decode(message[start:])
        except json.JSONDecodeError:
            pass
        else:
            if (
                isinstance(existing, dict)
                and existing.get("_mcp_cnes_public_error") == PUBLIC_ERROR_MARKER
                and {"erro", "causa", "sugestao"}.issubset(existing)
            ):
                public_code = str(existing["erro"])
                if not re.fullmatch(r"[a-z0-9_]{1,64}", public_code):
                    public_code = code
                return _public_error_text(
                    str(existing["causa"]),
                    code=public_code,
                    suggestion=str(existing["sugestao"]),
                )
    return _public_error_text(
        PUBLIC_INTERNAL_ERROR,
        code="internal_error",
        suggestion="Tente novamente; se o problema persistir, contate o operador.",
    )


def _tool_error(message: str, *, code: str = "invalid_request") -> CallToolResult:
    return CallToolResult(
        is_error=True,
        content=[TextContent(type="text", text=_public_error_text(message, code=code))],
    )


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
                    return _tool_error(
                        f"Parâmetros não permitidos em {tool_name}: {names}. "
                        "Remova-os e tente novamente.",
                        code="unexpected_parameters",
                    )
                if tool_name == "cnes_search_cnes":
                    cnes = arguments.get("cnes")
                    if isinstance(cnes, str) and not re.fullmatch(r"\d{7}", cnes):
                        return _tool_error(
                            "cnes deve conter exatamente sete dígitos. "
                            "Corrija o código e tente novamente.",
                            code="invalid_cnes",
                        )
                if tool_name in {"cnes_search_municipio", "cnes_search_uf"}:
                    uf = arguments.get("uf")
                    if isinstance(uf, str) and not re.fullmatch(r"[A-Za-z]{2}", uf):
                        return _tool_error(
                            "uf deve conter exatamente duas letras.",
                            code="invalid_uf",
                        )
                    limit = arguments.get("limit")
                    if isinstance(limit, int) and not 1 <= limit <= MAX_RESULTS_PER_CALL:
                        return _tool_error(
                            "limit deve estar entre 1 e 500.",
                            code="invalid_limit",
                        )
                    for field in ("min_leitos", "max_leitos"):
                        beds = arguments.get(field)
                        if isinstance(beds, int) and beds < 0:
                            return _tool_error(
                                f"{field} deve ser maior ou igual a zero.",
                                code="invalid_bed_range",
                            )

        try:
            result = await call_next(ctx)
        except PublicToolError as exc:
            return CallToolResult(
                is_error=True,
                content=[TextContent(type="text", text=_structured_error_text(str(exc)))],
            )
        except ValueError:
            return _tool_error(PUBLIC_INTERNAL_ERROR, code="internal_error")
        except Exception:
            return _tool_error(
                PUBLIC_INTERNAL_ERROR,
                code="internal_error",
            )
        if ctx.method == "tools/call":
            is_error = (
                result.get("isError", result.get("is_error", False))
                if isinstance(result, dict)
                else getattr(result, "is_error", False)
            )
            content = (
                result.get("content", [])
                if isinstance(result, dict)
                else getattr(result, "content", [])
            )
            if is_error:
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        block["text"] = _structured_error_text(block["text"])
                    elif isinstance(block, TextContent):
                        block.text = _structured_error_text(block.text)
        if ctx.method == "tools/list" and result is not None:
            tools = (
                result.get("tools", [])
                if isinstance(result, dict)
                else getattr(result, "tools", [])
            )
            for tool in tools:
                if isinstance(tool, dict):
                    schema = tool.get("inputSchema") or tool.get("input_schema")
                    tool_name = tool.get("name")
                else:
                    schema = getattr(tool, "input_schema", None)
                    tool_name = getattr(tool, "name", None)
                if isinstance(schema, dict):
                    schema["additionalProperties"] = False
                    v2_tools = {
                        "cnes_search_advanced_v2",
                        "cnes_group_by_mantenedora",
                        "cnes_leads_triggers",
                        "cnes_score_leads",
                    }
                    schema["x-cnes-contract-version"] = "v2" if tool_name in v2_tools else "v1"
                    if tool_name == "cnes_export":
                        schema["x-cnes-contract-versions"] = ["v1", "v2"]
        return result


def _require_data(repository: CNESRepository) -> None:
    if not repository.has_data():
        raise PublicToolError(
            "Dados não carregados. Chame cnes_load_data antes de consultar."
        )


def _safe_load_error(error: CNESDataLoadError) -> str:
    message = str(error)
    safe_messages = ("CSV sem cabeçalho", "CSV sem coluna CNES")
    if message in safe_messages:
        return f"{message}. Corrija o arquivo e tente novamente."
    return "Não foi possível ler o CSV informado. Verifique o formato e as permissões."


def _public_remote_cause(error: CollectorError) -> str:
    causes = {
        "remote_competence_not_found": "A competência solicitada não foi encontrada na fonte oficial.",
        "remote_competence_unavailable": "A fonte oficial não possui competências para o período solicitado.",
        "remote_server_error": "A fonte remota está temporariamente indisponível.",
        "remote_rate_limited": "A fonte remota limitou temporariamente as solicitações.",
        "remote_destination_not_allowed": (
            "O destino deve permanecer dentro do diretório remoto configurado."
        ),
    }
    return causes.get(error.code, "Não foi possível consultar a fonte remota.")


def _raise_remote_error(error: CollectorError) -> None:
    suggestions = {
        "remote_competence_not_found": "Revise a competência e remova filtros muito restritivos.",
        "remote_competence_unavailable": (
            "Use cnes_list_sources para ver os anos ou omita ano em cnes_list_competencias."
        ),
        "remote_server_error": "Tente novamente mais tarde; o retry automático já foi esgotado.",
        "remote_rate_limited": "Aguarde antes de repetir a coleta.",
    }
    payload = {
        "erro": error.code,
        "causa": _public_remote_cause(error),
        "sugestao": suggestions.get(
            error.code,
            "Verifique a disponibilidade da fonte e os parâmetros informados.",
        ),
    }
    raise PublicToolError(
        payload["causa"],
        code=payload["erro"],
        suggestion=payload["sugestao"],
    ) from None


def _public_file_reference(path: Path, root: Path) -> str:
    """Retorna uma referência relativa sem revelar a estrutura do host."""

    resolved = path.resolve(strict=False)
    base = root.resolve(strict=False)
    try:
        relative = resolved.relative_to(base)
    except ValueError:
        return path.name
    reference = relative.as_posix()
    return reference if reference and reference != "." else path.name


def _public_source_reference(value: str | None) -> str | None:
    if value is None:
        return None
    return Path(value).name


def create_mcp_server(
    *,
    settings: Settings | None = None,
    repository: CNESCatalogRepository | None = None,
    importer: CNESImporter | None = None,
    remote_source: CNESRemoteSource | None = None,
    remote_sources: Mapping[str, CNESRemoteSource] | None = None,
) -> MCPServer:
    """Compõe o servidor sem iniciar transporte, rede ou leitura de arquivos."""

    runtime_settings = settings or load_settings()
    default_columnar_path = Settings().columnar_database_path
    columnar_path = runtime_settings.columnar_database_path
    if (
        columnar_path == default_columnar_path
        and runtime_settings.database_path != Settings().database_path
    ):
        columnar_path = runtime_settings.database_path.with_suffix(".duckdb")
    columnar_dir = runtime_settings.columnar_dir
    if columnar_dir == Settings().columnar_dir and columnar_path != default_columnar_path:
        columnar_dir = columnar_path.parent / "parquet"
    runtime_repository = repository or DuckDBCNESRepository(
        columnar_path,
        columnar_dir=columnar_dir,
        batch_retention_count=runtime_settings.batch_retention_count,
    )
    runtime_importer = importer or SecureCsvImporter(
        CsvCNESImporter(),
        runtime_settings.data_dir,
        runtime_settings.max_csv_size_bytes,
        runtime_settings.allowed_csv_files,
    )
    if remote_sources is not None and remote_source is not None:
        raise ValueError("Use remote_source ou remote_sources, não ambos")
    if remote_sources is not None:
        runtime_remote_sources = dict(remote_sources)
    elif remote_source is not None:
        runtime_remote_sources = {remote_source.name: remote_source}
    else:
        portal_source = PortalSUSRemoteSource(runtime_settings)
        full_source = DatasusFullRemoteSource(runtime_settings)
        runtime_remote_sources = {
            portal_source.name: portal_source,
            full_source.name: full_source,
        }
    default_remote_source_name = "portal_sus_hospitais_leitos"
    if default_remote_source_name not in runtime_remote_sources:
        default_remote_source_name = next(iter(runtime_remote_sources))

    def select_remote_source(name: str | None) -> CNESRemoteSource:
        selected = name or default_remote_source_name
        try:
            return runtime_remote_sources[selected]
        except KeyError:
            available = ", ".join(sorted(runtime_remote_sources))
            raise PublicToolError(
                f"fonte desconhecida: {selected}. Fontes disponíveis: {available}."
            ) from None

    load_data = LoadData(runtime_repository, runtime_importer)
    search_municipality = SearchByMunicipality(runtime_repository)
    search_cnes = SearchByCNES(runtime_repository)
    search_uf = SearchByUF(runtime_repository)
    get_statistics = GetStatistics(runtime_repository)
    catalog_repository = runtime_repository
    validate_dataset = ValidateDataset(catalog_repository)
    list_batches = ListBatches(catalog_repository)
    use_batch = UseBatch(catalog_repository)
    purge_batch = PurgeBatch(catalog_repository)
    aggregate_data = AggregateData(catalog_repository)
    time_series = TimeSeries(catalog_repository)
    diff_batches = DiffBatches(catalog_repository)
    advanced_search = AdvancedSearch(catalog_repository)
    advanced_search_v2 = AdvancedSearchV2(cast(CNESColumnarRepository, catalog_repository))
    group_by_maintainer = GroupByMaintainer(cast(CNESColumnarRepository, catalog_repository))
    lead_triggers = LeadTriggers(cast(CNESColumnarRepository, catalog_repository))
    score_leads = ScoreLeads(cast(CNESColumnarRepository, catalog_repository))
    dataset_exporter = LocalDatasetExporter(runtime_settings.output_dir)
    normalize_data = NormalizeData(runtime_importer, dataset_exporter)
    export_data = ExportData(catalog_repository, dataset_exporter)

    server = MCPServer(
        name="mcp-cnes",
        title="MCP CNES",
        description="Consulta dados públicos de estabelecimentos de saúde do CNES.",
        version=__version__,
        log_level="WARNING",
        middleware=[StrictToolArgumentsMiddleware()],
    )

    @server.tool(name="cnes_load_data", structured_output=True)
    @_guard_tool
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
            raise PublicToolError(
                f"{exc}. Verifique filepath e a politica configurada."
            ) from None
        except CNESDataLoadError as exc:
            raise PublicToolError(_safe_load_error(exc)) from None
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
            motivos_rejeicao={reason.code: reason.count for reason in summary.rejection_reasons},
            mensagem=f"Carregados {summary.records_loaded} estabelecimentos de saúde",
        )

    @server.tool(name="cnes_search_municipio", structured_output=True)
    @_guard_tool
    def cnes_search_municipio(
        municipio: Annotated[
            str,
            Field(min_length=1, description="Nome parcial ou completo do município"),
        ],
        limit: ResultLimit = 50,
        min_leitos: BedMinimum = None,
        max_leitos: BedMaximum = None,
        uf: Annotated[str | None, Field(pattern=r"^[A-Za-z]{2}$")] = None,
        tipo_estabelecimento: Annotated[str | None, Field(min_length=1)] = None,
        natureza_juridica: Annotated[str | None, Field(min_length=1)] = None,
        gestao: Annotated[str | None, Field(min_length=1)] = None,
        convenio_sus: bool | None = None,
        order_by: SearchOrder = "leitos_existentes",
    ) -> MunicipalitySearchOutput:
        """Busca estabelecimentos por município e faixa opcional de leitos."""

        _require_data(runtime_repository)
        if not municipio.strip():
            raise PublicToolError("municipio não pode conter somente espaços.")
        result = _execute_public(
            search_municipality.execute,
            municipio,
            limit,
            min_leitos,
            max_leitos,
            uf=uf,
            establishment_type=tipo_estabelecimento,
            legal_nature=natureza_juridica,
            management=gestao,
            sus_agreement=convenio_sus,
            order_by=order_by,
        )
        return MunicipalitySearchOutput(
            municipio=municipio,
            total_encontrados=result.total_available,
            total_retornados=len(result.items),
            filtros_leitos=BedFiltersOutput(minimo=min_leitos, maximo=max_leitos),
            estabelecimentos=[HospitalOutput.from_domain(item) for item in result.items],
        )

    @server.tool(name="cnes_search_cnes", structured_output=True)
    @_guard_tool
    def cnes_search_cnes(
        cnes: Annotated[
            str,
            Field(pattern=r"^\d{7}$", description="Código CNES com exatamente sete dígitos"),
        ],
    ) -> CNESSearchOutput:
        """Busca um estabelecimento pelo código CNES de sete dígitos."""

        _require_data(runtime_repository)
        result = _execute_public(search_cnes.execute, cnes)
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
    @_guard_tool
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
        municipio: Annotated[str | None, Field(min_length=1)] = None,
        tipo_estabelecimento: Annotated[str | None, Field(min_length=1)] = None,
        natureza_juridica: Annotated[str | None, Field(min_length=1)] = None,
        gestao: Annotated[str | None, Field(min_length=1)] = None,
        convenio_sus: bool | None = None,
        order_by: SearchOrder = "leitos_existentes",
    ) -> UFSearchOutput:
        """Busca estabelecimentos por UF e faixa opcional de leitos."""

        _require_data(runtime_repository)
        result = _execute_public(
            search_uf.execute,
            uf,
            limit,
            min_leitos,
            max_leitos,
            municipality=municipio,
            establishment_type=tipo_estabelecimento,
            legal_nature=natureza_juridica,
            management=gestao,
            sus_agreement=convenio_sus,
            order_by=order_by,
        )
        return UFSearchOutput(
            uf=uf.upper(),
            total_encontrados=result.total_available,
            total_retornados=len(result.items),
            filtros_leitos=BedFiltersOutput(minimo=min_leitos, maximo=max_leitos),
            estabelecimentos=[HospitalOutput.from_domain(item) for item in result.items],
        )

    @server.tool(name="cnes_statistics", structured_output=True)
    @_guard_tool
    def cnes_statistics() -> StatisticsOutput:
        """Retorna estatísticas dos dados atualmente carregados."""

        _require_data(runtime_repository)
        values = dict(_execute_public(get_statistics.execute))
        values["arquivo_fonte"] = _public_source_reference(values.get("arquivo_fonte"))
        return StatisticsOutput.model_validate(values)

    @server.tool(name="cnes_download_instructions", structured_output=True)
    @_guard_tool
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

    @server.tool(name="cnes_list_sources", structured_output=True)
    @_guard_tool
    def cnes_list_sources() -> SourceListOutput:
        """Lista as fontes oficiais e sua cobertura canônica observada."""

        checked_at = datetime.now(UTC).isoformat()
        outputs: list[SourceOutput] = []
        for source_name, source in runtime_remote_sources.items():
            try:
                resources = _execute_public(ListRemoteResources(source).execute)
            except CollectorError as exc:
                outputs.append(
                    SourceOutput(
                        nome=source_name,
                        status="indisponivel",
                        campos_cobertos=[],
                        campos_derivados=[],
                        ultima_verificacao=checked_at,
                        observacoes=[exc.code, _public_remote_cause(exc)],
                    )
                )
                continue
            years = sorted({str(item.year) for item in resources})
            is_full = source_name == "datasus_base_completa"
            outputs.append(
                SourceOutput(
                    nome=source_name,
                    status="disponivel",
                    campos_cobertos=(
                        [
                            "CONTRATO_V1",
                            "RAZAO_SOCIAL",
                            "CNPJ",
                            "CNPJ_MANTENEDORA",
                            "TIPO_PESSOA",
                            "NIVEL_DEPENDENCIA",
                            "ENDERECO",
                            "LATITUDE",
                            "LONGITUDE",
                            "TELEFONE",
                            "EMAIL",
                            "LEITOS_POR_TIPO",
                        ]
                        if is_full
                        else [
                            "COMPETENCIA",
                            "UF",
                            "MUNICIPIO",
                            "CNES",
                            "NOME_FANTASIA",
                            "TIPO_ESTABELECIMENTO",
                            "NATUREZA_JURIDICA",
                            "GESTAO",
                            "LEITOS_EXISTENTES",
                            "LEITOS_SUS",
                        ]
                    ),
                    campos_derivados=(
                        ["CONVENIO_SUS", "GEO_CONFIAVEL", "LEITOS_POR_GRUPO"]
                        if is_full
                        else ["CONVENIO_SUS"]
                    ),
                    ultima_verificacao=checked_at,
                    observacoes=(
                        [
                            f"ZIPs mensais disponíveis: {', '.join(years)}",
                            "Somente pessoa jurídica entra no schema padrão; nenhum CPF é projetado.",
                        ]
                        if is_full
                        else [
                            f"Arquivos anuais disponíveis: {', '.join(years)}",
                            "Filtros são aplicados localmente após o download.",
                        ]
                    ),
                )
            )
        return SourceListOutput(fontes=outputs)

    @server.tool(name="cnes_list_competencias", structured_output=True)
    @_guard_tool
    def cnes_list_competencias(
        fonte: Annotated[
            str | None,
            Field(description="Fonte remota; omita para usar a fonte padrão"),
        ] = None,
        ano: Annotated[
            int | None,
            Field(ge=1, description="Ano publicado a consultar; omita para o mais recente"),
        ] = None,
    ) -> CompetenceListOutput:
        """Lista competências mensais de um único arquivo anual do CNES."""

        source = select_remote_source(fonte)
        try:
            result = _execute_public(ListRemoteCompetences(source).execute, ano)
        except CollectorError as exc:
            _raise_remote_error(exc)
        return CompetenceListOutput(
            fonte=source.name,
            ano_consultado=result.year,
            competencias_disponiveis=list(result.competences),
            mais_recente=result.competences[-1] if result.competences else None,
            granularidade="mensal YYYYMM",
        )

    @server.tool(name="cnes_fetch", structured_output=True)
    @_guard_tool
    def cnes_fetch(
        competencia: Annotated[
            str,
            Field(pattern=r"^\d{6}$", description="Competência mensal no formato YYYYMM"),
        ],
        uf: Annotated[
            str | None,
            Field(pattern=r"^[A-Za-z]{2}$", description="Sigla opcional da UF"),
        ] = None,
        municipio: Annotated[
            str | None,
            Field(min_length=1, description="Município parcial ou completo"),
        ] = None,
        tipo_estabelecimento: Annotated[
            str | None,
            Field(min_length=1, description="Descrição parcial do tipo"),
        ] = None,
        natureza_juridica: Annotated[
            str | None,
            Field(min_length=1, description="Descrição parcial da natureza jurídica"),
        ] = None,
        gestao: Annotated[
            str | None,
            Field(min_length=1, description="Gestão do estabelecimento"),
        ] = None,
        convenio_sus: bool | None = None,
        min_leitos: BedMinimum = None,
        max_leitos: BedMaximum = None,
        fonte: str | None = None,
        auto_load: bool = True,
        dest_dir: str | None = None,
    ) -> RemoteFetchOutput:
        """Baixa, filtra e normaliza uma competência oficial sem passo manual."""

        source = select_remote_source(fonte)
        fetch_remote_data = FetchRemoteData(
            source,
            loader=LoadData(runtime_repository, CsvCNESImporter()),
            repository=catalog_repository,
        )
        try:
            result = _execute_public(
                fetch_remote_data.execute,
                competence=competencia,
                uf=uf,
                municipality=municipio,
                establishment_type=tipo_estabelecimento,
                legal_nature=natureza_juridica,
                management=gestao,
                sus_agreement=convenio_sus,
                min_beds=min_leitos,
                max_beds=max_leitos,
                auto_load=auto_load,
                destination=Path(dest_dir) if dest_dir else None,
            )
        except CollectorError as exc:
            _raise_remote_error(exc)
        return RemoteFetchOutput(
            filepath=_public_file_reference(
                result.fetch.filepath, runtime_settings.remote_dir
            ),
            lote_id=result.batch_id,
            registros=result.fetch.records,
            filtros_nativos=list(result.fetch.native_filters),
            filtros_locais=list(result.fetch.local_filters),
            fonte_usada=result.fetch.source,
            campos_nao_preenchidos=list(result.fetch.missing_fields),
            campos_derivados=list(result.fetch.derived_fields),
            cache=result.fetch.from_cache or result.fetch.download_cache_hit,
            etag=result.fetch.etag,
        )

    @server.tool(name="cnes_validate_dataset", structured_output=True)
    @_guard_tool
    def cnes_validate_dataset(
        lote_id: Annotated[
            str | None,
            Field(min_length=1, description="Lote; omita para validar o lote ativo"),
        ] = None,
    ) -> DatasetValidationOutput:
        """Verifica completude, duplicidade, competências e leitos de um lote."""

        return DatasetValidationOutput.model_validate(
            _execute_public(validate_dataset.execute, lote_id)
        )

    @server.tool(name="cnes_list_lotes", structured_output=True)
    @_guard_tool
    def cnes_list_lotes() -> BatchListOutput:
        """Lista lotes retidos e identifica a projeção atualmente ativa."""

        return BatchListOutput(
            lotes=[
                BatchOutput.model_validate(
                    {
                        **item,
                        "arquivo_fonte": _public_source_reference(item["arquivo_fonte"]),
                    }
                )
                for item in _execute_public(list_batches.execute)
            ]
        )

    @server.tool(name="cnes_use_lote", structured_output=True)
    @_guard_tool
    def cnes_use_lote(
        lote_id: Annotated[str, Field(min_length=1, description="Identidade do lote")],
    ) -> ActiveBatchOutput:
        """Ativa atomicamente um lote retido para as seis consultas legadas."""

        _execute_public(use_batch.execute, lote_id)
        return ActiveBatchOutput(lote_id=lote_id, ativo=True)

    @server.tool(name="cnes_purge", structured_output=True)
    @_guard_tool
    def cnes_purge(
        lote_id: Annotated[
            str | None,
            Field(
                min_length=1,
                max_length=128,
                description="Lote a excluir; omita para limpar somente o cache remoto",
            ),
        ] = None,
        confirmacao: Annotated[
            str | None,
            Field(
                min_length=1,
                max_length=160,
                description=(
                    "Use EXCLUIR_LOTE:<lote_id> para apagar um lote ou "
                    "LIMPAR_CACHE para apagar somente caches remotos"
                ),
            ),
        ] = None,
    ) -> PurgeOutput:
        """Remove um lote específico ou, sem lote_id, o cache remoto gerado."""

        if not runtime_settings.allow_purge:
            raise PublicToolError(
                "cnes_purge está desabilitado. O operador deve definir "
                "MCP_CNES_ALLOW_PURGE=true antes de iniciar o servidor."
            )
        expected_confirmation = (
            f"EXCLUIR_LOTE:{lote_id}" if lote_id is not None else "LIMPAR_CACHE"
        )
        if confirmacao != expected_confirmation:
            raise PublicToolError(
                "Confirmação inválida para cnes_purge. "
                f"Informe exatamente {expected_confirmation}."
            )
        if lote_id is not None:
            removed, released = _execute_public(purge_batch.execute, lote_id)
            return PurgeOutput(lote_id=lote_id, itens_removidos=removed, bytes_liberados=released)
        removed = released = 0
        for source in runtime_remote_sources.values():
            purge_cache = getattr(source, "purge_cache", None)
            if callable(purge_cache):
                source_removed, source_released = cast(tuple[int, int], purge_cache())
                removed += source_removed
                released += source_released
        return PurgeOutput(lote_id=None, itens_removidos=removed, bytes_liberados=released)

    @server.tool(name="cnes_aggregate", structured_output=True)
    @_guard_tool
    def cnes_aggregate(
        group_by: Annotated[
            str,
            Field(pattern=r"^(uf|municipio|tipo|natureza|gestao)$"),
        ],
        metrica: Annotated[
            str,
            Field(pattern=(r"^(estabelecimentos|leitos_existentes|leitos_sus|media_leitos)$")),
        ],
        filtros: AdvancedFiltersInput | None = None,
        lote_id: str | None = None,
    ) -> AggregateOutput:
        """Agrupa estabelecimentos e leitos por dimensão canônica."""

        values = filtros.model_dump(exclude_none=True) if filtros else {}
        result = _execute_public(
            aggregate_data.execute, group_by, metrica, values, lote_id
        )
        return AggregateOutput(
            group_by=group_by,
            metrica=metrica,
            lote_id=lote_id,
            resultados=[AggregatePointOutput.model_validate(item) for item in result],
        )

    @server.tool(name="cnes_timeseries", structured_output=True)
    @_guard_tool
    def cnes_timeseries(
        chave: Annotated[str, Field(min_length=1)],
        tipo_chave: Annotated[str, Field(pattern=r"^(cnes|municipio)$")],
        de: Annotated[str, Field(pattern=r"^\d{6}$")],
        ate: Annotated[str, Field(pattern=r"^\d{6}$")],
    ) -> TimeSeriesOutput:
        """Retorna a evolução mensal de leitos nos lotes retidos."""

        result = _execute_public(time_series.execute, chave, tipo_chave, de, ate)
        return TimeSeriesOutput(
            chave=chave,
            tipo_chave=tipo_chave,
            de=de,
            ate=ate,
            serie=[TimeSeriesPointOutput.model_validate(item) for item in result],
            avisos=[
                "A série usa os registros retidos mais recentes por CNES/competência; "
                "lotes coletados com filtros podem representar cobertura parcial."
            ],
        )

    @server.tool(name="cnes_diff", structured_output=True)
    @_guard_tool
    def cnes_diff(
        lote_a: Annotated[str, Field(min_length=1)],
        lote_b: Annotated[str, Field(min_length=1)],
    ) -> DiffOutput:
        """Compara entradas, saídas e mudanças de leitos entre dois lotes."""

        return DiffOutput.model_validate(
            _execute_public(diff_batches.execute, lote_a, lote_b)
        )

    @server.tool(name="cnes_search_advanced", structured_output=True)
    @_guard_tool
    def cnes_search_advanced(
        filtros: AdvancedFiltersInput | None = None,
        order_by: Annotated[
            str,
            Field(pattern=r"^(cnes|municipio|leitos_existentes|leitos_sus)$"),
        ] = "cnes",
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: ResultLimit = 100,
        lote_id: str | None = None,
    ) -> AdvancedSearchOutput:
        """Combina filtros canônicos com ordenação e paginação por offset."""

        values = filtros.model_dump(exclude_none=True) if filtros else {}
        result = _execute_public(
            advanced_search.execute, values, order_by, offset, limit, lote_id
        )
        return AdvancedSearchOutput(
            total_encontrados=result.total_available,
            total_retornados=len(result.items),
            offset=result.offset,
            limit=result.limit,
            estabelecimentos=[HospitalOutput.from_domain(item) for item in result.items],
        )

    @server.tool(name="cnes_search_advanced_v2", structured_output=True)
    @_guard_tool
    def cnes_search_advanced_v2(
        filtros: AdvancedFiltersInput | None = None,
        order_by: Annotated[
            str,
            Field(pattern=r"^(cnes|municipio|leitos_existentes|leitos_sus)$"),
        ] = "cnes",
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: ResultLimit = 100,
        lote_id: str | None = None,
    ) -> AdvancedSearchV2Output:
        """Consulta os campos institucionais e leitos desagregados do contrato v2."""

        values = filtros.model_dump(exclude_none=True) if filtros else {}
        result = _execute_public(
            advanced_search_v2.execute, values, order_by, offset, limit, lote_id
        )
        return AdvancedSearchV2Output(
            total_encontrados=result.total_available,
            total_retornados=len(result.items),
            offset=result.offset,
            limit=result.limit,
            estabelecimentos=[HospitalV2Output.from_domain(item) for item in result.items],
        )

    @server.tool(name="cnes_group_by_mantenedora", structured_output=True)
    @_guard_tool
    def cnes_group_by_mantenedora(
        filtros: AdvancedFiltersInput | None = None,
        limit: ResultLimit = 100,
        lote_id: str | None = None,
    ) -> MaintainerGroupsOutput:
        """Agrupa unidades do lote v2 por CNPJ da mantenedora."""

        values = filtros.model_dump(exclude_none=True) if filtros else {}
        result = _execute_public(group_by_maintainer.execute, values, limit, lote_id)
        missing_maintainers = result["unidades_sem_cnpj_mantenedora"]
        return MaintainerGroupsOutput(
            total_retornado=len(result["redes"]),
            lote_id=result["lote_id"],
            unidades_sem_cnpj_mantenedora=missing_maintainers,
            redes=[MaintainerGroupOutput.model_validate(item) for item in result["redes"]],
            avisos=[
                "A base mensal informa o CNPJ da mantenedora, mas não publica seu nome; "
                "rede permanece nulo."
            ]
            + (
                [f"{missing_maintainers} unidade(s) sem CNPJ da mantenedora foram omitidas."]
                if missing_maintainers
                else []
            ),
        )

    @server.tool(name="cnes_leads_triggers", structured_output=True)
    @_guard_tool
    def cnes_leads_triggers(
        competencia_a: Annotated[str, Field(pattern=r"^\d{6}$")],
        competencia_b: Annotated[str, Field(pattern=r"^\d{6}$")],
        delta_min: Annotated[int, Field(ge=1)],
        tipo_estabelecimento: Annotated[str | None, Field(min_length=1)] = None,
        lote_a: str | None = None,
        lote_b: str | None = None,
    ) -> LeadTriggersOutput:
        """Detecta expansão, retração, entrada e saída entre competências v2."""

        result = _execute_public(
            lead_triggers.execute,
            competencia_a, competencia_b, delta_min, tipo_estabelecimento, lote_a, lote_b
        )
        return LeadTriggersOutput.model_validate(result)

    @server.tool(name="cnes_score_leads", structured_output=True)
    @_guard_tool
    def cnes_score_leads(
        competencia_a: Annotated[str, Field(pattern=r"^\d{6}$")],
        competencia_b: Annotated[str, Field(pattern=r"^\d{6}$")],
        pesos: LeadScoreWeightsInput,
        filtros: AdvancedFiltersInput | None = None,
        limit: ResultLimit = 100,
        lote_a: str | None = None,
        lote_b: str | None = None,
    ) -> LeadScoresOutput:
        """Ordena leads por score decomposto usando somente os pesos informados."""

        filter_values = filtros.model_dump(exclude_none=True) if filtros else {}
        weight_values = pesos.model_dump()
        result = _execute_public(
            score_leads.execute,
            competencia_a,
            competencia_b,
            weight_values,
            filter_values,
            limit,
            lote_a,
            lote_b,
        )
        return LeadScoresOutput(
            competencia_a=competencia_a,
            competencia_b=competencia_b,
            lote_a=result["lote_a"],
            lote_b=result["lote_b"],
            pesos=pesos,
            total_retornado=len(result["leads"]),
            leads=[LeadScoreOutput.model_validate(item) for item in result["leads"]],
            metodologia=[
                "porte: posição acumulada dos leitos existentes dentro do recorte",
                "complexidade_uti: percentil da soma de UTI adulto, pediátrica e neonatal",
                "complexidade_habilitacoes: percentil da quantidade de habilitações ativas",
                "complexidade: média dos dois componentes; usa habilitações quando UTI está ausente",
                "mix_pagador: percentual de leitos não SUS; nulo quando não há leitos",
                "tendencia: posição acumulada do delta de leitos entre as competências",
                "total: média ponderada somente das dimensões disponíveis",
            ],
            campos_ausentes=sorted(
                {
                    field
                    for lead in result["leads"]
                    for field in lead["campos_ausentes"]
                }
            ),
            avisos=result["avisos"],
        )

    @server.tool(name="cnes_normalize", structured_output=True)
    @_guard_tool
    def cnes_normalize(
        filepath: Annotated[str, Field(min_length=1)],
        origem: Annotated[str, Field(pattern=r"^(auto|csv_canonico|portal_sus)$")] = "auto",
        destino: str | None = None,
    ) -> NormalizeOutput:
        """Normaliza um CSV local aprovado para o contrato canônico de 11 campos."""

        try:
            result = normalize_data.execute(
                Path(filepath), origem, Path(destino) if destino else None
            )
        except ImportSecurityError as exc:
            raise PublicToolError(
                f"{exc}. Verifique filepath e a politica configurada."
            ) from None
        except CNESDataLoadError as exc:
            raise PublicToolError(_safe_load_error(exc)) from None
        return NormalizeOutput(
            filepath=_public_file_reference(result.filepath, runtime_settings.output_dir),
            origem=result.origin,
            registros=result.records,
            campos_nao_preenchidos=list(result.missing_fields),
            campos_derivados=list(result.derived_fields),
        )

    @server.tool(name="cnes_export", structured_output=True)
    @_guard_tool
    def cnes_export(
        formato: Annotated[str, Field(pattern=r"^(csv|json|jsonl|xlsx)$")],
        filtros: AdvancedFiltersInput | None = None,
        destino: str | None = None,
        lote_id: str | None = None,
        cnes_list: Annotated[
            list[str] | None,
            Field(
                min_length=1,
                max_length=500,
                description="Conjunto explícito de códigos CNES de sete dígitos",
            ),
        ] = None,
        limit: Annotated[int | None, Field(ge=1, le=500)] = None,
        offset: Annotated[int, Field(ge=0)] = 0,
        order_by: SearchOrder = "cnes",
        perfil_saida: Annotated[
            str | None,
            Field(pattern=r"^crm_generico$"),
        ] = None,
    ) -> ExportOutput:
        """Exporta a seleção para CSV, JSON, JSONL ou XLSX, com perfil CRM opcional."""

        values = filtros.model_dump(exclude_none=True) if filtros else {}
        result = _execute_public(
            export_data.execute,
            formato,
            values,
            Path(destino) if destino else None,
            lote_id,
            cnes_list,
            limit,
            offset,
            order_by,
            perfil_saida,
        )
        return ExportOutput(
            filepath=_public_file_reference(result.filepath, runtime_settings.output_dir),
            formato=formato,
            registros=result.records,
        )

    return server
