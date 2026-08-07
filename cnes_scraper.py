"""
CNES Hospital Scraper - Extração de Hospitais Privados de Médio Porte
======================================================================

Script para extrair dados de hospitais privados de médio porte (50-150 leitos)
do portal ElastiCNES (elasticnes.saude.gov.br).

Autor: Gerado por Antigravity AI
Data: 2026-01-21

Estratégia:
- Utiliza API Elasticsearch diretamente (mais estável que browser automation)
- Combina dados de 3 índices: leitos, geral e profissionais
- Implementa rate limiting e retry para evitar bloqueios
"""

import argparse
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
from tqdm import tqdm

from mcp_cnes.domain.rules import validate_bed_range
from mcp_cnes.infrastructure.config import Settings

# Configuração de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


_DEFAULT_SETTINGS = Settings()


# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

@dataclass
class CNESConfig:
    """Configuração do scraper CNES."""

    # Limites de leitos para hospitais de médio porte (configurável)
    MIN_BEDS: int = _DEFAULT_SETTINGS.min_beds
    MAX_BEDS: int = _DEFAULT_SETTINGS.max_beds

    # Códigos de Natureza Jurídica (Hospitais Privados)
    PRIVATE_NATURE_CODES: List[str] = field(
        default_factory=lambda: list(_DEFAULT_SETTINGS.private_nature_codes)
    )

    # Códigos CBO para diretores
    DIRECTOR_CBO_CODES: List[str] = field(
        default_factory=lambda: list(_DEFAULT_SETTINGS.director_cbo_codes)
    )

    # Cidades alvo organizadas por região
    TARGET_CITIES: Dict[str, List[str]] = field(
        default_factory=lambda: {
            region: list(cities) for region, cities in _DEFAULT_SETTINGS.target_cities.items()
        }
    )

    # Rate limiting
    MIN_DELAY: float = _DEFAULT_SETTINGS.min_delay
    MAX_DELAY: float = _DEFAULT_SETTINGS.max_delay

    # Timeout para requisições
    REQUEST_TIMEOUT: int = _DEFAULT_SETTINGS.request_timeout

    # Retries
    MAX_RETRIES: int = _DEFAULT_SETTINGS.max_retries
    RETRY_DELAY: float = _DEFAULT_SETTINGS.retry_delay

    # Competência (mês de referência)
    COMPETENCIA: str = _DEFAULT_SETTINGS.competence

    # URLs base
    BASE_URL: str = _DEFAULT_SETTINGS.base_url
    KIBANA_API: str = _DEFAULT_SETTINGS.kibana_api

    def __post_init__(self) -> None:
        validate_bed_range(self.MIN_BEDS, self.MAX_BEDS)

    @classmethod
    def from_settings(cls, settings: Settings) -> "CNESConfig":
        """Adapta os settings centrais ao coletor legado."""

        return cls(
            MIN_BEDS=settings.min_beds,
            MAX_BEDS=settings.max_beds,
            PRIVATE_NATURE_CODES=list(settings.private_nature_codes),
            DIRECTOR_CBO_CODES=list(settings.director_cbo_codes),
            TARGET_CITIES={
                region: list(cities) for region, cities in settings.target_cities.items()
            },
            MIN_DELAY=settings.min_delay,
            MAX_DELAY=settings.max_delay,
            REQUEST_TIMEOUT=settings.request_timeout,
            MAX_RETRIES=settings.max_retries,
            RETRY_DELAY=settings.retry_delay,
            COMPETENCIA=settings.competence,
            BASE_URL=settings.base_url,
            KIBANA_API=settings.kibana_api,
        )


# =============================================================================
# SCRAPER PRINCIPAL
# =============================================================================

class CNESScraper:
    """
    Scraper para extração de dados de hospitais do portal CNES.

    Utiliza a API Elasticsearch subjacente ao Kibana para obter dados
    de forma mais estável e eficiente que web scraping tradicional.
    """

    def __init__(self, config: Optional[CNESConfig] = None):
        self.config = config or CNESConfig()
        self.session = self._create_session()
        self.hospitals_data: List[Dict[str, Any]] = []

    def _create_session(self) -> requests.Session:
        """Cria sessão HTTP com headers apropriados para Kibana 8.8.2."""
        session = requests.Session()

        # Headers que simulam um navegador real + Kibana específicos
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Origin": self.config.BASE_URL,
            "Referer": f"{self.config.BASE_URL}/kibana/app/dashboards",
            "Connection": "keep-alive",
            "Content-Type": "application/json",
            # Kibana 8.8.2 specific headers
            "kbn-version": "8.8.2",
            "kbn-xsrf": "kibana",
        })

        return session

    def _delay(self):
        """Aplica delay aleatório entre requisições."""
        delay = random.uniform(self.config.MIN_DELAY, self.config.MAX_DELAY)
        time.sleep(delay)

    def _make_request(self, method: str, url: str, **kwargs) -> Optional[requests.Response]:
        """Faz requisição HTTP com retry logic."""
        for attempt in range(self.config.MAX_RETRIES):
            try:
                response = self.session.request(
                    method,
                    url,
                    timeout=self.config.REQUEST_TIMEOUT,
                    **kwargs
                )

                if response.status_code == 200:
                    return response
                elif response.status_code == 429:  # Rate limited
                    logger.warning(f"Rate limited. Aguardando {self.config.RETRY_DELAY}s...")
                    time.sleep(self.config.RETRY_DELAY * (attempt + 1))
                else:
                    logger.warning(f"Status {response.status_code} na tentativa {attempt + 1}")

            except requests.exceptions.RequestException as e:
                logger.error(f"Erro na requisição (tentativa {attempt + 1}): {e}")
                if attempt < self.config.MAX_RETRIES - 1:
                    time.sleep(self.config.RETRY_DELAY)

        return None

    def _init_session_cookies(self):
        """Inicializa sessão visitando a página principal."""
        logger.info("Inicializando sessão...")
        response = self._make_request("GET", self.config.BASE_URL)
        if response:
            logger.info("Sessão inicializada com sucesso")
        else:
            logger.warning("Falha ao inicializar sessão, continuando sem cookies")

    def _build_leitos_query(self, municipio: str) -> Dict:
        """
        Constrói query Elasticsearch para buscar hospitais no índice de leitos.

        Filtra por:
        - Município específico
        - Natureza jurídica privada
        - Competência atual
        """
        query = {
            "size": 10000,
            "query": {
                "bool": {
                    "must": [
                        {"match": {"MUNICIPIO.keyword": municipio}},
                        {"match": {"COMPETENCIA": self.config.COMPETENCIA}},
                    ],
                    "should": [
                        {"prefix": {"NATUREZA_JURIDICA.keyword": code}}
                        for code in self.config.PRIVATE_NATURE_CODES
                    ],
                    "minimum_should_match": 1
                }
            },
            "aggs": {
                "por_cnes": {
                    "terms": {
                        "field": "CNES.keyword",
                        "size": 10000
                    },
                    "aggs": {
                        "total_leitos": {"sum": {"field": "QT_EXIST"}},
                        "nome_fantasia": {"terms": {"field": "NOME_FANTASIA.keyword", "size": 1}},
                        "gestao": {"terms": {"field": "GESTAO.keyword", "size": 1}},
                        "natureza": {"terms": {"field": "NATUREZA_JURIDICA.keyword", "size": 1}},
                        "uf": {"terms": {"field": "UF.keyword", "size": 1}},
                    }
                }
            },
            "_source": [
                "CNES", "NOME_FANTASIA", "MUNICIPIO", "UF",
                "GESTAO", "QT_EXIST", "NATUREZA_JURIDICA", "COMPETENCIA"
            ]
        }
        return query

    def _build_geral_query(self, cnes_list: List[str]) -> Dict:
        """
        Constrói query para buscar dados de contato no índice geral.
        """
        query = {
            "size": 10000,
            "query": {
                "bool": {
                    "must": [
                        {"terms": {"CNES.keyword": cnes_list}},
                        {"match": {"COMPETENCIA": self.config.COMPETENCIA}},
                    ]
                }
            },
            "_source": [
                "CNES", "NOME_FANTASIA", "LOGRADOURO", "NUMERO",
                "BAIRRO", "CEP", "TELEFONE", "EMAIL",
                "MUNICIPIO", "UF"
            ]
        }
        return query

    def _build_profissionais_query(self, cnes_list: List[str]) -> Dict:
        """
        Constrói query para buscar diretores no índice de profissionais.
        """
        query = {
            "size": 10000,
            "query": {
                "bool": {
                    "must": [
                        {"terms": {"CNES.keyword": cnes_list}},
                    ],
                    "should": [
                        {"prefix": {"PROFISSIONAL_CBO.keyword": cbo}}
                        for cbo in self.config.DIRECTOR_CBO_CODES
                    ],
                    "minimum_should_match": 1
                }
            },
            "_source": [
                "CNES", "PROFISSIONAL_NOME", "PROFISSIONAL_CBO", "PROFISSIONAL_CNS"
            ]
        }
        return query

    def query_elasticsearch(self, index: str, query: Dict) -> Optional[Dict]:
        """
        Executa query no Elasticsearch via API Kibana bsearch.

        Args:
            index: Nome do índice (leitos, geral, profissionais)
            query: Query DSL do Elasticsearch

        Returns:
            Resposta JSON do Elasticsearch
        """
        # Kibana bsearch endpoint
        bsearch_url = f"{self.config.BASE_URL}/kibana/internal/bsearch"

        # Format for bsearch (batch search)
        bsearch_body = {
            "batch": [
                {
                    "request": {
                        "params": {
                            "index": index,
                            "body": query
                        }
                    }
                }
            ]
        }

        response = self._make_request("POST", bsearch_url, json=bsearch_body)

        if response:
            try:
                result = response.json()
                # Extract the actual search result from bsearch response
                if isinstance(result, list) and len(result) > 0:
                    return result[0].get("result", {}).get("rawResponse", result[0])
                elif isinstance(result, dict):
                    if "rawResponse" in result:
                        return result["rawResponse"]
                    return result
            except json.JSONDecodeError:
                pass

        # Fallback: try alternative endpoints
        alt_endpoints = [
            f"{self.config.BASE_URL}/kibana/api/console/proxy?path={index}/_search&method=POST",
            f"{self.config.BASE_URL}/kibana/elasticsearch/{index}/_search",
        ]

        for endpoint in alt_endpoints:
            response = self._make_request("POST", endpoint, json=query)
            if response:
                try:
                    return response.json()
                except json.JSONDecodeError:
                    continue

        return None

    def _source_matches_business_filters(self, source: Dict[str, Any]) -> bool:
        """Revalida competência e natureza no fallback de hits diretos."""

        competence = str(source.get("COMPETENCIA", ""))
        nature = str(source.get("NATUREZA_JURIDICA", ""))
        return competence == self.config.COMPETENCIA and any(
            nature.startswith(code) for code in self.config.PRIVATE_NATURE_CODES
        )

    def _extract_hospitals(
        self,
        leitos_data: Dict[str, Any],
        municipio: str,
        min_beds: int,
        max_beds: int,
    ) -> Dict[str, Dict[str, Any]]:
        """Consolida hospitais e aplica o intervalo sem reinserir rejeitados."""

        validate_bed_range(min_beds, max_beds)
        hospitals: Dict[str, Dict[str, Any]] = {}
        buckets = (
            leitos_data.get("aggregations", {})
            .get("por_cnes", {})
            .get("buckets", [])
        )

        if buckets:
            for bucket in buckets:
                total_beds = int(bucket.get("total_leitos", {}).get("value", 0))
                if not min_beds <= total_beds <= max_beds:
                    continue
                cnes = str(bucket["key"])
                hospitals[cnes] = {
                    "cnes": cnes,
                    "total_leitos": total_beds,
                    "nome_fantasia": self._get_first_bucket_key(bucket, "nome_fantasia"),
                    "gestao": self._get_first_bucket_key(bucket, "gestao"),
                    "natureza_juridica": self._get_first_bucket_key(bucket, "natureza"),
                    "uf": self._get_first_bucket_key(bucket, "uf"),
                    "municipio": municipio,
                }
            return hospitals

        grouped_hits: Dict[str, Dict[str, Any]] = {}
        for hit in leitos_data.get("hits", {}).get("hits", []):
            source = hit.get("_source", {})
            cnes = str(source.get("CNES", "")).strip()
            if not cnes or not self._source_matches_business_filters(source):
                continue
            try:
                quantity = int(float(source.get("QT_EXIST", 0) or 0))
            except (TypeError, ValueError):
                logger.warning("Quantidade de leitos inválida para CNES %s", cnes)
                continue

            hospital = grouped_hits.setdefault(
                cnes,
                {
                    "cnes": cnes,
                    "total_leitos": 0,
                    "nome_fantasia": source.get("NOME_FANTASIA"),
                    "municipio": municipio,
                    "uf": source.get("UF"),
                    "gestao": source.get("GESTAO"),
                    "natureza_juridica": source.get("NATUREZA_JURIDICA"),
                },
            )
            hospital["total_leitos"] += quantity

        return {
            cnes: hospital
            for cnes, hospital in grouped_hits.items()
            if min_beds <= hospital["total_leitos"] <= max_beds
        }

    def fetch_hospitals_by_city(
        self,
        municipio: str,
        min_beds: Optional[int] = None,
        max_beds: Optional[int] = None,
    ) -> List[Dict]:
        """
        Busca hospitais privados de médio porte em um município.

        Args:
            municipio: Nome do município em MAIÚSCULAS
            min_beds: Limite mínimo inclusivo; usa a configuração quando omitido
            max_beds: Limite máximo inclusivo; usa a configuração quando omitido

        Returns:
            Lista de hospitais encontrados
        """
        resolved_min = self.config.MIN_BEDS if min_beds is None else min_beds
        resolved_max = self.config.MAX_BEDS if max_beds is None else max_beds
        validate_bed_range(resolved_min, resolved_max)
        logger.info(
            "Buscando hospitais em %s com %s-%s leitos...",
            municipio,
            resolved_min,
            resolved_max,
        )

        # 1. Buscar dados de leitos
        leitos_query = self._build_leitos_query(municipio)
        leitos_data = self.query_elasticsearch("cnes_leitos*", leitos_query)

        if not leitos_data:
            logger.warning(f"Sem dados de leitos para {municipio}")
            return []

        self._delay()

        # 2. Consolidar por CNES e aplicar o intervalo solicitado
        hospitals = self._extract_hospitals(
            leitos_data,
            municipio,
            resolved_min,
            resolved_max,
        )

        logger.info(
            "Encontrados %s hospitais com %s-%s leitos em %s",
            len(hospitals),
            resolved_min,
            resolved_max,
            municipio,
        )

        if not hospitals:
            return []

        # 3. Buscar dados de contato
        cnes_list = list(hospitals.keys())
        geral_query = self._build_geral_query(cnes_list)
        geral_data = self.query_elasticsearch("cnes_geral*", geral_query)

        if geral_data and "hits" in geral_data and "hits" in geral_data["hits"]:
            for hit in geral_data["hits"]["hits"]:
                source = hit.get("_source", {})
                cnes = source.get("CNES")
                if cnes in hospitals:
                    hospitals[cnes].update({
                        "logradouro": source.get("LOGRADOURO"),
                        "numero": source.get("NUMERO"),
                        "bairro": source.get("BAIRRO"),
                        "cep": source.get("CEP"),
                        "telefone": source.get("TELEFONE"),
                        "email": source.get("EMAIL"),
                    })

        self._delay()

        # 4. Buscar diretores
        prof_query = self._build_profissionais_query(cnes_list)
        prof_data = self.query_elasticsearch("cnes_profissionais*", prof_query)

        if prof_data and "hits" in prof_data and "hits" in prof_data["hits"]:
            for hit in prof_data["hits"]["hits"]:
                source = hit.get("_source", {})
                cnes = source.get("CNES")
                if cnes in hospitals:
                    # Adiciona diretor (pode haver múltiplos, pega o primeiro)
                    if "diretor_nome" not in hospitals[cnes]:
                        hospitals[cnes]["diretor_nome"] = source.get("PROFISSIONAL_NOME")
                        hospitals[cnes]["diretor_cargo"] = source.get("PROFISSIONAL_CBO")

        return list(hospitals.values())

    def _get_first_bucket_key(self, bucket: Dict, agg_name: str) -> Optional[str]:
        """Extrai o primeiro valor de uma agregação terms."""
        if agg_name in bucket:
            buckets = bucket[agg_name].get("buckets", [])
            if buckets:
                return buckets[0].get("key")
        return None

    def scrape_all_cities(
        self,
        min_beds: Optional[int] = None,
        max_beds: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Executa scraping em todas as cidades configuradas.

        Returns:
            DataFrame com todos os hospitais encontrados
        """
        logger.info("=" * 60)
        logger.info("INICIANDO SCRAPING DE HOSPITAIS PRIVADOS - CNES")
        logger.info("=" * 60)

        # Inicializar sessão
        self._init_session_cookies()

        all_hospitals = []

        # Iterar por região e cidade
        for regiao, cidades in self.config.TARGET_CITIES.items():
            logger.info(f"\n--- Região: {regiao} ({len(cidades)} cidades) ---")

            for cidade in tqdm(cidades, desc=f"Processando {regiao}"):
                try:
                    hospitals = self.fetch_hospitals_by_city(cidade, min_beds, max_beds)

                    # Adicionar região aos dados
                    for h in hospitals:
                        h["regiao"] = regiao

                    all_hospitals.extend(hospitals)

                    # Delay entre cidades
                    self._delay()

                except Exception as e:
                    logger.error(f"Erro ao processar {cidade}: {e}")
                    continue

        logger.info(f"\n{'=' * 60}")
        logger.info(f"TOTAL: {len(all_hospitals)} hospitais encontrados")
        logger.info("=" * 60)

        # Criar DataFrame
        df = pd.DataFrame(all_hospitals)

        # Ordenar colunas
        column_order = [
            "regiao", "uf", "municipio", "cnes", "nome_fantasia",
            "total_leitos", "natureza_juridica", "gestao",
            "logradouro", "numero", "bairro", "cep",
            "telefone", "email", "diretor_nome", "diretor_cargo"
        ]

        # Reordenar apenas colunas existentes
        existing_cols = [c for c in column_order if c in df.columns]
        other_cols = [c for c in df.columns if c not in column_order]
        df = df.reindex(columns=existing_cols + other_cols)

        return df

    def export_csv(self, df: pd.DataFrame, filename: str = "hospitais_target.csv"):
        """
        Exporta DataFrame para CSV.

        Args:
            df: DataFrame com dados dos hospitais
            filename: Nome do arquivo de saída
        """
        # Adicionar metadados
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Renomear colunas para português
        column_names = {
            "regiao": "Região",
            "uf": "UF",
            "municipio": "Cidade",
            "cnes": "CNES",
            "nome_fantasia": "Nome do Estabelecimento",
            "total_leitos": "Total de Leitos",
            "natureza_juridica": "Natureza Jurídica",
            "gestao": "Tipo de Gestão",
            "logradouro": "Logradouro",
            "numero": "Número",
            "bairro": "Bairro",
            "cep": "CEP",
            "telefone": "Telefone",
            "email": "Email",
            "diretor_nome": "Nome do Diretor/Sócio",
            "diretor_cargo": "Cargo",
        }

        df_export = df.rename(columns=column_names)

        # Criar coluna de endereço completo
        if "Logradouro" in df_export.columns:
            df_export["Endereço Completo"] = df_export.apply(
                lambda row: f"{row.get('Logradouro', '')} {row.get('Número', '')}, {row.get('Bairro', '')} - CEP: {row.get('CEP', '')}".strip(),
                axis=1
            )

        # Salvar CSV
        df_export.to_csv(filename, index=False, encoding="utf-8-sig")
        logger.info(f"\nArquivo salvo: {filename}")
        logger.info(f"Total de registros: {len(df_export)}")
        logger.info(f"Timestamp: {timestamp}")

        return filename


# =============================================================================
# EXECUÇÃO PRINCIPAL
# =============================================================================

def main():
    """Função principal de execução."""
    parser = argparse.ArgumentParser(description="Coleta hospitais privados do CNES")
    parser.add_argument("--min-beds", type=int, default=50, help="Mínimo inclusivo de leitos")
    parser.add_argument("--max-beds", type=int, default=150, help="Máximo inclusivo de leitos")
    args = parser.parse_args()
    try:
        validate_bed_range(args.min_beds, args.max_beds)
    except ValueError as exc:
        parser.error(str(exc))
    print("""
    ╔══════════════════════════════════════════════════════════════╗
    ║     CNES Hospital Scraper - Hospitais Privados Médio Porte   ║
    ║                                                              ║
    ║  Extração de dados do portal ElastiCNES                      ║
    ║  Filtro: Privados | intervalo de leitos configurável         ║
    ╚══════════════════════════════════════════════════════════════╝
    """)
    print(f"Intervalo selecionado: {args.min_beds}-{args.max_beds} leitos")

    # Configuração padrão
    config = CNESConfig(MIN_BEDS=args.min_beds, MAX_BEDS=args.max_beds)

    # Criar scraper
    scraper = CNESScraper(config)

    # Executar scraping
    df = scraper.scrape_all_cities(args.min_beds, args.max_beds)

    # Exportar resultados
    if not df.empty:
        output_file = scraper.export_csv(df)
        print(f"\n✅ Sucesso! Dados salvos em: {output_file}")
    else:
        print("\n⚠️ Nenhum hospital encontrado com os filtros especificados.")
        print("   Verifique a conexão ou ajuste os parâmetros de busca.")

    return df


if __name__ == "__main__":
    main()
