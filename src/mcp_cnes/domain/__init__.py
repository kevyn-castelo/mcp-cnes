"""Entidades, regras puras e erros do domínio CNES."""

from .errors import (
    BatchNotFoundError,
    CNESDataLoadError,
    ConfigurationError,
    DomainValidationError,
)
from .models import HospitalInfo, ImportBatch, LoadSummary
from .rules import (
    is_within_bed_range,
    normalize_column_name,
    parse_bool,
    parse_non_negative_int,
    validate_bed_range,
)

__all__ = [
    "BatchNotFoundError",
    "CNESDataLoadError",
    "ConfigurationError",
    "DomainValidationError",
    "HospitalInfo",
    "ImportBatch",
    "LoadSummary",
    "is_within_bed_range",
    "normalize_column_name",
    "parse_bool",
    "parse_non_negative_int",
    "validate_bed_range",
]
