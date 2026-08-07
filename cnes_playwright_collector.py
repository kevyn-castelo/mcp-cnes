"""
CNES Data Collector - Abordagem baseada em Playwright
======================================================

Este módulo usa automação de browser para coletar dados do CNES Leitos,
aproveitando a funcionalidade nativa de "Download CSV" do Kibana.

"""

import asyncio
import os
import glob
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
import logging
import json

from mcp_cnes.infrastructure.config import Settings

# Configuração de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class CNESPlaywrightCollector:
    # URLs do sistema CNES
    DASHBOARD_URL = "https://elasticnes.saude.gov.br/leitos"
    BASE_URL = "https://elasticnes.saude.gov.br"

    # Configurações de download
    DEFAULT_DOWNLOAD_DIR = Path("./downloads")
    DEFAULT_TIMEOUT = 60000  # 60 segundos

    def __init__(
        self,
        download_dir: Optional[Path] = None,
        headless: bool = True,
        settings: Optional[Settings] = None,
    ):

        self.settings = settings or Settings()
        self.download_dir = download_dir or self.settings.data_dir
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        self.browser = None
        self.context = None
        self.page = None

    async def __aenter__(self):
        """Contexto async para uso com 'async with'."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Fecha recursos ao sair do contexto."""
        await self.stop()

    async def start(self):
        """Inicia o browser Playwright."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error("Playwright não está instalado. Execute: pip install playwright && playwright install chromium")
            raise

        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=['--disable-blink-features=AutomationControlled']
        )
        self.context = await self.browser.new_context(
            accept_downloads=True,
            viewport={'width': 1920, 'height': 1080}
        )
        self.page = await self.context.new_page()
        logger.info("Browser Playwright iniciado")

    async def stop(self):
        """Para o browser e libera recursos."""
        if self.browser:
            await self.browser.close()
        if hasattr(self, 'playwright') and self.playwright:
            await self.playwright.stop()
        logger.info("Browser Playwright encerrado")

    async def navigate_to_dashboard(self, wait_for_load: bool = True) -> bool:
        logger.info(f"Navegando para {self.settings.dashboard_url}")

        try:
            await self.page.goto(
                self.settings.dashboard_url,
                wait_until='networkidle',
                timeout=self.settings.browser_timeout_ms,
            )

            if wait_for_load:
                # Aguarda o iframe do Kibana carregar
                iframe_handle = await self.page.wait_for_selector('iframe', timeout=30000)
                if iframe_handle:
                    logger.info("Iframe do Kibana carregado")

                # Aguarda mais tempo para os dados carregarem
                await asyncio.sleep(15)

            return True

        except Exception as e:
            logger.error(f"Erro ao navegar: {e}")
            return False

    async def get_kibana_frame(self):
        """Obtém o frame do Kibana dentro do iframe."""
        frames = self.page.frames
        for frame in frames:
            if 'kibana' in frame.url.lower():
                return frame
        # Fallback: retorna o primeiro iframe
        iframe_element = await self.page.query_selector('iframe')
        if iframe_element:
            return await iframe_element.content_frame()
        return None

    async def scroll_to_table(self, table_name: str = "EXTRATO DOS LEITOS"):

        frame = await self.get_kibana_frame()
        if not frame:
            logger.warning("Não foi possível acessar o frame do Kibana")
            return False

        try:
            # Tenta encontrar o painel pelo nome
            for _ in range(10):  # Máximo 10 tentativas de scroll
                panels = await frame.query_selector_all('[data-test-subj="embeddablePanelHeading"]')
                for panel in panels:
                    text = await panel.inner_text()
                    if table_name.lower() in text.lower():
                        await panel.scroll_into_view_if_needed()
                        logger.info(f"Tabela '{table_name}' encontrada")
                        return True

                # Scroll down
                await frame.evaluate('window.scrollBy(0, 500)')
                await asyncio.sleep(1)

            logger.warning(f"Tabela '{table_name}' não encontrada após scroll")
            return False

        except Exception as e:
            logger.error(f"Erro ao procurar tabela: {e}")
            return False

    async def click_panel_options(self, panel_name: str = "EXTRATO DOS LEITOS") -> bool:

        frame = await self.get_kibana_frame()
        if not frame:
            return False

        try:
            # Busca botão de opções do painel
            buttons = await frame.query_selector_all('button')
            for button in buttons:
                aria_label = await button.get_attribute('aria-label')
                if aria_label and panel_name in aria_label and 'options' in aria_label.lower():
                    await button.click()
                    await asyncio.sleep(0.5)
                    logger.info(f"Menu de opções aberto para '{panel_name}'")
                    return True

            logger.warning("Botão de opções não encontrado")
            return False

        except Exception as e:
            logger.error(f"Erro ao abrir menu: {e}")
            return False

    async def download_csv(self) -> Optional[Path]:

        frame = await self.get_kibana_frame()
        if not frame:
            return None

        try:
            # Configura handler de download
            async with self.context.expect_download() as download_info:
                # Clica em "Download CSV"
                download_button = await frame.query_selector('text="Download CSV"')
                if not download_button:
                    # Fallback: busca em spans
                    spans = await frame.query_selector_all('span')
                    for span in spans:
                        text = await span.inner_text()
                        if text.strip() == 'Download CSV':
                            await span.click()
                            break
                else:
                    await download_button.click()

            download = await download_info.value

            # Salva o arquivo
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"cnes_leitos_{timestamp}.csv"
            filepath = self.download_dir / filename

            await download.save_as(str(filepath))
            logger.info(f"CSV baixado: {filepath}")
            return filepath

        except Exception as e:
            logger.error(f"Erro no download: {e}")
            return None

    async def collect_data(self) -> Optional[Path]:

        logger.info("Iniciando coleta de dados CNES")

        # 1. Navegar para o dashboard
        if not await self.navigate_to_dashboard():
            return None

        # 2. Scroll até a tabela
        await self.scroll_to_table()

        # 3. Abrir menu de opções
        if not await self.click_panel_options():
            logger.warning("Tentando abordagem alternativa...")
            # Pode tentar métodos alternativos aqui

        # 4. Download CSV
        filepath = await self.download_csv()

        return filepath


class CNESManualInstructions:
    """
    Instruções para coleta manual de dados quando automação não é viável.
    """

    @staticmethod
    def print_instructions():
        """Imprime instruções para download manual."""
        instructions = """
╔══════════════════════════════════════════════════════════════════════════════╗
║                    INSTRUÇÕES PARA DOWNLOAD MANUAL                            ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                                ║
║  1. Acesse: https://elasticnes.saude.gov.br/leitos                            ║
║                                                                                ║
║  2. Aguarde o dashboard carregar completamente                                 ║
║                                                                                ║
║  3. Role a página até encontrar a tabela "EXTRATO DOS LEITOS"                 ║
║                                                                                ║
║  4. Clique no ícone "⋮" (três pontos) no canto superior direito da tabela     ║
║                                                                                ║
║  5. Selecione "Download CSV"                                                   ║
║                                                                                ║
║  6. Aguarde o download (mensagem: "CSV Download Started")                      ║
║                                                                                ║
║    NOTA: Limite de 400.000 registros por download                            ║
║                                                                                ║
║    COLUNAS DISPONÍVEIS:                                                       ║
║     - COMPETÊNCIA, UF, CÓDIGO DO MUNICÍPIO, MUNICÍPIO                          ║
║     - CNES, NOME FANTASIA, TIPO DO ESTABELECIMENTO                             ║
║     - NATUREZA JURÍDICA, GESTÃO, CONVÊNIO SUS                                  ║
║     - TIPO DO LEITO, CÓDIGO DO LEITO, LEITO                                    ║
║     - LEITOS EXISTENTES, LEITOS SUS                                            ║
║                                                                                ║
╚══════════════════════════════════════════════════════════════════════════════╝
        """
        print(instructions)
        return instructions


async def main():
    """Função principal para teste."""
    # Verifica se Playwright está instalado
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("\n Playwright não está instalado.")
        print("Execute: pip install playwright && playwright install chromium\n")
        CNESManualInstructions.print_instructions()
        return

    # Executa coleta automatizada
    print("Iniciando coleta automatizada de dados CNES...")
    async with CNESPlaywrightCollector(headless=False) as collector:
        result = await collector.collect_data()

        if result:
            print(f"\n Dados coletados com sucesso: {result}")
        else:
            print("\n Falha na coleta automatizada.")
            CNESManualInstructions.print_instructions()


if __name__ == "__main__":
    asyncio.run(main())
