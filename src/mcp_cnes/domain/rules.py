"""Regras puras e conversões compartilhadas do CNES."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .errors import DomainValidationError


def normalize_column_name(value: str) -> str:
    """Normaliza acentos, espaços e separadores de um identificador."""

    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]+", "_", ascii_value.upper()).strip("_")


def parse_non_negative_int(value: Any, field_name: str) -> int:
    """Converte números do CNES sem aceitar valores negativos."""

    if value is None or str(value).strip() == "":
        return 0
    try:
        parsed = int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError) as exc:
        raise DomainValidationError(f"{field_name} inválido: {value}") from exc
    if parsed < 0:
        raise DomainValidationError(f"{field_name} não pode ser negativo")
    return parsed


def parse_bool(value: Any, default: bool = True) -> bool:
    """Converte representações usuais de sim/não para booleano."""

    if value is None or str(value).strip() == "":
        return default
    normalized = str(value).strip().casefold()
    if normalized in {"sim", "yes", "true", "1", "s"}:
        return True
    if normalized in {"não", "nao", "no", "false", "0", "n"}:
        return False
    raise DomainValidationError(f"valor booleano inválido: {value}")


def validate_bed_range(
    min_beds: int | None, max_beds: int | None
) -> tuple[int | None, int | None]:
    """Valida limites inclusivos opcionais informados pelo consumidor."""

    for label, value in (("mínimo", min_beds), ("máximo", max_beds)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise DomainValidationError(f"Limite {label} de leitos deve ser um inteiro")
        if value is not None and value < 0:
            raise DomainValidationError(f"Limite {label} de leitos não pode ser negativo")
    if min_beds is not None and max_beds is not None and min_beds > max_beds:
        raise DomainValidationError("Limite mínimo de leitos não pode ser maior que o máximo")
    return min_beds, max_beds


def is_within_bed_range(
    beds: int, min_beds: int | None = None, max_beds: int | None = None
) -> bool:
    """Informa se a quantidade de leitos pertence ao intervalo inclusivo."""

    validate_bed_range(min_beds, max_beds)
    return (min_beds is None or beds >= min_beds) and (max_beds is None or beds <= max_beds)
