"""Erros explícitos do domínio CNES."""


class DomainValidationError(ValueError):
    """Uma regra de domínio recebeu um valor inválido."""


class BatchNotFoundError(DomainValidationError):
    """O lote solicitado não existe no catálogo."""


class CNESDataLoadError(ValueError):
    """Um lote de dados CNES não pôde ser validado ou lido."""


class ImportSecurityError(CNESDataLoadError):
    """A fonte solicitada viola a politica local de importacao."""


class ConfigurationError(ValueError):
    """A aplicação não pode iniciar com os settings informados."""


class CollectorError(RuntimeError):
    """Falha previsível de uma dependência externa de coleta."""

    def __init__(
        self,
        code: str,
        stage: str,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.retryable = retryable
        self.status_code = status_code
