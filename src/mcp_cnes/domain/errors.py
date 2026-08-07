"""Erros explícitos do domínio CNES."""


class DomainValidationError(ValueError):
    """Uma regra de domínio recebeu um valor inválido."""


class CNESDataLoadError(ValueError):
    """Um lote de dados CNES não pôde ser validado ou lido."""


class ImportSecurityError(CNESDataLoadError):
    """A fonte solicitada viola a politica local de importacao."""


class ConfigurationError(ValueError):
    """A aplicação não pode iniciar com os settings informados."""
