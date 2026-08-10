"""Identidade deterministica da projecao canonica CNES."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from typing import Any

from .models import HospitalInfo


def canonical_hospital_digest(
    hospitals: Iterable[HospitalInfo], *, presorted: bool = False
) -> str:
    """Calcula a identidade do conteudo logico efetivamente persistido."""

    digest = hashlib.sha256()
    ordered = (
        hospitals
        if presorted
        else sorted(hospitals, key=lambda hospital: (hospital.cnes, hospital.competencia))
    )
    for hospital in ordered:
        payload = json.dumps(
            asdict(hospital), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        digest.update(payload.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def contextual_batch_digest(
    content_digest: str,
    *,
    source: str,
    competence: str | None,
    filters: Mapping[str, Any],
) -> str:
    """Identifica uma coleta remota pelo conteúdo e pela proveniência canônica."""

    if source == "arquivo_local" and competence is None and not filters:
        return content_digest
    payload = json.dumps(
        {
            "content_sha256": content_digest,
            "source": source,
            "competence": competence,
            "filters": filters,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
