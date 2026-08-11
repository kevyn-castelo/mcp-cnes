# Changelog

Todas as mudanças relevantes deste projeto serão documentadas neste arquivo.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o
projeto adota [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [0.1.0] - 2026-08-10

Primeira versão pública do MCP CNES. Consulte as
[notas completas da release](docs/releases/v0.1.0.md).

### Adicionado

- Servidor MCP em Python, via `stdio`, com 23 ferramentas para ingestão, consulta,
  análise e exportação de dados do CNES.
- Contrato `v1` compatível com as 19 ferramentas originais e contrato aditivo
  `v2` para dados institucionais, geolocalização qualificada e leitos por tipo.
- Integração com a base anual de Hospitais e Leitos do Portal SUS e com a base
  completa mensal do DATASUS.
- Persistência colunar com DuckDB e Parquet para os lotes da base completa.
- Busca por porte, tipo de estabelecimento, natureza jurídica, gestão, convênio
  SUS e faixa de leitos, com paginação e ordenação determinística.
- Agrupamento de unidades por CNPJ mantenedora, gatilhos de expansão/retração e
  score comercial com pesos informados pelo consumidor.
- Exportação auditável em CSV, JSON, JSONL e XLSX, incluindo proveniência e perfil
  de colunas para CRM.
- Cache de artefatos por fonte e período, com revalidação do conteúdo remoto.

### Integridade e segurança

- Campos numéricos ausentes ou inválidos permanecem nulos no contrato `v2`, sem
  serem confundidos com zero.
- CPF e nomes de pessoas físicas não fazem parte dos schemas, retornos ou exports.
- Coordenadas são preservadas com o indicador `geo_confiavel`, em vez de serem
  silenciosamente descartadas.
- Importações locais restringem extensão, diretório, tamanho e arquivos permitidos.
- O CI audita dependências travadas e exige Ruff, Pyright, testes e cobertura.

### Limitações conhecidas

- O CNES descreve capacidade instalada; esta versão não calcula nem estima taxa de
  ocupação.
- Disponibilidade, atualização e estrutura dos arquivos dependem dos serviços
  oficiais do Ministério da Saúde/DATASUS.
- Esta release fornece um servidor MCP local via `stdio`; não inclui hospedagem ou
  operação como serviço gerenciado.

[0.1.0]: https://github.com/kevyn-castelo/mcp-cnes/releases/tag/v0.1.0
