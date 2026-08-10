"""Schemas públicos das ferramentas MCP."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mcp_cnes.domain.models import HospitalInfo, HospitalInfoV2


class ContractModel(BaseModel):
    """Base estrita para evitar campos silenciosamente ignorados."""

    model_config = ConfigDict(extra="forbid")


class HospitalOutput(ContractModel):
    cnes: str
    nome_fantasia: str
    municipio: str
    uf: str
    tipo_estabelecimento: str = ""
    natureza_juridica: str = ""
    gestao: str = ""
    convenio_sus: bool = True
    leitos_existentes: int = Field(ge=0)
    leitos_sus: int = Field(ge=0)
    competencia: str = ""

    @classmethod
    def from_domain(cls, hospital: HospitalInfo) -> HospitalOutput:
        return cls.model_validate(hospital.to_dict())


class HospitalV2Output(HospitalOutput):
    razao_social: str | None = None
    cnpj: str | None = None
    cnpj_mantenedora: str | None = None
    tipo_pessoa: str | None = None
    nivel_dependencia: str | None = None
    logradouro: str | None = None
    numero: str | None = None
    complemento: str | None = None
    bairro: str | None = None
    cep: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    geo_confiavel: bool = False
    telefone: str | None = None
    email: str | None = None
    leitos_uti_adulto: int | None = Field(default=None, ge=0)
    leitos_uti_pediatrica: int | None = Field(default=None, ge=0)
    leitos_uti_neonatal: int | None = Field(default=None, ge=0)
    leitos_cirurgicos: int | None = Field(default=None, ge=0)
    leitos_clinicos: int | None = Field(default=None, ge=0)
    leitos_obstetricos: int | None = Field(default=None, ge=0)
    leitos_complementares: int | None = Field(default=None, ge=0)
    habilitacoes: list[str] = Field(default_factory=list)
    total_habilitacoes: int = Field(default=0, ge=0)
    campos_ausentes: list[str] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, hospital: HospitalInfoV2) -> HospitalV2Output:
        return cls.model_validate(hospital.to_dict())


class LoadDataOutput(ContractModel):
    success: bool
    lote_id: str
    registros_carregados: int = Field(ge=0)
    linhas_lidas: int = Field(ge=0)
    linhas_aceitas: int = Field(ge=0)
    linhas_rejeitadas: int = Field(ge=0)
    linhas_ignoradas: int = Field(ge=0)
    motivos_rejeicao: dict[str, int]
    mensagem: str


class BedFiltersOutput(ContractModel):
    minimo: int | None = Field(default=None, ge=0)
    maximo: int | None = Field(default=None, ge=0)


class MunicipalitySearchOutput(ContractModel):
    municipio: str
    total_encontrados: int = Field(ge=0)
    total_retornados: int = Field(ge=0)
    filtros_leitos: BedFiltersOutput
    estabelecimentos: list[HospitalOutput]


class UFSearchOutput(ContractModel):
    uf: str = Field(pattern=r"^[A-Z]{2}$")
    total_encontrados: int = Field(ge=0)
    total_retornados: int = Field(ge=0)
    filtros_leitos: BedFiltersOutput
    estabelecimentos: list[HospitalOutput]


class CNESSearchOutput(ContractModel):
    encontrado: bool
    estabelecimento: HospitalOutput | None = None
    mensagem: str | None = None


class StatisticsOutput(ContractModel):
    total_estabelecimentos: int = Field(ge=0)
    total_leitos_existentes: int = Field(ge=0)
    total_leitos_sus: int = Field(ge=0)
    estabelecimentos_por_uf: dict[str, int]
    ultima_atualizacao: str | None
    arquivo_fonte: str | None


class DownloadInstructionsOutput(ContractModel):
    titulo: str
    url: str
    passos: list[str]
    colunas_disponiveis: list[str]
    apos_download: str


class SourceOutput(ContractModel):
    nome: str
    status: str
    campos_cobertos: list[str]
    campos_derivados: list[str]
    ultima_verificacao: str
    observacoes: list[str]


class SourceListOutput(ContractModel):
    fontes: list[SourceOutput]


class CompetenceListOutput(ContractModel):
    fonte: str
    ano_consultado: int
    competencias_disponiveis: list[str]
    mais_recente: str | None
    granularidade: str


class RemoteFetchOutput(ContractModel):
    filepath: str
    lote_id: str | None
    registros: int = Field(ge=0)
    filtros_nativos: list[str]
    filtros_locais: list[str]
    fonte_usada: str
    campos_nao_preenchidos: list[str]
    campos_derivados: list[str]
    cache: bool
    etag: str | None


class StructuredErrorOutput(ContractModel):
    erro: str
    causa: str
    sugestao: str


class BatchOutput(ContractModel):
    lote_id: str
    arquivo_fonte: str
    fonte: str
    competencia: str | None
    filtros: dict[str, Any]
    registros: int = Field(ge=0)
    importado_em: str
    ativo: bool


class BatchListOutput(ContractModel):
    lotes: list[BatchOutput]


class ActiveBatchOutput(ContractModel):
    lote_id: str
    ativo: bool


class PurgeOutput(ContractModel):
    lote_id: str | None
    itens_removidos: int = Field(ge=0)
    bytes_liberados: int = Field(ge=0)


class DatasetValidationOutput(ContractModel):
    lote_id: str
    total_registros: int = Field(ge=0)
    campos_vazios: dict[str, int]
    cnes_duplicados: int = Field(ge=0)
    competencias: list[str]
    competencias_mistas: bool
    leitos_invalidos: int = Field(ge=0)
    valido: bool


class AggregatePointOutput(ContractModel):
    grupo: str
    valor: int | float


class AggregateOutput(ContractModel):
    group_by: str
    metrica: str
    lote_id: str | None
    resultados: list[AggregatePointOutput]


class TimeSeriesPointOutput(ContractModel):
    competencia: str
    estabelecimentos: int = Field(ge=0)
    leitos_existentes: int = Field(ge=0)
    leitos_sus: int = Field(ge=0)


class TimeSeriesOutput(ContractModel):
    chave: str
    tipo_chave: str
    de: str
    ate: str
    serie: list[TimeSeriesPointOutput]
    avisos: list[str]


class BedChangeOutput(ContractModel):
    cnes: str
    competencia_a: str | None = None
    competencia_b: str | None = None
    leitos_existentes_a: int = Field(ge=0)
    leitos_existentes_b: int = Field(ge=0)
    leitos_sus_a: int = Field(ge=0)
    leitos_sus_b: int = Field(ge=0)


class DiffOutput(ContractModel):
    lote_a: str
    lote_b: str
    entraram: list[str]
    sairam: list[str]
    mudaram_leitos: list[BedChangeOutput]
    avisos: list[str]


class AdvancedFiltersInput(ContractModel):
    uf: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    municipio: str | None = Field(default=None, min_length=1)
    tipo_estabelecimento: str | None = Field(default=None, min_length=1)
    natureza_juridica: str | None = Field(default=None, min_length=1)
    gestao: str | None = Field(default=None, min_length=1)
    convenio_sus: bool | None = None
    min_leitos: int | None = Field(default=None, ge=0)
    max_leitos: int | None = Field(default=None, ge=0)


class AdvancedSearchOutput(ContractModel):
    total_encontrados: int = Field(ge=0)
    total_retornados: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=500)
    estabelecimentos: list[HospitalOutput]


class AdvancedSearchV2Output(ContractModel):
    total_encontrados: int = Field(ge=0)
    total_retornados: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=500)
    contrato: str = "v2"
    estabelecimentos: list[HospitalV2Output]


class MaintainerGroupOutput(ContractModel):
    cnpj_mantenedora: str
    rede: str | None = None
    unidades: int = Field(ge=1)
    leitos_existentes: int = Field(ge=0)
    leitos_sus: int = Field(ge=0)
    mix_sus: float | None = Field(default=None, ge=0, le=1)
    mix_nao_sus: float | None = Field(default=None, ge=0, le=1)
    distribuicao_uf: dict[str, int]
    campos_ausentes: list[str]
    alertas: list[str] = Field(default_factory=list)


class MaintainerGroupsOutput(ContractModel):
    total_retornado: int = Field(ge=0)
    lote_id: str | None = None
    redes: list[MaintainerGroupOutput]
    avisos: list[str]


class LeadTriggerOutput(ContractModel):
    cnes: str
    nome_fantasia: str
    tipo_estabelecimento: str
    leitos_existentes_a: int | None = Field(default=None, ge=0)
    leitos_existentes_b: int | None = Field(default=None, ge=0)
    leitos_sus_a: int | None = Field(default=None, ge=0)
    leitos_sus_b: int | None = Field(default=None, ge=0)
    delta_leitos: int
    motivo: str = Field(pattern=r"^(expansao|retracao|entrada|saida)$")


class LeadTriggersOutput(ContractModel):
    competencia_a: str
    competencia_b: str
    lote_a: str
    lote_b: str
    gatilhos: list[LeadTriggerOutput]
    avisos: list[str]


class LeadScoreWeightsInput(ContractModel):
    porte: float = Field(ge=0)
    complexidade: float = Field(ge=0)
    mix_pagador: float = Field(ge=0)
    tendencia: float = Field(ge=0)


class LeadScoreOutput(ContractModel):
    cnes: str
    nome_fantasia: str
    razao_social: str | None = None
    cnpj: str | None = None
    cnpj_mantenedora: str | None = None
    municipio: str
    uf: str
    tipo_estabelecimento: str
    leitos_existentes: int = Field(ge=0)
    leitos_sus: int = Field(ge=0)
    leitos_uti: int | None = Field(default=None, ge=0)
    total_habilitacoes: int = Field(ge=0)
    delta_leitos: int | None = None
    score_porte: float | None = Field(default=None, ge=0, le=100)
    score_complexidade_uti: float | None = Field(default=None, ge=0, le=100)
    score_complexidade_habilitacoes: float = Field(ge=0, le=100)
    score_complexidade: float = Field(ge=0, le=100)
    score_mix_pagador: float | None = Field(default=None, ge=0, le=100)
    score_tendencia: float | None = Field(default=None, ge=0, le=100)
    score_total: float | None = Field(default=None, ge=0, le=100)
    campos_ausentes: list[str] = Field(default_factory=list)


class LeadScoresOutput(ContractModel):
    competencia_a: str
    competencia_b: str
    lote_a: str
    lote_b: str
    pesos: LeadScoreWeightsInput
    total_retornado: int = Field(ge=0)
    leads: list[LeadScoreOutput]
    metodologia: list[str]
    campos_ausentes: list[str]
    avisos: list[str]


class NormalizeOutput(ContractModel):
    filepath: str
    origem: str
    registros: int = Field(ge=0)
    campos_nao_preenchidos: list[str]
    campos_derivados: list[str]


class ExportOutput(ContractModel):
    filepath: str
    formato: str
    registros: int = Field(ge=0)


def dump_contract(model: ContractModel) -> dict[str, Any]:
    """Serializa sem aliases implícitos ou valores não JSON."""

    return model.model_dump(mode="json")
