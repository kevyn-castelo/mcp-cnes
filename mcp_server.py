"""Interface MCP legada, mantida como camada fina até a migração do SDK no F3."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from mcp_cnes.application import (
    GetStatistics,
    LoadData,
    SearchByCNES,
    SearchByMunicipality,
    SearchByUF,
)
from mcp_cnes.domain import (
    CNESDataLoadError,
    HospitalInfo,
    LoadSummary,
    is_within_bed_range,
    normalize_column_name,
    parse_bool,
    parse_non_negative_int,
    validate_bed_range,
)
from mcp_cnes.infrastructure.config import Settings, load_settings
from mcp_cnes.infrastructure.importers import CsvCNESImporter, SecureCsvImporter
from mcp_cnes.infrastructure.persistence import MemoryCNESRepository

logger = logging.getLogger(__name__)

__all__ = [
    "CNESDataLoadError",
    "CNESDataStore",
    "HospitalInfo",
    "LoadSummary",
    "MCPServer",
    "is_within_bed_range",
    "normalize_column_name",
    "parse_bool",
    "parse_non_negative_int",
    "run_server",
    "validate_bed_range",
]


class CNESDataStore(MemoryCNESRepository):
    """Facade compatível do repositório em memória usado pelo servidor legado."""

    def load_from_csv(self, filepath: Path) -> LoadSummary:
        return LoadData(self, CsvCNESImporter()).execute(filepath)


class MCPServer:
    """Adapta o contrato MCP legado aos casos de uso da aplicação."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self.data_store = CNESDataStore()
        self.downloads_dir = self.settings.data_dir
        self._load_data = LoadData(
            self.data_store,
            SecureCsvImporter(
                CsvCNESImporter(),
                self.settings.data_dir,
                self.settings.max_csv_size_bytes,
                self.settings.allowed_csv_files,
            ),
        )
        self._search_municipality = SearchByMunicipality(self.data_store)
        self._search_cnes = SearchByCNES(self.data_store)
        self._search_uf = SearchByUF(self.data_store)
        self._statistics = GetStatistics(self.data_store)

    def get_tools(self) -> list[dict[str, Any]]:
        """Retorna a lista de ferramentas preservada pelo baseline de contrato."""

        return [
            {
                "name": "cnes_load_data",
                "description": (
                    "Carrega dados do CNES a partir de um arquivo CSV exportado do dashboard "
                    "Kibana."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "filepath": {
                            "type": "string",
                            "description": "Caminho para o arquivo CSV",
                        }
                    },
                    "required": ["filepath"],
                },
            },
            {
                "name": "cnes_search_municipio",
                "description": "Busca estabelecimentos de saúde por nome do município.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "municipio": {
                            "type": "string",
                            "description": "Nome do município (parcial ou completo)",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Número máximo de resultados (padrão: 50)",
                            "default": 50,
                            "minimum": 1,
                        },
                        "min_leitos": {
                            "type": "integer",
                            "description": "Quantidade mínima inclusiva de leitos",
                            "minimum": 0,
                        },
                        "max_leitos": {
                            "type": "integer",
                            "description": "Quantidade máxima inclusiva de leitos",
                            "minimum": 0,
                        },
                    },
                    "required": ["municipio"],
                },
            },
            {
                "name": "cnes_search_cnes",
                "description": "Busca estabelecimento de saúde pelo código CNES.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "cnes": {
                            "type": "string",
                            "description": "Código CNES do estabelecimento (7 dígitos)",
                        }
                    },
                    "required": ["cnes"],
                },
            },
            {
                "name": "cnes_search_uf",
                "description": "Busca estabelecimentos de saúde por UF (estado).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "uf": {
                            "type": "string",
                            "description": "Sigla do estado (ex: SP, RJ, MG)",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Número máximo de resultados (padrão: 100)",
                            "default": 100,
                            "minimum": 1,
                        },
                        "min_leitos": {
                            "type": "integer",
                            "description": "Quantidade mínima inclusiva de leitos",
                            "minimum": 0,
                        },
                        "max_leitos": {
                            "type": "integer",
                            "description": "Quantidade máxima inclusiva de leitos",
                            "minimum": 0,
                        },
                    },
                    "required": ["uf"],
                },
            },
            {
                "name": "cnes_statistics",
                "description": "Retorna estatísticas gerais dos dados carregados.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "cnes_download_instructions",
                "description": "Retorna instruções para download manual de dados do CNES.",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handlers = {
            "cnes_load_data": self._handle_load_data,
            "cnes_search_municipio": self._handle_search_municipio,
            "cnes_search_cnes": self._handle_search_cnes,
            "cnes_search_uf": self._handle_search_uf,
            "cnes_statistics": self._handle_statistics,
            "cnes_download_instructions": self._handle_download_instructions,
        }
        handler = handlers.get(name)
        if handler is None:
            return {"error": f"Ferramenta desconhecida: {name}"}
        try:
            return await handler(arguments)
        except Exception as exc:
            logger.error("Erro ao executar %s: %s", name, exc)
            return {"error": str(exc)}

    async def _handle_load_data(self, args: dict[str, Any]) -> dict[str, Any]:
        filepath = Path(args["filepath"])
        if not filepath.exists():
            return {"success": False, "error": f"Arquivo não encontrado: {filepath}"}
        try:
            summary = self._load_data.execute(filepath)
        except CNESDataLoadError as exc:
            return {"success": False, "error": str(exc)}
        return {
            "success": True,
            "registros_carregados": summary.records_loaded,
            "linhas_lidas": summary.rows_read,
            "linhas_rejeitadas": summary.rows_rejected,
            "linhas_ignoradas": summary.rows_ignored,
            "mensagem": f"Carregados {summary.records_loaded} estabelecimentos de saúde",
        }

    @staticmethod
    def _parse_search_options(
        args: dict[str, Any], default_limit: int
    ) -> tuple[int, int | None, int | None]:
        limit = args.get("limit", default_limit)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit deve ser um inteiro maior que zero")
        min_beds = args.get("min_leitos")
        max_beds = args.get("max_leitos")
        validate_bed_range(min_beds, max_beds)
        return limit, min_beds, max_beds

    def _require_data(self) -> dict[str, str] | None:
        if not self.data_store.has_data():
            return {"error": "Dados não carregados. Use cnes_load_data primeiro."}
        return None

    async def _handle_search_municipio(self, args: dict[str, Any]) -> dict[str, Any]:
        if error := self._require_data():
            return error
        municipality = args["municipio"]
        limit, min_beds, max_beds = self._parse_search_options(args, 50)
        result = self._search_municipality.execute(
            municipality, limit, min_beds, max_beds
        )
        return {
            "municipio": municipality,
            "total_encontrados": result.total_available,
            "total_retornados": len(result.items),
            "filtros_leitos": {"minimo": result.min_beds, "maximo": result.max_beds},
            "estabelecimentos": [hospital.to_dict() for hospital in result.items],
        }

    async def _handle_search_cnes(self, args: dict[str, Any]) -> dict[str, Any]:
        if error := self._require_data():
            return error
        cnes = args["cnes"]
        result = self._search_cnes.execute(cnes)
        if result is not None:
            return {"encontrado": True, "estabelecimento": result.to_dict()}
        return {"encontrado": False, "mensagem": f"CNES {cnes} não encontrado"}

    async def _handle_search_uf(self, args: dict[str, Any]) -> dict[str, Any]:
        if error := self._require_data():
            return error
        uf = args["uf"]
        limit, min_beds, max_beds = self._parse_search_options(args, 100)
        result = self._search_uf.execute(uf, limit, min_beds, max_beds)
        return {
            "uf": uf.upper(),
            "total_encontrados": result.total_available,
            "total_retornados": len(result.items),
            "filtros_leitos": {"minimo": result.min_beds, "maximo": result.max_beds},
            "estabelecimentos": [hospital.to_dict() for hospital in result.items],
        }

    async def _handle_statistics(self, args: dict[str, Any]) -> dict[str, Any]:
        return self._statistics.execute()

    async def _handle_download_instructions(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "titulo": "Instruções para Download de Dados CNES",
            "url": self.settings.dashboard_url,
            "passos": [
                "1. Acesse a URL do dashboard de leitos",
                "2. Aguarde o carregamento completo do dashboard",
                "3. Role até encontrar a tabela 'EXTRATO DOS LEITOS'",
                "4. Clique no ícone '⋮' (três pontos) no canto superior direito da tabela",
                "5. Selecione 'Download CSV'",
                "6. Aguarde o download (limite: 400.000 registros)",
            ],
            "colunas_disponiveis": [
                "COMPETÊNCIA",
                "UF",
                "CÓDIGO_MUNICÍPIO",
                "MUNICÍPIO",
                "CNES",
                "NOME_FANTASIA",
                "TIPO_ESTABELECIMENTO",
                "NATUREZA_JURÍDICA",
                "GESTÃO",
                "CONVÊNIO_SUS",
                "TIPO_LEITO",
                "CÓDIGO_LEITO",
                "LEITO",
                "LEITOS_EXISTENTES",
                "LEITOS_SUS",
            ],
            "apos_download": "Use a ferramenta cnes_load_data para carregar o CSV baixado",
        }


async def run_server() -> None:
    """Executa o transporte manual existente; será removido no F3."""

    server = MCPServer()
    print(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "cnes-mcp-server", "version": "1.0.0"},
                },
            }
        )
    )
    while True:
        try:
            line = input()
            if not line:
                continue
            request = json.loads(line)
            method = request.get("method", "")
            if method == "tools/list":
                response = {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "result": {"tools": server.get_tools()},
                }
            elif method == "tools/call":
                params = request.get("params", {})
                result = await server.call_tool(
                    params.get("name", ""), params.get("arguments", {})
                )
                response = {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "result": {
                        "content": [
                            {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
                        ]
                    },
                }
            else:
                response = {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "result": {},
                }
            print(json.dumps(response, ensure_ascii=False))
        except EOFError:
            break
        except Exception as exc:
            logger.error("Erro no servidor: %s", exc)


if __name__ == "__main__":
    asyncio.run(run_server())
