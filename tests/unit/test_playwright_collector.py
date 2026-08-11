from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mcp_cnes.application.ports import CNESCollector
from mcp_cnes.domain.errors import CollectorError
from mcp_cnes.domain.models import ImportBatch
from mcp_cnes.infrastructure.collectors import (
    PlaywrightCNESCollector,
    PlaywrightCsvDownloader,
)
from mcp_cnes.infrastructure.config import Settings


class FakeElement:
    def __init__(
        self,
        text: str = "",
        aria_label: str | None = None,
        *,
        click_error: Exception | None = None,
    ) -> None:
        self.text = text
        self.aria_label = aria_label
        self.clicked = False
        self.click_error = click_error

    async def inner_text(self) -> str:
        return self.text

    async def get_attribute(self, name: str) -> str | None:
        return self.aria_label if name == "aria-label" else None

    async def scroll_into_view_if_needed(self) -> None:
        return None

    async def click(self) -> None:
        if self.click_error is not None:
            raise self.click_error
        self.clicked = True


class FakeFrame:
    url = "https://elasticnes.saude.gov.br/kibana/app"

    def __init__(
        self, *, has_panel: bool = True, download_error: Exception | None = None
    ) -> None:
        self.panel = FakeElement("EXTRATO DOS LEITOS") if has_panel else None
        self.options = FakeElement(aria_label="EXTRATO DOS LEITOS options")
        self.download = FakeElement("Download CSV", click_error=download_error)

    async def query_selector_all(self, selector: str) -> list[FakeElement]:
        if "embeddablePanelHeading" in selector:
            return [self.panel] if self.panel else []
        if selector == "button":
            return [self.options]
        return []

    async def query_selector(self, selector: str) -> FakeElement | None:
        return self.download if "Download CSV" in selector else None


class FakeDownload:
    def __init__(self, content: str, *, save_error: Exception | None = None) -> None:
        self.content = content
        self.save_error = save_error

    async def save_as(self, path: str) -> None:
        if self.save_error is not None:
            raise self.save_error
        Path(path).write_text(self.content, encoding="utf-8")


class FakeDownloadInfo:
    def __init__(
        self, download: FakeDownload, *, receive_error: Exception | None = None
    ) -> None:
        self._download = download
        self.receive_error = receive_error

    async def __aenter__(self) -> FakeDownloadInfo:
        return self

    async def __aexit__(self, *args: object) -> None:
        if self.receive_error is not None:
            raise self.receive_error
        return None

    @property
    async def value(self) -> FakeDownload:
        return self._download


class FakePage:
    def __init__(
        self,
        frame: FakeFrame,
        *,
        receive_error: Exception | None = None,
        save_error: Exception | None = None,
    ) -> None:
        self.frames = [frame]
        self.expect_download_calls = 0
        self.download = FakeDownload(
            "CNES,NOME_FANTASIA,MUNICIPIO,UF,NATUREZA_JURIDICA,"
            "LEITOS_EXISTENTES,LEITOS_SUS,COMPETENCIA\n"
            "1234567,Hospital Norte,Manaus,AM,2062 - PRIVADA,75,50,202607\n"
            "9999999,Hospital Público,Manaus,AM,1000 - PÚBLICA,80,70,202607\n",
            save_error=save_error,
        )
        self.receive_error = receive_error

    async def goto(self, *args: object, **kwargs: object) -> None:
        return None

    def expect_download(self, **kwargs: object) -> FakeDownloadInfo:
        self.expect_download_calls += 1
        return FakeDownloadInfo(self.download, receive_error=self.receive_error)


class FakePageSession:
    def __init__(self, page: object) -> None:
        self.page = page

    async def __aenter__(self) -> object:
        return self.page

    async def __aexit__(self, *args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_playwright_download_uses_page_event_and_reports_output(tmp_path: Path) -> None:
    page = FakePage(FakeFrame())
    downloader = PlaywrightCsvDownloader(
        Settings(data_dir=tmp_path), page_session_factory=lambda: FakePageSession(page)
    )

    downloaded = await downloader.download()

    assert page.expect_download_calls == 1
    assert downloaded.exists()
    assert downloaded.parent == tmp_path


@pytest.mark.asyncio
async def test_playwright_failure_identifies_exact_stage(tmp_path: Path) -> None:
    page = FakePage(FakeFrame(has_panel=False))
    downloader = PlaywrightCsvDownloader(
        Settings(data_dir=tmp_path), page_session_factory=lambda: FakePageSession(page)
    )

    with pytest.raises(CollectorError) as raised:
        await downloader.download()

    assert raised.value.code == "playwright_stage_failed"
    assert raised.value.stage == "locate_panel"


@pytest.mark.asyncio
async def test_playwright_frame_access_failure_reports_locate_frame(tmp_path: Path) -> None:
    class BrokenFramesPage:
        async def goto(self, *args: object, **kwargs: object) -> None:
            return None

        @property
        def frames(self) -> list[FakeFrame]:
            raise RuntimeError("frames unavailable")

    page = BrokenFramesPage()
    downloader = PlaywrightCsvDownloader(
        Settings(data_dir=tmp_path), page_session_factory=lambda: FakePageSession(page)
    )

    with pytest.raises(CollectorError) as raised:
        await downloader.download()

    assert raised.value.stage == "locate_frame"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "page, expected_stage",
    [
        (FakePage(FakeFrame(download_error=RuntimeError("click"))), "trigger_download"),
        (FakePage(FakeFrame(), receive_error=RuntimeError("wait")), "receive_download"),
        (FakePage(FakeFrame(), save_error=RuntimeError("disk")), "save_download"),
    ],
)
async def test_playwright_download_failure_reports_exact_stage(
    tmp_path: Path, page: FakePage, expected_stage: str
) -> None:
    downloader = PlaywrightCsvDownloader(
        Settings(data_dir=tmp_path), page_session_factory=lambda: FakePageSession(page)
    )

    with pytest.raises(CollectorError) as raised:
        await downloader.download()

    assert raised.value.stage == expected_stage


@pytest.mark.asyncio
async def test_concurrent_downloads_use_distinct_paths(tmp_path: Path) -> None:
    downloaders = [
        PlaywrightCsvDownloader(
            Settings(data_dir=tmp_path),
            page_session_factory=lambda page=FakePage(FakeFrame()): FakePageSession(page),
        )
        for _ in range(2)
    ]

    paths = await asyncio.gather(*(downloader.download() for downloader in downloaders))

    assert paths[0] != paths[1]
    assert all(path.exists() for path in paths)


def test_playwright_adapter_implements_collector_port(tmp_path: Path) -> None:
    page = FakePage(FakeFrame())
    downloader = PlaywrightCsvDownloader(
        Settings(data_dir=tmp_path), page_session_factory=lambda: FakePageSession(page)
    )

    collector: CNESCollector = PlaywrightCNESCollector(downloader)
    result = collector.collect("Manaus", 50, 100)

    assert [hospital.cnes for hospital in result] == ["1234567"]
    assert not list(tmp_path.glob("cnes_leitos_*.csv"))


def test_playwright_adapter_removes_download_when_import_fails(tmp_path: Path) -> None:
    class FailingImporter:
        def import_file(self, filepath: Path) -> ImportBatch:
            raise RuntimeError("invalid csv")

    page = FakePage(FakeFrame())
    downloader = PlaywrightCsvDownloader(
        Settings(data_dir=tmp_path), page_session_factory=lambda: FakePageSession(page)
    )
    collector = PlaywrightCNESCollector(downloader, importer=FailingImporter())

    with pytest.raises(RuntimeError, match="invalid csv"):
        collector.collect("Manaus")

    assert not list(tmp_path.glob("cnes_leitos_*.csv"))


@pytest.mark.asyncio
async def test_playwright_stops_manager_when_browser_launch_fails(tmp_path: Path) -> None:
    class FailingChromium:
        async def launch(self, **kwargs: object) -> None:
            raise RuntimeError("launch failed")

    class Manager:
        chromium = FailingChromium()

        def __init__(self) -> None:
            self.stopped = False

        async def start(self) -> Manager:
            return self

        async def stop(self) -> None:
            self.stopped = True

    manager = Manager()
    downloader = PlaywrightCsvDownloader(
        Settings(data_dir=tmp_path), playwright_factory=lambda: manager
    )

    with pytest.raises(CollectorError) as raised:
        await downloader.download()

    assert raised.value.stage == "launch_browser"
    assert manager.stopped is True
