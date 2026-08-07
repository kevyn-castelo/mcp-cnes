"""Caso de uso de estatísticas agregadas."""

from typing import Any

from .ports import CNESRepository


class GetStatistics:
    def __init__(self, repository: CNESRepository) -> None:
        self._repository = repository

    def execute(self) -> dict[str, Any]:
        return self._repository.statistics()
