"""Adapters substituíveis para fontes externas do CNES."""

from .http import KibanaHttpCollector
from .playwright import PlaywrightCNESCollector, PlaywrightCsvDownloader

__all__ = [
    "KibanaHttpCollector",
    "PlaywrightCNESCollector",
    "PlaywrightCsvDownloader",
]
