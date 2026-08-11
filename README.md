# MCP CNES

[![CI](https://github.com/kevyn-castelo/mcp-cnes/actions/workflows/ci.yml/badge.svg)](https://github.com/kevyn-castelo/mcp-cnes/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/kevyn-castelo/mcp-cnes)](https://github.com/kevyn-castelo/mcp-cnes/releases/latest)
[![Python](https://img.shields.io/badge/Python-%3E%3D3.11-3776AB)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Servidor MCP local para consultar dados públicos do Cadastro Nacional de
Estabelecimentos de Saúde (CNES) e transformar registros hospitalares em recortes
úteis para pesquisa, qualificação de leads e exportação para CRM.

O MCP CNES usa fontes oficiais do Ministério da Saúde/DATASUS, funciona por
`stdio`, não exige chave de API e expõe **23 ferramentas MCP**.

## O que você pode fazer

- localizar hospitais por município, UF, tipo, gestão, natureza jurídica e porte;
- consultar razão social, CNPJ, mantenedora, endereço e contatos institucionais;
- diferenciar leitos de UTI, cirúrgicos, clínicos, obstétricos e complementares;
- identificar redes hospitalares pelo CNPJ da mantenedora;
- detectar expansão, retração, entrada e saída entre competências;
- calcular scores comerciais com pesos informados em cada chamada;
- exportar seleções auditáveis em CSV, JSON, JSONL ou XLSX;
- manter lotes históricos para comparação sem misturar competências.

> O CNES descreve capacidade instalada. O projeto não calcula nem estima taxa de
> ocupação. CPF e nomes de pessoas físicas não fazem parte dos schemas, retornos
> ou arquivos exportados.

## Início rápido

O primeiro objetivo é fazer o cliente MCP listar as ferramentas e executar uma
consulta real. O fluxo completo é:

```text
instalar Git e uv
  → clonar o repositório
  → sincronizar o ambiente
  → configurar o cliente MCP
  → carregar uma competência
  → consultar hospitais
```

A versão `0.1.2` é distribuída pelo GitHub. PyPI, npm e instalação própria por
`curl` ainda não são canais oficiais; por enquanto, use o checkout conforme as
instruções abaixo.

## Requisitos do sistema

- Windows 10/11, macOS ou Linux;
- [Git](https://git-scm.com/downloads);
- [uv](https://docs.astral.sh/uv/) 0.12 ou superior;
- acesso HTTPS ao Portal SUS e acesso FTP ao DATASUS para a base completa;
- espaço em disco compatível com os arquivos consultados.

O runtime suporta Python 3.11 ou superior. O arquivo `.python-version` seleciona
Python 3.14 para desenvolvimento, e o `uv` pode provisionar essa versão sem alterar
o Python global do sistema.

Arquivos da base completa podem ser grandes. O limite de download padrão dessa
fonte é 2 GiB; escolha um diretório com espaço livre suficiente antes da primeira
carga.

## Setup no sistema

### Instalar o uv

No Windows, instale o Git pelo site oficial ou pelo gerenciador de pacotes da sua
organização. Depois, em PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

No macOS ou Linux, instale o Git pelo gerenciador do sistema e execute:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Abra um novo terminal caso `uv` ainda não esteja no `PATH`.

### Instalar a versão estável

Os mesmos comandos funcionam em PowerShell, macOS e Linux:

```bash
git --version
uv --version

git clone --branch v0.1.2 --depth 1 https://github.com/kevyn-castelo/mcp-cnes.git
cd mcp-cnes
uv sync --locked
uv run python --version
```

Para contribuir ou testar alterações ainda não lançadas, clone `main` sem
`--branch` e sem `--depth 1`.

O comando `uv sync --locked` recria o ambiente a partir de `pyproject.toml` e
`uv.lock`. Não reutilize a `.venv` copiada de outra máquina.

### Verificar o servidor

No diretório do projeto, execute:

```powershell
uv run mcp-cnes
```

O processo utiliza `stdio` e fica aguardando um cliente MCP. Um terminal sem
prompt ou mensagens após a inicialização é esperado. Pressione `Ctrl+C` para
encerrar o teste manual.

## Configurar no cliente MCP

Adicione um servidor chamado `cnes` na configuração do seu cliente. Substitua
`CAMINHO_ABSOLUTO` pelo diretório do checkout.

```json
{
  "mcpServers": {
    "cnes": {
      "command": "uv",
      "args": [
        "--directory",
        "CAMINHO_ABSOLUTO",
        "run",
        "mcp-cnes"
      ]
    }
  }
}
```

| Sistema | Exemplo de caminho absoluto | Localizar `uv` |
|---|---|---|
| Windows | `C:/Users/SEU_USUARIO/mcp-cnes` | `where.exe uv` |
| macOS | `/Users/seu-usuario/mcp-cnes` | `command -v uv` |
| Linux | `/home/seu-usuario/mcp-cnes` | `command -v uv` |

Se o cliente não encontrar `uv`, substitua `"command": "uv"` pelo caminho
absoluto retornado na última coluna.

Salve a configuração e reinicie completamente o cliente MCP. O nome e a localização
do arquivo de configuração variam entre Claude Desktop, Cursor, VS Code, Codex e
outros clientes; consulte a documentação do cliente para localizar a seção
`mcpServers`.

## Configuração de dados e armazenamento

Sem configuração adicional, bancos, caches e arquivos remotos ficam sob
`downloads/` dentro do checkout. Para separar código e dados, adicione um bloco
`env` ao servidor já configurado:

```json
{
  "env": {
    "MCP_CNES_COLUMNAR_DATABASE_PATH": "C:/dados/mcp-cnes/cnes.duckdb",
    "MCP_CNES_COLUMNAR_DIR": "C:/dados/mcp-cnes/parquet",
    "MCP_CNES_OUTPUT_DIR": "C:/dados/mcp-cnes/exports"
  }
}
```

Use caminhos absolutos equivalentes em macOS ou Linux. Configure também os
diretórios de importação, download e cache da tabela abaixo quando quiser manter
**todos** os dados fora do checkout.

O arquivo [.env.example](.env.example) serve como referência, mas o entrypoint não
o carrega automaticamente: defina as variáveis no cliente MCP ou no ambiente do
processo.

### Variáveis mais úteis

| Variável | Padrão | Finalidade |
|---|---:|---|
| `MCP_CNES_DATA_DIR` | `downloads` | Diretório autorizado para importações CSV manuais |
| `MCP_CNES_COLUMNAR_DATABASE_PATH` | `downloads/cnes.duckdb` | Banco de consultas colunares |
| `MCP_CNES_COLUMNAR_DIR` | `downloads/parquet` | Lotes imutáveis em Parquet |
| `MCP_CNES_OUTPUT_DIR` | `downloads/exports` | Destino permitido para exports |
| `MCP_CNES_REMOTE_DIR` | `downloads/remote` | Artefatos baixados das fontes oficiais |
| `MCP_CNES_REMOTE_CACHE_DIR` | `downloads/cache` | Índices e metadados de cache |
| `MCP_CNES_BATCH_RETENTION_COUNT` | `5` | Quantidade de lotes concluídos retidos |
| `MCP_CNES_REMOTE_CACHE_TTL_SECONDS` | `86400` | TTL do cache de fontes ainda mutáveis |
| `MCP_CNES_REMOTE_MAX_DOWNLOAD_BYTES` | `104857600` | Limite do artefato anual do Portal SUS |
| `MCP_CNES_DATASUS_MAX_DOWNLOAD_BYTES` | `2147483648` | Limite do ZIP mensal da base completa |
| `MCP_CNES_REQUEST_TIMEOUT` | `60` | Timeout HTTP em segundos |
| `MCP_CNES_MAX_RETRIES` | `3` | Máximo de tentativas para falhas transitórias |

Para importação manual, `MCP_CNES_ALLOWED_CSV_FILES` pode restringir os nomes
aceitos, separados por vírgula. Arquivos fora de `MCP_CNES_DATA_DIR`, links que
escapem desse diretório e CSVs acima do limite são rejeitados antes da leitura.

Configurações inválidas interrompem a inicialização com erro explícito, antes de
qualquer download ou processamento.

## Fazer a primeira consulta

Depois de reiniciar o cliente, experimente esta sequência em linguagem natural.

### 1. Verificar as fontes

> Liste as fontes disponíveis no MCP CNES e informe o status de cada uma.

O cliente deve chamar `cnes_list_sources` e mostrar:

- `portal_sus_hospitais_leitos`, para o contrato `v1`;
- `datasus_base_completa`, para o contrato `v2`.

Uma fonte externa indisponível aparece como `indisponivel`, com o motivo. O
servidor não transforma falhas de rede em respostas vazias.

### 2. Descobrir uma competência

> Liste as competências do Portal SUS para 2025.

O cliente deve usar `cnes_list_competencias` com `ano=2025` e devolver competências
mensais no formato `YYYYMM`.

### 3. Carregar os dados

> Carregue a competência 202512 do Portal SUS para o Amazonas e deixe o lote ativo.

O cliente deve chamar `cnes_fetch` com algo equivalente a:

```json
{
  "competencia": "202512",
  "uf": "AM",
  "fonte": "portal_sus_hospitais_leitos",
  "auto_load": true
}
```

A resposta informa `lote_id`, quantidade de registros, filtros locais, ETag e se o
download usou cache. A primeira chamada pode demorar porque o Portal SUS publica
um arquivo anual completo; trocar apenas o município no mesmo ano deve reutilizar
o artefato.

### 4. Pesquisar hospitais

> Liste os 10 maiores hospitais de Manaus por número de leitos existentes.

O cliente pode usar `cnes_search_municipio` com `tipo_estabelecimento="HOSPITAL"`,
`order_by="leitos_existentes"` e `limit=10`.

Nesse ponto, a instalação está funcional: o servidor foi iniciado, uma competência
foi carregada e uma consulta retornou estabelecimentos reais.

## Usar o contrato v2

Para razão social, CNPJ, mantenedora, endereço, contato institucional,
geolocalização qualificada, habilitações e leitos por tipo, carregue a base mensal
completa:

> Carregue a competência 202512 usando a fonte datasus_base_completa.

Uma consulta v2 simples já pode usar esse lote. Gatilhos e score, porém, precisam
de um lote v2 retido para cada competência comparada. Antes dos exemplos de
tendência, solicite também:

> Carregue a competência 202012 usando a fonte datasus_base_completa.

Se houver mais de um lote v2 para a mesma competência, informe explicitamente
`lote_a` e `lote_b`. Depois, use prompts como:

- “Mostre hospitais com UTI em São Paulo usando o contrato v2.”
- “Agrupe as unidades por CNPJ da mantenedora e ordene pelo total de leitos.”
- “Compare 202012 e 202512 e mostre expansões de pelo menos 20 leitos.”
- “Calcule o score dos leads usando pesos iguais para porte, complexidade, mix
  pagador e tendência.”

O ZIP mensal completo pode conter centenas de milhares de estabelecimentos e leva
mais tempo e espaço que a fonte anual de hospitais e leitos.

## Fontes e contratos

| Fonte | Cobertura | Contrato | Características |
|---|---|---|---|
| `portal_sus_hospitais_leitos` | Hospitais, classificação e totais de leitos | `v1` | Arquivo anual; filtros aplicados localmente |
| `datasus_base_completa` | Dados institucionais, habilitações e leitos por tipo | `v2` | ZIP mensal; persistência em DuckDB/Parquet |
| CSV manual | Arquivo previamente aprovado pelo operador | `v1` | Importação confinada ao diretório configurado |

O contrato `v1` mantém os 11 campos canônicos originais. O contrato aditivo `v2`
preserva `v1` e acrescenta dados institucionais, geolocalização qualificada,
habilitações e leitos desagregados.

Leia [docs/fontes.md](docs/fontes.md) para conhecer layouts, regras de derivação,
cache, limites e fontes investigadas.

## Ferramentas disponíveis

As 23 ferramentas são agrupadas por objetivo:

| Grupo | Ferramentas |
|---|---|
| Ingestão e qualidade | `cnes_list_sources`, `cnes_list_competencias`, `cnes_fetch`, `cnes_download_instructions`, `cnes_load_data`, `cnes_normalize`, `cnes_validate_dataset` |
| Lotes | `cnes_list_lotes`, `cnes_use_lote`, `cnes_purge` |
| Buscas v1 | `cnes_search_cnes`, `cnes_search_municipio`, `cnes_search_uf`, `cnes_search_advanced` |
| Análises v1 | `cnes_statistics`, `cnes_aggregate`, `cnes_timeseries`, `cnes_diff` |
| Inteligência comercial v2 | `cnes_search_advanced_v2`, `cnes_group_by_mantenedora`, `cnes_leads_triggers`, `cnes_score_leads` |
| Exportação | `cnes_export` |

As 19 ferramentas existentes permanecem compatíveis com o contrato `v1`. As quatro
ferramentas comerciais usam `v2`; `cnes_export` mantém `v1` por padrão e também
suporta o perfil CRM baseado em `v2`.

Inputs e outputs possuem JSON Schema. Parâmetros extras são rejeitados, CNES exige
sete dígitos, UF exige duas letras e `limit` aceita valores de 1 a 500. Falhas
recuperáveis são retornadas ao cliente como erro MCP, não como sucesso vazio.
Referências de arquivos em respostas são relativas aos diretórios configurados;
o servidor não divulga caminhos absolutos do host.

## Exportar para CRM

`cnes_export` aceita CSV, JSON, JSONL e XLSX. Para exportar exatamente uma seleção,
informe `cnes_list`; para repetir uma consulta paginada, use os mesmos filtros,
`limit`, `offset` e `order_by`.

Exemplo de solicitação:

> Exporte estes 10 códigos CNES em JSONL com perfil crm_generico.

O perfil `crm_generico` usa `cnes:cnpj` como chave de deduplicação. CSV, JSON e
JSONL recebem proveniência por registro; XLSX recebe a aba `_metadados`. Os
metadados incluem competência, lote, filtros, versão da fonte, timestamp e versão
do contrato.

## Dados locais, cache e retenção

- Um lote fica ativo por vez, mas lotes anteriores podem ser consultados pelo
  identificador.
- A fonte anual reutiliza o download entre filtros do mesmo ano e revalida períodos
  ainda abertos.
- A base completa mantém Parquets imutáveis por lote e consulta os dados com
  DuckDB.
- `cnes_purge` fica desabilitado por padrão. O operador precisa iniciar o servidor
  com `MCP_CNES_ALLOW_PURGE=true` e cada chamada deve informar `confirmacao` como
  `EXCLUIR_LOTE:<lote_id>` ou `LIMPAR_CACHE`.
- Não apague manualmente um banco ou Parquet enquanto o servidor estiver em uso.

Consulte [docs/data-retention.md](docs/data-retention.md) para a política mínima de
dados e retenção.

## Privacidade e limites de interpretação

- O pipeline padrão aceita somente estabelecimentos classificados como pessoa
  jurídica na base completa.
- CPF e nomes de responsáveis, profissionais ou diretores não são coletados,
  persistidos ou exportados.
- Telefone, e-mail, CNPJ e endereço são tratados como dados institucionais.
- No contrato `v2`, campos numéricos marcados como ausentes permanecem nulos e
  aparecem em `campos_ausentes`; o contrato `v1` preserva sua semântica histórica.
- `leitos_sus / leitos_existentes` representa mix cadastral de leitos, não taxa de
  ocupação.
- Resultados dependem da atualização e disponibilidade das fontes oficiais.

## Desenvolvimento e verificação

### Suíte local

```powershell
# Testes determinísticos; não acessam serviços externos
uv run pytest -m "not live"

# Qualidade estática
uv run ruff check src tests benchmarks
uv run pyright

# Cobertura
uv run pytest tests/unit tests/integration -m "not live" `
  --cov=mcp_cnes.domain --cov=mcp_cnes.application `
  --cov-report=term-missing

# Contratos do SDK MCP
uv run pytest tests/unit/test_mcp_sdk_contract.py -m "not live"

# Artefatos de distribuição
uv build
```

Em macOS ou Linux, substitua o acento grave de continuação do PowerShell por `\`
ou execute o comando de cobertura em uma única linha.

### MCP Inspector

```powershell
uv run mcp dev src/mcp_cnes/mcp_app.py
```

### Testes externos

Os testes `live` só acessam a internet quando explicitamente autorizados:

```powershell
$env:CNES_RUN_LIVE_TESTS = "1"
uv run pytest -m live
Remove-Item Env:CNES_RUN_LIVE_TESTS
```

O workflow `Live smoke` pode ser iniciado manualmente no GitHub Actions. O CI de
pull requests executa auditoria de dependências, Ruff, Pyright, testes de unidade,
integração, contratos e cobertura.

## Solução de problemas

### `uv` não é reconhecido

Abra um novo terminal após a instalação. Use `where.exe uv` no Windows ou
`command -v uv` em macOS/Linux. Se necessário, coloque o caminho absoluto no campo
`command` da configuração MCP.

### O cliente não mostra as ferramentas

1. Execute `uv run mcp-cnes` no diretório do projeto.
2. Confirme que o processo permanece aguardando em `stdio`.
3. Valide o JSON do cliente e o caminho absoluto em `--directory`.
4. Reinicie completamente o cliente MCP.
5. Confira os logs do cliente, não apenas a janela de conversa.

### `uv sync --locked` falha

Confirme `uv --version` e acesso ao índice de pacotes. A `.venv` é descartável,
mas remova somente a `.venv` deste checkout e apenas quando nenhum processo do
MCP estiver usando seus executáveis.

### Uma fonte está indisponível

Use `cnes_list_sources` para obter o status e o motivo. Portal SUS e DATASUS são
serviços externos; tente novamente apenas quando a falha for marcada como
transitória. O servidor não substitui indisponibilidade por lista vazia.

### A importação manual foi rejeitada

Confirme que o arquivo é CSV, está dentro de `MCP_CNES_DATA_DIR`, respeita
`MCP_CNES_MAX_CSV_SIZE_BYTES` e, quando configurada, consta em
`MCP_CNES_ALLOWED_CSV_FILES`.

### O banco parece bloqueado ou inconsistente

Não apague o catálogo em uso. Encerre os clientes que executam o MCP e preserve os
arquivos para diagnóstico. Use um diretório separado para smokes e validações de
cutover.

## Documentação adicional

| Documento | Conteúdo |
|---|---|
| [Fontes e normalização](docs/fontes.md) | Origens oficiais, campos, cache e limitações |
| [Política mínima de dados](docs/data-retention.md) | Persistência, privacidade e retenção |
| [Cutover e rollback](docs/cutover.md) | Validação operacional e recuperação |
| [Changelog](CHANGELOG.md) | Histórico das versões |
| [Notas da v0.1.2](docs/releases/v0.1.2.md) | Versão recomendada e endurecida |
| [Notas da v0.1.1](docs/releases/v0.1.1.md) | Histórico; substituída por inconsistência de versão |
| [Notas da v0.1.0](docs/releases/v0.1.0.md) | Histórico da release inicial |
| [Política de segurança](SECURITY.md) | Reporte responsável de vulnerabilidades |

## Estrutura do projeto

```text
mcp-cnes/
├── pyproject.toml
├── uv.lock
├── src/mcp_cnes/
│   ├── domain/              # modelos e regras puras
│   ├── application/         # casos de uso e portas
│   ├── infrastructure/      # fontes, importação, persistência e exports
│   ├── interfaces/mcp/      # servidor, tools e schemas MCP
│   ├── mcp_app.py           # objeto usado pelo MCP CLI e Inspector
│   └── __main__.py          # entrypoint stdio
├── tests/                   # testes unitários, integração e contratos
├── docs/                    # fontes, retenção, cutover e releases
└── downloads/               # dados locais; ignorados pelo Git
```

## Licença e origem dos dados

O código é distribuído sob a [licença MIT](LICENSE).

Os dados do CNES são públicos e disponibilizados pelo Ministério da
Saúde/DATASUS. A licença do código não altera os termos, a disponibilidade nem a
responsabilidade sobre os dados de origem.
