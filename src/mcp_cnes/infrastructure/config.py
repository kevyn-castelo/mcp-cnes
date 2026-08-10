"""Settings explícitos e validados, sem efeitos colaterais durante import."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from mcp_cnes.domain.errors import ConfigurationError
from mcp_cnes.domain.rules import validate_bed_range

DEFAULT_PRIVATE_NATURE_CODES = ("2062", "2240", "2135", "3999", "4000", "2054", "2046")
DEFAULT_DIRECTOR_CBO_CODES = ("121010", "121005", "131215", "131210")
DEFAULT_TARGET_CITIES: Mapping[str, tuple[str, ...]] = {
    "NORTE": ("MANAUS", "PORTO VELHO"),
    "NORDESTE": ("RECIFE", "FORTALEZA", "CAMPINA GRANDE"),
    "SUL": (
        "PORTO ALEGRE",
        "BENTO GONCALVES",
        "CAXIAS DO SUL",
        "PELOTAS",
        "CANOAS",
        "CURITIBA",
        "MARINGA",
        "LONDRINA",
        "PINHAIS",
        "PONTA GROSSA",
        "CASCAVEL",
        "FOZ DO IGUACU",
        "FLORIANOPOLIS",
        "JOINVILLE",
        "ITAJAI",
        "BLUMENAU",
        "CHAPECO",
    ),
    "SUDESTE": (
        "SAO PAULO",
        "JUNDIAI",
        "CAMPINAS",
        "BARRETOS",
        "PRESIDENTE PRUDENTE",
        "SAO JOSE DOS CAMPOS",
        "RIO DE JANEIRO",
        "SANTOS",
        "NITEROI",
        "SAO GONCALO",
        "DUQUE DE CAXIAS",
        "NOVA IGUACU",
    ),
}


@dataclass(frozen=True)
class Settings:
    """Configuração única de runtime para servidor e coletores."""

    competence: str = "202512"
    min_beds: int = 50
    max_beds: int = 150
    target_cities: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_TARGET_CITIES)
    )
    private_nature_codes: tuple[str, ...] = DEFAULT_PRIVATE_NATURE_CODES
    director_cbo_codes: tuple[str, ...] = DEFAULT_DIRECTOR_CBO_CODES
    data_dir: Path = Path("downloads")
    database_path: Path = Path("downloads/cnes.sqlite3")
    columnar_database_path: Path = Path("downloads/cnes.duckdb")
    columnar_dir: Path = Path("downloads/parquet")
    max_csv_size_bytes: int = 100 * 1024 * 1024
    allowed_csv_files: tuple[str, ...] = ()
    batch_retention_count: int = 5
    output_dir: Path = Path(".")
    base_url: str = "https://elasticnes.saude.gov.br"
    kibana_api: str = "https://elasticnes.saude.gov.br/kibana/internal/bsearch"
    kibana_index: str = "cnes-leitos*"
    dashboard_url: str = "https://elasticnes.saude.gov.br/leitos"
    request_timeout: int = 60
    browser_timeout_ms: int = 60_000
    min_delay: float = 2.0
    max_delay: float = 5.0
    max_retries: int = 3
    retry_delay: float = 10.0
    remote_catalog_url: str = (
        "https://dadosabertos.saude.gov.br/dataset/hospitais-e-leitos"
    )
    remote_download_host: str = "s3.sa-east-1.amazonaws.com"
    remote_download_path_prefix: str = "/ckan.saude.gov.br/Leitos_SUS/"
    remote_dir: Path = Path("downloads/remote")
    remote_cache_dir: Path = Path("downloads/cache")
    remote_cache_ttl_seconds: int = 86_400
    remote_max_download_bytes: int = 100 * 1024 * 1024
    remote_max_concurrency: int = 2
    remote_user_agent: str = (
        "mcp-cnes/0.1 (+https://github.com/kevyn-castelo/mcp-cnes)"
    )
    remote_backoff_base: float = 1.0
    datasus_ftp_host: str = "ftp.datasus.gov.br"
    datasus_ftp_directory: str = "/cnes"
    datasus_max_download_bytes: int = 2 * 1024 * 1024 * 1024

    def __post_init__(self) -> None:
        try:
            validate_bed_range(self.min_beds, self.max_beds)
        except ValueError as exc:
            raise ConfigurationError(f"Faixa de leitos inválida: {exc}") from exc
        if not re.fullmatch(r"\d{6}", self.competence):
            raise ConfigurationError("competence deve usar o formato YYYYMM")
        month = int(self.competence[4:])
        if not 1 <= month <= 12:
            raise ConfigurationError("competence contém mês inválido")
        for name, value in (
            ("request_timeout", self.request_timeout),
            ("browser_timeout_ms", self.browser_timeout_ms),
            ("max_retries", self.max_retries),
            ("max_csv_size_bytes", self.max_csv_size_bytes),
            ("batch_retention_count", self.batch_retention_count),
            ("remote_cache_ttl_seconds", self.remote_cache_ttl_seconds),
            ("remote_max_download_bytes", self.remote_max_download_bytes),
            ("remote_max_concurrency", self.remote_max_concurrency),
            ("datasus_max_download_bytes", self.datasus_max_download_bytes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ConfigurationError(f"{name} deve ser um inteiro maior que zero")
        for name, value in (
            ("min_delay", self.min_delay),
            ("max_delay", self.max_delay),
            ("retry_delay", self.retry_delay),
            ("remote_backoff_base", self.remote_backoff_base),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ConfigurationError(f"{name} deve ser um número não negativo")
        if self.min_delay > self.max_delay:
            raise ConfigurationError("min_delay não pode ser maior que max_delay")
        for name, value in (
            ("base_url", self.base_url),
            ("kibana_api", self.kibana_api),
            ("dashboard_url", self.dashboard_url),
            ("remote_catalog_url", self.remote_catalog_url),
        ):
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ConfigurationError(f"{name} deve ser uma URL HTTP(S) válida")
        if not re.fullmatch(r"[A-Za-z0-9.-]+", self.remote_download_host):
            raise ConfigurationError("remote_download_host inválido")
        if not re.fullmatch(r"[A-Za-z0-9.-]+", self.datasus_ftp_host):
            raise ConfigurationError("datasus_ftp_host inválido")
        if not self.datasus_ftp_directory.startswith("/"):
            raise ConfigurationError("datasus_ftp_directory deve começar com /")
        if not self.remote_download_path_prefix.startswith("/"):
            raise ConfigurationError("remote_download_path_prefix deve começar com /")
        if not self.remote_user_agent.strip():
            raise ConfigurationError("remote_user_agent não pode ser vazio")
        if not self.private_nature_codes:
            raise ConfigurationError("private_nature_codes não pode ser vazio")
        if not re.fullmatch(r"[A-Za-z0-9_.*,-]+", self.kibana_index):
            raise ConfigurationError("índice Kibana inválido")
        if not self.director_cbo_codes:
            raise ConfigurationError("director_cbo_codes não pode ser vazio")
        if not self.target_cities or any(not cities for cities in self.target_cities.values()):
            raise ConfigurationError("target_cities deve conter regiões com cidades")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Constrói settings somente quando o bootstrap solicitar explicitamente."""

        env = os.environ if environ is None else environ

        def integer(name: str, default: int) -> int:
            raw = env.get(name)
            if raw is None:
                return default
            try:
                return int(raw)
            except ValueError as exc:
                raise ConfigurationError(f"{name} deve ser um inteiro") from exc

        def number(name: str, default: float) -> float:
            raw = env.get(name)
            if raw is None:
                return default
            try:
                return float(raw)
            except ValueError as exc:
                raise ConfigurationError(f"{name} deve ser um número") from exc

        def codes(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
            raw = env.get(name)
            return tuple(part.strip() for part in raw.split(",") if part.strip()) if raw else default

        default = cls()
        database_path = Path(
            env.get("MCP_CNES_DATABASE_PATH", str(default.database_path))
        )
        columnar_database_path = Path(
            env.get(
                "MCP_CNES_COLUMNAR_DATABASE_PATH",
                str(database_path.with_suffix(".duckdb")),
            )
        )
        columnar_dir = Path(
            env.get(
                "MCP_CNES_COLUMNAR_DIR",
                str(columnar_database_path.parent / "parquet"),
            )
        )
        remote_dir = Path(
            env.get("MCP_CNES_REMOTE_DIR", str(database_path.parent / "remote"))
        )
        remote_cache_dir = Path(
            env.get(
                "MCP_CNES_REMOTE_CACHE_DIR",
                str(database_path.parent / "cache"),
            )
        )
        cities: Mapping[str, tuple[str, ...]] = default.target_cities
        if raw_cities := env.get("MCP_CNES_TARGET_CITIES"):
            try:
                parsed = json.loads(raw_cities)
                cities = {str(region): tuple(map(str, values)) for region, values in parsed.items()}
            except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ConfigurationError(
                    "MCP_CNES_TARGET_CITIES deve ser um objeto JSON de regiões para cidades"
                ) from exc
        return cls(
            competence=env.get("MCP_CNES_COMPETENCE", default.competence),
            min_beds=integer("MCP_CNES_MIN_BEDS", default.min_beds),
            max_beds=integer("MCP_CNES_MAX_BEDS", default.max_beds),
            target_cities=cities,
            private_nature_codes=codes(
                "MCP_CNES_PRIVATE_NATURE_CODES", default.private_nature_codes
            ),
            director_cbo_codes=codes(
                "MCP_CNES_DIRECTOR_CBO_CODES", default.director_cbo_codes
            ),
            data_dir=Path(env.get("MCP_CNES_DATA_DIR", str(default.data_dir))),
            database_path=database_path,
            columnar_database_path=columnar_database_path,
            columnar_dir=columnar_dir,
            max_csv_size_bytes=integer(
                "MCP_CNES_MAX_CSV_SIZE_BYTES", default.max_csv_size_bytes
            ),
            allowed_csv_files=codes(
                "MCP_CNES_ALLOWED_CSV_FILES", default.allowed_csv_files
            ),
            batch_retention_count=integer(
                "MCP_CNES_BATCH_RETENTION_COUNT", default.batch_retention_count
            ),
            output_dir=Path(env.get("MCP_CNES_OUTPUT_DIR", str(default.output_dir))),
            base_url=env.get("MCP_CNES_BASE_URL", default.base_url),
            kibana_api=env.get("MCP_CNES_KIBANA_API", default.kibana_api),
            kibana_index=env.get("MCP_CNES_KIBANA_INDEX", default.kibana_index),
            dashboard_url=env.get("MCP_CNES_DASHBOARD_URL", default.dashboard_url),
            request_timeout=integer("MCP_CNES_REQUEST_TIMEOUT", default.request_timeout),
            browser_timeout_ms=integer(
                "MCP_CNES_BROWSER_TIMEOUT_MS", default.browser_timeout_ms
            ),
            min_delay=number("MCP_CNES_MIN_DELAY", default.min_delay),
            max_delay=number("MCP_CNES_MAX_DELAY", default.max_delay),
            max_retries=integer("MCP_CNES_MAX_RETRIES", default.max_retries),
            retry_delay=number("MCP_CNES_RETRY_DELAY", default.retry_delay),
            remote_catalog_url=env.get(
                "MCP_CNES_REMOTE_CATALOG_URL", default.remote_catalog_url
            ),
            remote_download_host=env.get(
                "MCP_CNES_REMOTE_DOWNLOAD_HOST", default.remote_download_host
            ),
            remote_download_path_prefix=env.get(
                "MCP_CNES_REMOTE_DOWNLOAD_PATH_PREFIX",
                default.remote_download_path_prefix,
            ),
            remote_dir=remote_dir,
            remote_cache_dir=remote_cache_dir,
            remote_cache_ttl_seconds=integer(
                "MCP_CNES_REMOTE_CACHE_TTL_SECONDS",
                default.remote_cache_ttl_seconds,
            ),
            remote_max_download_bytes=integer(
                "MCP_CNES_REMOTE_MAX_DOWNLOAD_BYTES",
                default.remote_max_download_bytes,
            ),
            remote_max_concurrency=integer(
                "MCP_CNES_REMOTE_MAX_CONCURRENCY",
                default.remote_max_concurrency,
            ),
            remote_user_agent=env.get(
                "MCP_CNES_REMOTE_USER_AGENT", default.remote_user_agent
            ),
            remote_backoff_base=number(
                "MCP_CNES_REMOTE_BACKOFF_BASE", default.remote_backoff_base
            ),
            datasus_ftp_host=env.get(
                "MCP_CNES_DATASUS_FTP_HOST", default.datasus_ftp_host
            ),
            datasus_ftp_directory=env.get(
                "MCP_CNES_DATASUS_FTP_DIRECTORY", default.datasus_ftp_directory
            ),
            datasus_max_download_bytes=integer(
                "MCP_CNES_DATASUS_MAX_DOWNLOAD_BYTES",
                default.datasus_max_download_bytes,
            ),
        )


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Bootstrap explícito que falha cedo com mensagens de configuração."""

    return Settings.from_env(environ)
