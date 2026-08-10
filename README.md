# MCP CNES — Servidor de dados do CNES

Servidor MCP para carregar e consultar dados públicos do Cadastro Nacional de
Estabelecimentos de Saúde (CNES). O entrypoint padrão usa o SDK Python MCP v2
oficial com transporte `stdio`. `mcp_server.py` permanece temporariamente apenas
como baseline de paridade e não deve ser usado como rollback por clientes MCP.

## Ferramentas disponíveis

| Ferramenta | Descrição |
|---|---|
| `cnes_load_data` | Carrega um CSV exportado do dashboard CNES |
| `cnes_search_municipio` | Busca estabelecimentos por município |
| `cnes_search_cnes` | Busca um estabelecimento pelo código CNES |
| `cnes_search_uf` | Busca estabelecimentos por UF |
| `cnes_statistics` | Retorna estatísticas dos dados carregados |
| `cnes_download_instructions` | Explica como obter o CSV manualmente |
| `cnes_list_sources` / `cnes_list_competencias` | Descobre a fonte oficial e as competências mensais `YYYYMM` disponíveis |
| `cnes_fetch` | Baixa, filtra, normaliza e opcionalmente ativa uma competência |
| `cnes_normalize` / `cnes_validate_dataset` | Normaliza CSV local e verifica qualidade do lote |
| `cnes_list_lotes` / `cnes_use_lote` / `cnes_purge` | Gerencia histórico, lote ativo e cache |
| `cnes_aggregate` / `cnes_timeseries` / `cnes_diff` | Executa análises sobre lotes retidos |
| `cnes_search_advanced` / `cnes_export` | Combina filtros e exporta CSV, JSON ou XLSX |

## Fluxo remoto e fluxo manual

No fluxo recomendado, use `cnes_list_competencias` e depois `cnes_fetch` com uma
competência `YYYYMM`. A tool descobre o arquivo no catálogo oficial, aplica os
filtros localmente, gera um CSV canônico e, por padrão, carrega o resultado como
lote ativo. A resposta informa a fonte, cache, campos derivados e quais filtros
foram nativos ou locais.

O fluxo anterior continua disponível: obtenha as instruções com
`cnes_download_instructions` e carregue um CSV aprovado com `cnes_load_data`.
As seis tools históricas mantêm assinaturas e schemas congelados; elas sempre
consultam o lote ativo. Para consultar outro lote sem alterar seus contratos, use
`cnes_use_lote` antes da busca.

Detalhes e limitações das integrações estão em [docs/fontes.md](docs/fontes.md).

## Requisitos

- [uv](https://docs.astral.sh/uv/) 0.12 ou superior.
- Python 3.11 ou superior. O arquivo `.python-version` fixa Python 3.14 para o
  ambiente de desenvolvimento reproduzível.

Não use o `.venv` de outra máquina. O `uv` cria ou recria o ambiente local a
partir de `pyproject.toml` e `uv.lock`.

## Bootstrap reproduzível

```powershell
uv sync --locked
```

`pyproject.toml` é a fonte canônica das dependências e `uv.lock` fixa as versões
resolvidas. O antigo `requirements.txt` permanece somente como aviso de migração.

## Verificação local

```powershell
# Suíte padrão: não acessa a internet nem requer Playwright
uv run pytest

# Cobertura do pacote que receberá as camadas modernizadas
uv run pytest --cov=mcp_cnes --cov-report=term-missing

# Qualidade estática
uv run ruff check src tests mcp_server.py benchmarks
uv run pyright

# Servidor MCP oficial via stdio
uv run mcp-cnes

# Validação interativa com MCP Inspector
uv run mcp dev src/mcp_cnes/mcp_app.py

# Paridade de cutover entre o fallback e o SDK oficial
uv run pytest tests/contract/test_cutover_parity.py
```

Os testes de contrato em `tests/fixtures/contracts/` congelam os nomes, schemas e
exemplos de resposta das seis ferramentas existentes. Alterações nessas fixtures
devem ser revisadas como mudanças de contrato.

O benchmark de persistência pode ser reproduzido com
`uv run python benchmarks/benchmark_sqlite_import.py --rows 400000`; a última
medição registrada fica em `benchmarks/results/sqlite-400k.json`.

O snapshot `sdk-tools.snapshot.json` protege os schemas completos por SHA-256 e
mantém visíveis propriedades de entrada/saída e campos obrigatórios.

## Contrato do SDK MCP

- Os seis nomes históricos permanecem estáveis.
- Inputs e outputs possuem JSON Schema gerado a partir de type hints/Pydantic.
- Parâmetros extras são rejeitados e anunciados com
  `additionalProperties: false`.
- CNES exige sete dígitos, UF exige duas letras e `limit` aceita `1–500`.
- Falhas recuperáveis retornam `isError: true`, permitindo autocorreção pelo
  agente; elas não são retornadas como sucesso.
- Sucessos incluem `structuredContent` e conteúdo textual JSON para clientes
  anteriores à saída estruturada.
- O SDK negocia a revisão atual `2026-07-28` e o modo legado suportado.

Essas são correções intencionais em relação ao envelope legado. O fallback
`mcp_server.py` não é um entrypoint suportado para novos consumidores e será
retirado após o gate operacional descrito em `docs/cutover.md`.

O teste `tests/unit/test_architecture.py` também funciona como gate de
dependências: domínio não pode importar MCP, banco, HTTP, Playwright ou pandas;
aplicação só pode depender do domínio e de suas próprias portas.

## Configuração validada

O bootstrap lê settings somente quando o servidor ou um coletor é criado.
Configurações inválidas interrompem a inicialização com mensagem explícita, sem
iniciar rede, browser ou processamento de arquivos. Os principais nomes são:

| Variável | Padrão |
|---|---|
| `MCP_CNES_COMPETENCE` | `202512` |
| `MCP_CNES_MIN_BEDS` / `MCP_CNES_MAX_BEDS` | `50` / `150` |
| `MCP_CNES_TARGET_CITIES` | objeto JSON com regiões e cidades |
| `MCP_CNES_PRIVATE_NATURE_CODES` | códigos separados por vírgula |
| `MCP_CNES_DIRECTOR_CBO_CODES` | códigos separados por vírgula |
| `MCP_CNES_DATA_DIR` / `MCP_CNES_OUTPUT_DIR` | `downloads` / `.` |
| `MCP_CNES_DATABASE_PATH` | `downloads/cnes.sqlite3` |
| `MCP_CNES_MAX_CSV_SIZE_BYTES` | `104857600` |
| `MCP_CNES_ALLOWED_CSV_FILES` | vazio (todos os CSVs confinados ao diretório) |
| `MCP_CNES_BATCH_RETENTION_COUNT` | `5` lotes concluídos |
| `MCP_CNES_BASE_URL`, `MCP_CNES_KIBANA_API`, `MCP_CNES_DASHBOARD_URL` | ElastiCNES |
| `MCP_CNES_KIBANA_INDEX` | padrão de índice; default `cnes-leitos*` |
| `MCP_CNES_REQUEST_TIMEOUT` / `MCP_CNES_BROWSER_TIMEOUT_MS` | `60` / `60000` |
| `MCP_CNES_REMOTE_DIR` / `MCP_CNES_REMOTE_CACHE_DIR` | ao lado do banco SQLite |
| `MCP_CNES_REMOTE_CACHE_TTL_SECONDS` | `86400` |
| `MCP_CNES_REMOTE_MAX_DOWNLOAD_BYTES` | `104857600` |
| `MCP_CNES_REMOTE_MAX_CONCURRENCY` | `2` |
| `MCP_CNES_REMOTE_USER_AGENT` | identificação do projeto e repositório |

Delays e retries também podem ser definidos com `MCP_CNES_MIN_DELAY`,
`MCP_CNES_MAX_DELAY`, `MCP_CNES_MAX_RETRIES` e `MCP_CNES_RETRY_DELAY`.

A importação aceita somente arquivos `.csv` resolvidos dentro de
`MCP_CNES_DATA_DIR`. Quando `MCP_CNES_ALLOWED_CSV_FILES` é definido, os nomes
permitidos devem ser separados por vírgula. Travessia de diretório, escape por
link simbólico e arquivos acima do limite são rejeitados antes da leitura.
Durante o parsing, deduplicação e consolidação usam um SQLite temporário em disco,
removido ao final da carga. O catálogo retém somente a quantidade configurada de
lotes concluídos; o expurgo do histórico e de seu staging ocorre na mesma transação
que publica o lote atual.

## Faixa de leitos configurável

As buscas MCP por município e UF aceitam limites inclusivos opcionais definidos
em cada chamada pelo usuário ou agente:

```json
{
  "municipio": "Manaus",
  "min_leitos": 50,
  "max_leitos": 150,
  "limit": 20
}
```

- `min_leitos` e `max_leitos` podem ser usados juntos ou isoladamente.
- Quando ambos são omitidos, a busca MCP mantém o comportamento anterior e não
  filtra por porte.
- A resposta diferencia `total_encontrados` de `total_retornados` e informa os
  limites aplicados em `filtros_leitos`.
- Valores negativos ou intervalos invertidos retornam erro explícito.

O scraper direto mantém `50–150` como padrão por compatibilidade, mas também
aceita override por linha de comando:

```powershell
uv run python cnes_scraper.py --min-beds 20 --max-beds 300
```

## Dependência opcional de navegador

Os coletores externos implementam a porta `CNESCollector` em
`mcp_cnes.infrastructure.collectors`:

- `KibanaHttpCollector` recebe uma sessão HTTP injetável, aplica timeout/retry e
  consulta `internal/bsearch`, distinguindo `http_timeout`, `http_rate_limited`,
  `http_server_error` e erros de transporte.
- `PlaywrightCNESCollector` compõe o `PlaywrightCsvDownloader`, que identifica a
  etapa de falha, captura downloads por `page.expect_download`, usa nomes únicos e
  remove o CSV temporário após a importação.

Falhas externas usam `CollectorError`, com `code`, `stage`, `retryable` e
`status_code`, permitindo diferenciá-las de regressões internas sem vazar respostas.

Playwright pertence ao grupo opcional `browser` e não é instalado por `uv sync`
nem necessário para a suíte padrão.

```powershell
uv sync --locked --group browser
uv run playwright install chromium
uv run python cnes_playwright_collector.py
```

## Testes externos

Testes marcados como `live` são separados da suíte padrão e só acessam a internet
quando a variável de autorização está definida:

```powershell
$env:CNES_RUN_LIVE_TESTS = "1"
uv run pytest -m live
Remove-Item Env:CNES_RUN_LIVE_TESTS
```

O workflow `Live smoke` também pode ser iniciado manualmente no GitHub Actions.
Ele executa uma única consulta de contrato via Kibana, possui timeout de cinco minutos
e nunca é disparado em pull requests. A suíte padrão bloqueia conexões externas;
coletores HTTP devem usar respostas injetadas nos testes.

Os scripts históricos `test_api.py`, `test_scraper.py` e `test_mcp_server.py` são
diagnósticos manuais e não fazem parte da suíte automatizada em `tests/`.

## Uso como servidor MCP

Exemplo de configuração local. Substitua o diretório pelo caminho real do seu
checkout; nenhum caminho de usuário é codificado na aplicação.

```json
{
  "mcpServers": {
    "cnes": {
      "command": "uv",
      "args": [
        "--directory",
        "C:/caminho/absoluto/mcp_cnes",
        "run",
        "mcp-cnes"
      ]
    }
  }
}
```

## Cutover e rollback

O procedimento operacional completo está em [docs/cutover.md](docs/cutover.md).
Antes de alterar um cliente real, execute a paridade automatizada e gere um
manifesto do smoke `stdio` com `uv run mcp-cnes-cutover-smoke --help`. O manifesto
registra versão, revisão verificada, digest da fonte, protocolo, hashes de schema,
volume importado e a execução das seis ferramentas sem persistir conteúdo de
estabelecimentos ou caminhos absolutos. O comando exige banco novo e descartável,
recusa catálogos preexistentes e nunca sobrescreve um manifesto anterior.

O legado só pode ser removido depois da validação do cliente real, do ensaio de
rollback para um checkout oficial `last-known-good`, do inventário de consumidores
e da aprovação explícita do responsável.

## Troubleshooting

- `uv sync --locked` falha: confirme Python 3.11+ e uma versão recente do `uv`;
  remova apenas a `.venv` local e repita o bootstrap.
- o cliente não lista as tools: execute `uv run mcp-cnes` no mesmo diretório e
  confira `command`, `args` e caminhos absolutos na configuração do cliente.
- a carga rejeita o CSV: confirme que o arquivo está dentro de
  `MCP_CNES_DATA_DIR`, possui extensão `.csv`, respeita o limite e, quando usada,
  consta em `MCP_CNES_ALLOWED_CSV_FILES`.
- o smoke falha em uma busca: use município, UF e CNES que existam no mesmo CSV;
  uma resposta vazia é tratada como falha de validação, não como sucesso.
- suspeita de banco corrompido ou bloqueado: não apague o catálogo em uso; gere um
  banco separado para o smoke e preserve o anterior para rollback e diagnóstico.

## Fonte dos dados

O fluxo automático usa o dataset oficial Hospitais e Leitos do Portal de Dados
Abertos do SUS. Os dashboards abaixo permanecem como alternativa manual:

- Leitos: <https://elasticnes.saude.gov.br/leitos>
- Geral: <https://elasticnes.saude.gov.br/geral>
- Profissionais: <https://elasticnes.saude.gov.br/profissionais>

Para download manual, acesse o dashboard de leitos, localize o painel “EXTRATO
DOS LEITOS”, abra o menu do painel e escolha “Download CSV”. O dashboard informa
limite de 400.000 registros por download.

## Estrutura atual

```text
mcp_cnes/
├── pyproject.toml
├── uv.lock
├── src/mcp_cnes/
│   ├── domain/                # modelos, regras puras e erros
│   ├── application/           # casos de uso e Protocols
│   ├── infrastructure/        # settings, importação segura, memória e SQLite
│   ├── interfaces/mcp/        # servidor, tools e schemas do SDK oficial
│   ├── mcp_app.py             # objeto descoberto pelo MCP CLI/Inspector
│   └── __main__.py            # entrypoint stdio
├── tests/                     # suíte automatizada e fixtures de contrato
├── docs/cutover.md            # runbook, inventário, smoke e rollback
├── mcp_server.py              # baseline legado de paridade aguardando remoção
├── cnes_scraper.py            # coletor HTTP experimental
├── cnes_playwright_collector.py
├── sample_data.csv
└── downloads/
```

Dados do CNES são públicos e disponibilizados pelo Ministério da Saúde/DATASUS.
