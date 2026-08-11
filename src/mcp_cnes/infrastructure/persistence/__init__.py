"""Adapters de persistência."""

from .duckdb import DuckDBCNESRepository
from .memory import MemoryCNESRepository
from .sqlite import SQLiteCNESRepository

__all__ = ["DuckDBCNESRepository", "MemoryCNESRepository", "SQLiteCNESRepository"]
