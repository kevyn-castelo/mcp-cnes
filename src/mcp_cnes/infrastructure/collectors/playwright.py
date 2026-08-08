"""Download Playwright e adaptação do CSV baixado à porta CNESCollector."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from mcp_cnes.application.ports import CNESImporter
from mcp_cnes.domain.errors import CollectorError
from mcp_cnes.domain.models import HospitalInfo
from mcp_cnes.domain.rules import (
    is_within_bed_range,
    matches_nature_code,
    normalize_search_text,
    validate_bed_range,
)
from mcp_cnes.infrastructure.config import Settings
from mcp_cnes.infrastructure.importers import CsvCNESImporter


class PlaywrightCsvDownloader:
    """Executa etapas explícitas do dashboard e salva o download no data_dir."""

    def __init__(
        self,
        settings: Settings,
        *,
        page_session_factory: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
        playwright_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.settings = settings
        self._page_session_factory = page_session_factory or self._production_page_session
        self._playwright_factory = playwright_factory

    async def download(self) -> Path:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            session = self._page_session_factory()
            async with session as page:
                await self._navigate(page)
                frame = await self._find_frame(page)
                await self._locate_panel(frame)
                await self._open_options(frame)
                return await self._capture_download(page, frame)
        except CollectorError:
            raise
        except Exception as exc:
            raise CollectorError(
                "playwright_stage_failed",
                "launch_browser",
                "Falha ao iniciar o navegador de coleta",
            ) from exc

    async def _navigate(self, page: Any) -> None:
        try:
            await page.goto(
                self.settings.dashboard_url,
                wait_until="networkidle",
                timeout=self.settings.browser_timeout_ms,
            )
        except Exception as exc:
            raise CollectorError(
                "playwright_stage_failed",
                "navigate_dashboard",
                "Falha ao abrir o dashboard CNES",
                retryable=True,
            ) from exc

    async def _find_frame(self, page: Any) -> Any:
        try:
            for frame in page.frames:
                if "kibana" in str(frame.url).casefold():
                    return frame
            iframe = await page.query_selector("iframe")
            frame = await iframe.content_frame() if iframe is not None else None
        except Exception as exc:
            raise CollectorError(
                "playwright_stage_failed",
                "locate_frame",
                "Falha ao acessar o frame do Kibana",
            ) from exc
        if frame is None:
            raise CollectorError(
                "playwright_stage_failed",
                "locate_frame",
                "Frame do Kibana não encontrado",
            )
        return frame

    async def _locate_panel(self, frame: Any) -> None:
        try:
            panels = await frame.query_selector_all(
                '[data-test-subj="embeddablePanelHeading"]'
            )
            for panel in panels:
                if "extrato dos leitos" in (await panel.inner_text()).casefold():
                    await panel.scroll_into_view_if_needed()
                    return
        except Exception as exc:
            raise CollectorError(
                "playwright_stage_failed",
                "locate_panel",
                "Falha ao localizar a tabela de leitos",
            ) from exc
        raise CollectorError(
            "playwright_stage_failed",
            "locate_panel",
            "Tabela EXTRATO DOS LEITOS não encontrada",
        )

    async def _open_options(self, frame: Any) -> None:
        try:
            buttons = await frame.query_selector_all("button")
            for button in buttons:
                label = await button.get_attribute("aria-label")
                is_target = label and "extrato dos leitos" in label.casefold()
                if is_target and "options" in label.casefold():
                    await button.click()
                    return
        except Exception as exc:
            raise CollectorError(
                "playwright_stage_failed",
                "open_panel_options",
                "Falha ao abrir as opções da tabela de leitos",
            ) from exc
        raise CollectorError(
            "playwright_stage_failed",
            "open_panel_options",
            "Botão de opções da tabela de leitos não encontrado",
        )

    async def _capture_download(self, page: Any, frame: Any) -> Path:
        try:
            button = await frame.query_selector('text="Download CSV"')
            if button is None:
                raise CollectorError(
                    "playwright_stage_failed",
                    "trigger_download",
                    "Ação Download CSV não encontrada",
                )
        except CollectorError:
            raise
        except Exception as exc:
            raise CollectorError(
                "playwright_stage_failed",
                "trigger_download",
                "Falha ao acionar o download do CSV do CNES",
            ) from exc

        try:
            async with page.expect_download(timeout=self.settings.browser_timeout_ms) as info:
                try:
                    await button.click()
                except Exception as exc:
                    raise CollectorError(
                        "playwright_stage_failed",
                        "trigger_download",
                        "Falha ao acionar o download do CSV do CNES",
                    ) from exc
        except CollectorError:
            raise
        except Exception as exc:
            raise CollectorError(
                "playwright_stage_failed",
                "receive_download",
                "Falha ao receber o download do CSV do CNES",
                retryable=True,
            ) from exc

        try:
            download = await info.value
        except Exception as exc:
            raise CollectorError(
                "playwright_stage_failed",
                "receive_download",
                "Falha ao receber o download do CSV do CNES",
                retryable=True,
            ) from exc

        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        destination = self.settings.data_dir / (
            f"cnes_leitos_{timestamp}_{uuid4().hex}.csv"
        )
        try:
            await download.save_as(str(destination))
            return destination
        except Exception as exc:
            raise CollectorError(
                "playwright_stage_failed",
                "save_download",
                "Falha ao salvar o CSV do CNES",
                retryable=True,
            ) from exc

    @asynccontextmanager
    async def _production_page_session(self) -> AsyncIterator[Any]:
        factory = self._playwright_factory
        if factory is None:
            try:
                from playwright.async_api import (  # pyright: ignore[reportMissingImports]
                    async_playwright,
                )
            except ImportError as exc:
                raise CollectorError(
                    "playwright_unavailable",
                    "launch_browser",
                    "Playwright não está instalado no grupo browser",
                ) from exc
            factory = async_playwright

        manager = browser = context = None
        try:
            manager = await factory().start()
            browser = await manager.chromium.launch(headless=True)
            context = await browser.new_context(accept_downloads=True)
            yield await context.new_page()
        finally:
            try:
                if context is not None:
                    await context.close()
            finally:
                try:
                    if browser is not None:
                        await browser.close()
                finally:
                    if manager is not None:
                        await manager.stop()


class PlaywrightCNESCollector:
    """Converte o CSV obtido via Playwright em estabelecimentos canônicos."""

    def __init__(
        self,
        downloader: PlaywrightCsvDownloader,
        *,
        importer: CNESImporter | None = None,
    ) -> None:
        self._downloader = downloader
        self._importer = importer or CsvCNESImporter()
        self._settings = downloader.settings

    def collect(
        self,
        municipality: str,
        min_beds: int | None = None,
        max_beds: int | None = None,
    ) -> Sequence[HospitalInfo]:
        validate_bed_range(min_beds, max_beds)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise CollectorError(
                "playwright_async_context",
                "collect",
                "Use o downloader assíncrono dentro de um event loop existente",
            )
        filepath = asyncio.run(self._downloader.download())
        batch = None
        try:
            if filepath.stat().st_size > self._settings.max_csv_size_bytes:
                raise CollectorError(
                    "playwright_download_too_large",
                    "validate_download",
                    "CSV baixado excede o tamanho máximo permitido",
                )
            batch = self._importer.import_file(filepath)
            query = normalize_search_text(municipality)
            return tuple(
                hospital
                for hospital in batch.hospitals
                if query in normalize_search_text(hospital.municipio)
                and is_within_bed_range(
                    hospital.leitos_existentes, min_beds, max_beds
                )
                and matches_nature_code(
                    hospital.natureza_juridica,
                    self._settings.private_nature_codes,
                )
            )
        finally:
            if batch is not None:
                batch.close()
            filepath.unlink(missing_ok=True)
