"""Servidor oficial MCP v2 sobre os casos de uso da aplicação."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, cast

from mcp.server import MCPServer
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.types import CallToolResult, TextContent
from pydantic import Field

from mcp_cnes import __version__
from mcp_cnes.application import (
    AdvancedSearch,
    AggregateData,
    DiffBatches,
    ExportData,
    FetchRemoteData,
    GetStatistics,
    ListBatches,
    ListRemoteCompetences,
    ListRemoteResources,
    LoadData,
    NormalizeData,
    PurgeBatch,
    SearchByCNES,
    SearchByMunicipality,
    SearchByUF,
    TimeSeries,
    UseBatch,
    ValidateDataset,
)
from mcp_cnes.application.ports import (
    CNESCatalogRepository,
    CNESImporter,
    CNESRemoteSource,
    CNESRepository,
)
from mcp_cnes.domain.errors import CNESDataLoadError, CollectorError, ImportSecurityError
from mcp_cnes.infrastructure.config import Settings, load_settings
from mcp_cnes.infrastructure.exports import LocalDatasetExporter
from mcp_cnes.infrastructure.importers import CsvCNESImporter, SecureCsvImporter
from mcp_cnes.infrastructure.persistence import SQLiteCNESRepository
from mcp_cnes.infrastructure.sources import PortalSUSRemoteSource

from .schemas import (
    ActiveBatchOutput,
    AdvancedFiltersInput,
    AdvancedSearchOutput,
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
    LoadDataOutput,
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
TOOL_ARGUMENTS = {
    "cnes_load_data": frozenset({"filepath"}),
    "cnes_search_municipio": frozenset(
        {"municipio", "limit", "min_leitos", "max_leitos"}
    ),
    "cnes_search_cnes": frozenset({"cnes"}),
    "cnes_search_uf": frozenset({"uf", "limit", "min_leitos", "max_leitos"}),
    "cnes_statistics": frozenset(),
    "cnes_download_instructions": frozenset(),
    "cnes_list_sources": frozenset(),
    "cnes_list_competencias": frozenset({"fonte"}),
    "cnes_fetch": frozenset(
        {
            "competencia",
            "uf",
            "municipio",
            "tipo_estabelecimento",
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
    "cnes_purge": frozenset({"lote_id"}),
    "cnes_aggregate": frozenset({"group_by", "metrica", "filtros", "lote_id"}),
    "cnes_timeseries": frozenset({"chave", "tipo_chave", "de", "ate"}),
    "cnes_diff": frozenset({"lote_a", "lote_b"}),
    "cnes_search_advanced": frozenset(
        {"filtros", "order_by", "offset", "limit", "lote_id"}
    ),
    "cnes_normalize": frozenset({"filepath", "origem", "destino"}),
    "cnes_export": frozenset({"formato", "filtros", "destino", "lote_id"}),
}
BedMinimum = Annotated[int | None, Field(ge=0, description="Mínimo inclusivo de leitos")]
BedMaximum = Annotated[int | None, Field(ge=0, description="Máximo inclusivo de leitos")]
ResultLimit = Annotated[
    int,
    Field(ge=1, le=MAX_RESULTS_PER_CALL, description="Quantidade máxima de resultados"),
]


def _structured_error_text(message: str, *, code: str = "invalid_request") -> str:
    start = message.find("{")
    if start >= 0:
        try:
            existing = json.JSONDecoder().decode(message[start:])
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(existing, dict) and {
                "erro",
                "causa",
                "sugestao",
            }.issubset(existing):
                return json.dumps(existing, ensure_ascii=False, sort_keys=True)
    prefix = "Error executing tool "
    cause = message
    if message.startswith(prefix) and ": " in message:
        cause = message.split(": ", 1)[1]
    return json.dumps(
        {
            "erro": code,
            "causa": cause,
            "sugestao": "Revise os parâmetros informados e tente novamente.",
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _tool_error(message: str, *, code: str = "invalid_request") -> CallToolResult:
    return CallToolResult(
        is_error=True,
        content=[
            TextContent(type="text", text=_structured_error_text(message, code=code))
        ],
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

        try:
            result = await call_next(ctx)
        except Exception as exc:
            return _tool_error(
                str(exc),
                code=("invalid_request" if isinstance(exc, ValueError) else "internal_error"),
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


def _raise_remote_error(error: CollectorError) -> None:
    suggestions = {
        "remote_competence_not_found": "Revise a competência e remova filtros muito restritivos.",
        "remote_competence_unavailable": "Use cnes_list_competencias antes de solicitar a carga.",
        "remote_server_error": "Tente novamente mais tarde; o retry automático já foi esgotado.",
        "remote_rate_limited": "Aguarde antes de repetir a coleta.",
    }
    payload = {
        "erro": error.code,
        "causa": str(error),
        "sugestao": suggestions.get(
            error.code,
            "Verifique a disponibilidade da fonte e os parâmetros informados.",
        ),
    }
    raise ValueError(json.dumps(payload, ensure_ascii=False, sort_keys=True)) from None


def create_mcp_server(
    *,
    settings: Settings | None = None,
    repository: CNESCatalogRepository | None = None,
    importer: CNESImporter | None = None,
    remote_source: CNESRemoteSource | None = None,
) -> MCPServer:
    """Compõe o servidor sem iniciar transporte, rede ou leitura de arquivos."""

    runtime_settings = settings or load_settings()
    runtime_repository = repository or SQLiteCNESRepository(
        runtime_settings.database_path,
        batch_retention_count=runtime_settings.batch_retention_count,
    )
    runtime_importer = importer or SecureCsvImporter(
        CsvCNESImporter(),
        runtime_settings.data_dir,
        runtime_settings.max_csv_size_bytes,
        runtime_settings.allowed_csv_files,
    )
    runtime_remote_source = remote_source or PortalSUSRemoteSource(runtime_settings)

    load_data = LoadData(runtime_repository, runtime_importer)
    search_municipality = SearchByMunicipality(runtime_repository)
    search_cnes = SearchByCNES(runtime_repository)
    search_uf = SearchByUF(runtime_repository)
    get_statistics = GetStatistics(runtime_repository)
    list_remote_competences = ListRemoteCompetences(runtime_remote_source)
    list_remote_resources = ListRemoteResources(runtime_remote_source)
    fetch_remote_data = FetchRemoteData(
        runtime_remote_source,
        loader=LoadData(runtime_repository, CsvCNESImporter()),
    )
    catalog_repository = runtime_repository
    validate_dataset = ValidateDataset(catalog_repository)
    list_batches = ListBatches(catalog_repository)
    use_batch = UseBatch(catalog_repository)
    purge_batch = PurgeBatch(catalog_repository)
    aggregate_data = AggregateData(catalog_repository)
    time_series = TimeSeries(catalog_repository)
    diff_batches = DiffBatches(catalog_repository)
    advanced_search = AdvancedSearch(catalog_repository)
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

    @server.tool(name="cnes_list_sources", structured_output=True)
    def cnes_list_sources() -> SourceListOutput:
        """Lista a fonte remota oficial e sua cobertura canônica observada."""

        checked_at = datetime.now(UTC).isoformat()
        try:
            resources = list_remote_resources.execute()
        except CollectorError as exc:
            return SourceListOutput(
                fontes=[
                    SourceOutput(
                        nome=runtime_remote_source.name,
                        status="indisponivel",
                        campos_cobertos=[],
                        campos_derivados=[],
                        ultima_verificacao=checked_at,
                        observacoes=[exc.code, str(exc)],
                    )
                ]
            )
        years = sorted({str(item.year) for item in resources})
        return SourceListOutput(
            fontes=[
                SourceOutput(
                    nome=runtime_remote_source.name,
                    status="disponivel",
                    campos_cobertos=[
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
                    ],
                    campos_derivados=["CONVENIO_SUS"],
                    ultima_verificacao=checked_at,
                    observacoes=[
                        f"Arquivos anuais disponíveis: {', '.join(years)}",
                        "Filtros são aplicados localmente após o download.",
                    ],
                )
            ]
        )

    @server.tool(name="cnes_list_competencias", structured_output=True)
    def cnes_list_competencias(
        fonte: Annotated[
            str | None,
            Field(description="Fonte remota; omita para usar a fonte padrão"),
        ] = None,
    ) -> CompetenceListOutput:
        """Lista arquivos anuais que contêm competências mensais do CNES."""

        if fonte is not None and fonte != runtime_remote_source.name:
            raise ValueError(
                f"fonte desconhecida: {fonte}. Use cnes_list_sources para descobrir fontes."
            )
        try:
            competences = list_remote_competences.execute()
        except CollectorError as exc:
            _raise_remote_error(exc)
        return CompetenceListOutput(
            fonte=runtime_remote_source.name,
            competencias_disponiveis=list(competences),
            mais_recente=competences[-1] if competences else None,
            granularidade="mensal YYYYMM",
        )

    @server.tool(name="cnes_fetch", structured_output=True)
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
        min_leitos: BedMinimum = None,
        max_leitos: BedMaximum = None,
        fonte: str | None = None,
        auto_load: bool = True,
        dest_dir: str | None = None,
    ) -> RemoteFetchOutput:
        """Baixa, filtra e normaliza uma competência oficial sem passo manual."""

        if fonte is not None and fonte != runtime_remote_source.name:
            raise ValueError(
                f"fonte desconhecida: {fonte}. Use cnes_list_sources para descobrir fontes."
            )
        try:
            result = fetch_remote_data.execute(
                competence=competencia,
                uf=uf,
                municipality=municipio,
                establishment_type=tipo_estabelecimento,
                min_beds=min_leitos,
                max_beds=max_leitos,
                auto_load=auto_load,
                destination=Path(dest_dir) if dest_dir else None,
            )
        except CollectorError as exc:
            _raise_remote_error(exc)
        return RemoteFetchOutput(
            filepath=str(result.fetch.filepath),
            lote_id=result.batch_id,
            registros=result.fetch.records,
            filtros_nativos=list(result.fetch.native_filters),
            filtros_locais=list(result.fetch.local_filters),
            fonte_usada=result.fetch.source,
            campos_nao_preenchidos=list(result.fetch.missing_fields),
            campos_derivados=list(result.fetch.derived_fields),
            cache=result.fetch.from_cache,
            etag=result.fetch.etag,
        )

    @server.tool(name="cnes_validate_dataset", structured_output=True)
    def cnes_validate_dataset(
        lote_id: Annotated[
            str | None,
            Field(min_length=1, description="Lote; omita para validar o lote ativo"),
        ] = None,
    ) -> DatasetValidationOutput:
        """Verifica completude, duplicidade, competências e leitos de um lote."""

        return DatasetValidationOutput.model_validate(validate_dataset.execute(lote_id))

    @server.tool(name="cnes_list_lotes", structured_output=True)
    def cnes_list_lotes() -> BatchListOutput:
        """Lista lotes retidos e identifica a projeção atualmente ativa."""

        return BatchListOutput(
            lotes=[BatchOutput.model_validate(item) for item in list_batches.execute()]
        )

    @server.tool(name="cnes_use_lote", structured_output=True)
    def cnes_use_lote(
        lote_id: Annotated[str, Field(min_length=1, description="Identidade do lote")],
    ) -> ActiveBatchOutput:
        """Ativa atomicamente um lote retido para as seis consultas legadas."""

        use_batch.execute(lote_id)
        return ActiveBatchOutput(lote_id=lote_id, ativo=True)

    @server.tool(name="cnes_purge", structured_output=True)
    def cnes_purge(
        lote_id: Annotated[
            str | None,
            Field(
                min_length=1,
                description="Lote a excluir; omita para limpar somente o cache remoto",
            ),
        ] = None,
    ) -> PurgeOutput:
        """Remove um lote específico ou, sem lote_id, o cache remoto gerado."""

        if lote_id is not None:
            removed, released = purge_batch.execute(lote_id)
            return PurgeOutput(
                lote_id=lote_id, itens_removidos=removed, bytes_liberados=released
            )
        purge_cache = getattr(runtime_remote_source, "purge_cache", None)
        if not callable(purge_cache):
            return PurgeOutput(lote_id=None, itens_removidos=0, bytes_liberados=0)
        removed, released = cast(tuple[int, int], purge_cache())
        return PurgeOutput(
            lote_id=None, itens_removidos=removed, bytes_liberados=released
        )

    @server.tool(name="cnes_aggregate", structured_output=True)
    def cnes_aggregate(
        group_by: Annotated[
            str,
            Field(pattern=r"^(uf|municipio|tipo|natureza|gestao)$"),
        ],
        metrica: Annotated[
            str,
            Field(
                pattern=(
                    r"^(estabelecimentos|leitos_existentes|leitos_sus|media_leitos)$"
                )
            ),
        ],
        filtros: AdvancedFiltersInput | None = None,
        lote_id: str | None = None,
    ) -> AggregateOutput:
        """Agrupa estabelecimentos e leitos por dimensão canônica."""

        values = filtros.model_dump(exclude_none=True) if filtros else {}
        result = aggregate_data.execute(group_by, metrica, values, lote_id)
        return AggregateOutput(
            group_by=group_by,
            metrica=metrica,
            lote_id=lote_id,
            resultados=[AggregatePointOutput.model_validate(item) for item in result],
        )

    @server.tool(name="cnes_timeseries", structured_output=True)
    def cnes_timeseries(
        chave: Annotated[str, Field(min_length=1)],
        tipo_chave: Annotated[str, Field(pattern=r"^(cnes|municipio)$")],
        de: Annotated[str, Field(pattern=r"^\d{6}$")],
        ate: Annotated[str, Field(pattern=r"^\d{6}$")],
    ) -> TimeSeriesOutput:
        """Retorna a evolução mensal de leitos nos lotes retidos."""

        result = time_series.execute(chave, tipo_chave, de, ate)
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
    def cnes_diff(
        lote_a: Annotated[str, Field(min_length=1)],
        lote_b: Annotated[str, Field(min_length=1)],
    ) -> DiffOutput:
        """Compara entradas, saídas e mudanças de leitos entre dois lotes."""

        return DiffOutput.model_validate(diff_batches.execute(lote_a, lote_b))

    @server.tool(name="cnes_search_advanced", structured_output=True)
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
        result = advanced_search.execute(values, order_by, offset, limit, lote_id)
        return AdvancedSearchOutput(
            total_encontrados=result.total_available,
            total_retornados=len(result.items),
            offset=result.offset,
            limit=result.limit,
            estabelecimentos=[HospitalOutput.from_domain(item) for item in result.items],
        )

    @server.tool(name="cnes_normalize", structured_output=True)
    def cnes_normalize(
        filepath: Annotated[str, Field(min_length=1)],
        origem: Annotated[
            str, Field(pattern=r"^(auto|csv_canonico|portal_sus)$")
        ] = "auto",
        destino: str | None = None,
    ) -> NormalizeOutput:
        """Normaliza um CSV local aprovado para o contrato canônico de 11 campos."""

        try:
            result = normalize_data.execute(
                Path(filepath), origem, Path(destino) if destino else None
            )
        except ImportSecurityError as exc:
            raise ValueError(
                f"{exc}. Verifique filepath e a politica configurada."
            ) from None
        except CNESDataLoadError as exc:
            raise ValueError(_safe_load_error(exc)) from None
        return NormalizeOutput(
            filepath=str(result.filepath),
            origem=result.origin,
            registros=result.records,
            campos_nao_preenchidos=list(result.missing_fields),
            campos_derivados=list(result.derived_fields),
        )

    @server.tool(name="cnes_export", structured_output=True)
    def cnes_export(
        formato: Annotated[str, Field(pattern=r"^(csv|json|xlsx)$")],
        filtros: AdvancedFiltersInput | None = None,
        destino: str | None = None,
        lote_id: str | None = None,
    ) -> ExportOutput:
        """Exporta a seleção completa para CSV, JSON ou XLSX local."""

        values = filtros.model_dump(exclude_none=True) if filtros else {}
        result = export_data.execute(
            formato,
            values,
            Path(destino) if destino else None,
            lote_id,
        )
        return ExportOutput(
            filepath=str(result.filepath), formato=formato, registros=result.records
        )

    return server
