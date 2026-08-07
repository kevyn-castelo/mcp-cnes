"""Schemas públicos das ferramentas MCP."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mcp_cnes.domain.models import HospitalInfo


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


class LoadDataOutput(ContractModel):
    success: bool
    registros_carregados: int = Field(ge=0)
    linhas_lidas: int = Field(ge=0)
    linhas_rejeitadas: int = Field(ge=0)
    linhas_ignoradas: int = Field(ge=0)
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


def dump_contract(model: ContractModel) -> dict[str, Any]:
    """Serializa sem aliases implícitos ou valores não JSON."""

    return model.model_dump(mode="json")
