"""Adapters de persistência."""

from .memory import MemoryCNESRepository
from .sqlite import SQLiteCNESRepository

__all__ = ["MemoryCNESRepository", "SQLiteCNESRepository"]
