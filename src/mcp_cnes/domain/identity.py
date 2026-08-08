"""Identidade deterministica da projecao canonica CNES."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict

from .models import HospitalInfo


def canonical_hospital_digest(hospitals: Iterable[HospitalInfo]) -> str:
    """Calcula a identidade do conteudo logico efetivamente persistido."""

    digest = hashlib.sha256()
    ordered = sorted(hospitals, key=lambda hospital: (hospital.cnes, hospital.competencia))
    for hospital in ordered:
        payload = json.dumps(
            asdict(hospital), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        digest.update(payload.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()
