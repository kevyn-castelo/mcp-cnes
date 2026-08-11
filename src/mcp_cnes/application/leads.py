"""Casos de uso da camada de inteligência comercial."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .analytics import _validate_filters
from .ports import CNESColumnarRepository
from .remote import validate_competence


class GroupByMaintainer:
    def __init__(self, repository: CNESColumnarRepository) -> None:
        self._repository = repository

    def execute(
        self,
        filters: Mapping[str, Any],
        limit: int = 100,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        _validate_filters(filters)
        if isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("limit deve estar entre 1 e 500")
        return self._repository.group_by_maintainer(filters, limit, batch_id)


class LeadTriggers:
    def __init__(self, repository: CNESColumnarRepository) -> None:
        self._repository = repository

    def execute(
        self,
        competence_a: str,
        competence_b: str,
        delta_min: int,
        establishment_type: str | None = None,
        batch_a: str | None = None,
        batch_b: str | None = None,
    ) -> dict[str, Any]:
        validate_competence(competence_a)
        validate_competence(competence_b)
        if competence_a >= competence_b:
            raise ValueError("competencia_a deve ser anterior a competencia_b")
        if isinstance(delta_min, bool) or delta_min < 1:
            raise ValueError("delta_min deve ser um inteiro maior que zero")
        return self._repository.lead_triggers(
            competence_a,
            competence_b,
            delta_min,
            establishment_type,
            batch_a,
            batch_b,
        )


class ScoreLeads:
    WEIGHT_NAMES = frozenset({"porte", "complexidade", "mix_pagador", "tendencia"})

    def __init__(self, repository: CNESColumnarRepository) -> None:
        self._repository = repository

    def execute(
        self,
        competence_a: str,
        competence_b: str,
        weights: Mapping[str, float],
        filters: Mapping[str, Any],
        limit: int = 100,
        batch_a: str | None = None,
        batch_b: str | None = None,
    ) -> dict[str, Any]:
        validate_competence(competence_a)
        validate_competence(competence_b)
        if competence_a >= competence_b:
            raise ValueError("competencia_a deve ser anterior a competencia_b")
        if set(weights) != self.WEIGHT_NAMES:
            raise ValueError("pesos deve informar porte, complexidade, mix_pagador e tendencia")
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
            for value in weights.values()
        ):
            raise ValueError("pesos devem ser números não negativos")
        if sum(weights.values()) <= 0:
            raise ValueError("ao menos um peso deve ser maior que zero")
        _validate_filters(filters)
        if isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("limit deve estar entre 1 e 500")
        return self._repository.score_leads(
            competence_a, competence_b, weights, filters, limit, batch_a, batch_b
        )
