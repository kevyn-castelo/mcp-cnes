"""Monólito modular do MCP CNES."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mcp-cnes")
except PackageNotFoundError:  # pragma: no cover - somente uso direto sem instalação
    __version__ = "0+unknown"

__all__ = ["__version__"]
