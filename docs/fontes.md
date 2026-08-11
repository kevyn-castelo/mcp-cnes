# Fontes remotas e normalização

Validação executada em 9 de agosto de 2026. A implementação não usa credenciais,
não raspa o ElasticNES/Kibana e não fixa URLs de arquivos de competência.

## Fonte primária: Portal de Dados Abertos do SUS

- Catálogo: `GET https://dadosabertos.saude.gov.br/dataset/hospitais-e-leitos`.
- Descoberta: o HTML oficial contém o array JSON `resources`; a URL de cada
  recurso é descoberta em runtime.
- Download permitido: somente HTTPS no host `s3.sa-east-1.amazonaws.com`, sob o
  prefixo `/ckan.saude.gov.br/Leitos_SUS/`.
- Formato observado: ZIP com um CSV anual, separador `;`, codificação Latin-1.
- Competência: coluna `COMP`, no formato mensal `YYYYMM`. O catálogo anuncia o
  arquivo anual, e `cnes_list_competencias(ano?)` indexa os valores realmente
  presentes em um único recurso. Sem `ano`, consulta somente o arquivo do maior
  ano publicado; para histórico, o cliente faz uma chamada explícita por ano.
- Autenticação: nenhuma.

O arquivo anual observado cobre diretamente `COMP`, `UF`, `MUNICIPIO`, `CNES`,
`NOME_ESTABELECIMENTO`, `TP_GESTAO`, `CO_TIPO_UNIDADE`, `DS_TIPO_UNIDADE`,
`NATUREZA_JURIDICA`, `DESC_NATUREZA_JURIDICA`, `LEITOS_EXISTENTES` e
`LEITOS_SUS`. `CONVENIO_SUS` não existe na fonte e é derivado como
`LEITOS_SUS > 0`, sendo sempre declarado em `campos_derivados`.

| Campo canônico | Campo na fonte | Regra |
|---|---|---|
| `COMPETENCIA` | `COMP` | cópia |
| `UF` | `UF` | maiúsculas na validação de filtro |
| `MUNICIPIO` | `MUNICIPIO` | cópia; busca local parcial e sem acento |
| `CNES` | `CNES` | cópia |
| `NOME_FANTASIA` | `NOME_ESTABELECIMENTO` | renomeado |
| `TIPO_ESTABELECIMENTO` | `CO_TIPO_UNIDADE` + `DS_TIPO_UNIDADE` | código e descrição |
| `NATUREZA_JURIDICA` | `NATUREZA_JURIDICA` + `DESC_NATUREZA_JURIDICA` | código e descrição |
| `GESTAO` | `TP_GESTAO` | renomeado |
| `CONVENIO_SUS` | — | derivado de `LEITOS_SUS > 0` |
| `LEITOS_EXISTENTES` | `LEITOS_EXISTENTES` | inteiro não negativo |
| `LEITOS_SUS` | `LEITOS_SUS` | inteiro não negativo |

Todos os filtros dessa fonte são locais: o artefato anual é baixado antes de
aplicar competência, UF, município, tipo e faixa de leitos. A resposta da tool
distingue `filtros_nativos` de `filtros_locais` para deixar esse custo explícito.

## Fonte mensal: base completa do CNES

- Origem: `ftp://ftp.datasus.gov.br/cnes/BASE_DE_DADOS_CNES_[YYYYMM].ZIP`.
- Descoberta: listagem FTP filtrada pelo nome mensal; `SIZE` e `MDTM` formam a
  versão verificável do recurso quando a origem não oferece ETag.
- Tabelas projetadas: `tbEstabelecimento`, `tbMunicipio`,
  `rlEstabComplementar`, `tbLeito`, `tbTipoUnidade` e
  `tbNaturezaJuridica`. `tbTipoLeito` é usada quando publicada; layouts
  históricos usam `TP_LEITO` e o mapeamento oficial incorporado.
- Persistência: um Parquet imutável por lote, com catálogo e consultas em
  DuckDB. O conjunto completo não é materializado como objetos Python.
- Privacidade: somente estabelecimentos `TP_PFPJ=3` entram no schema padrão.
  A leitura usa projeção positiva; `NU_CPF` e demais campos pessoais nunca são
  persistidos ou expostos.

O contrato `v2` estende integralmente os 11 campos `v1`. Latitude e longitude
parseáveis são preservadas, enquanto `geo_confiavel` indica se o ponto cai nos
limites aproximados da UF. Valores impossíveis tornam-se nulos e o registro não
é descartado. Campos institucionais vazios permanecem nulos; nenhum valor
plausível é inventado.

Leitos são somados a partir de `rlEstabComplementar` e classificados pelas
descrições/códigos oficiais. UTI adulto, pediátrica e neonatal podem também
compor o total de leitos complementares; os grupos descrevem dimensões
comerciais e não são uma partição mutuamente exclusiva. Quantidades vazias ou
inválidas não são convertidas em zero no contrato v2: os campos afetados ficam
nulos e seus nomes entram em `campos_ausentes`.

Habilitações ativas são relacionadas por `rlEstabSipac` e descritas por
`tbSubGruposHabilitacao`. A janela `CMTP_INICIO`/`CMTP_FIM` é avaliada contra a
competência do arquivo; a lista e a contagem são preservadas por estabelecimento.

## Inteligência comercial e export CRM

Os agrupamentos por mantenedora usam somente `NU_CNPJ_MANTENEDORA`; como a tabela
mensal não publica o nome comercial da rede nessa projeção, `rede` permanece nulo
e `nome_mantenedora` aparece em `campos_ausentes`. Os gatilhos comparam dois lotes
v2 retidos e classificam `expansao`, `retracao`, `entrada` ou `saida`; diferenças
nos filtros de origem geram aviso explícito porque podem alterar a cobertura.
O limiar absoluto `delta_min` é aplicado a todos os quatro motivos. Quando existe
mais de um lote v2 para uma competência, `lote_a` e `lote_b` são obrigatórios para
desambiguar o recorte; gatilhos e score sempre devolvem os identificadores usados.

O score usa posições relativas dentro do recorte para porte e tendência, além da
proporção de leitos não SUS. Complexidade combina, por média explícita, o percentil
de leitos de UTI e o percentil da quantidade de habilitações ativas; ambos são
retornados separadamente. Todos os pesos das quatro dimensões são fornecidos na
chamada. O perfil `crm_generico` do export exige lote v2 e produz a chave
`cnes:cnpj`; valores institucionais vazios permanecem nulos.
Se `leitos_sus` superar `leitos_existentes`, a validação reprova o lote e o
agrupamento devolve o mix como nulo com alerta, sem truncar ou inventar proporção.

## Resiliência, cache e segurança

- timeout explícito, no máximo `MCP_CNES_MAX_RETRIES` tentativas e backoff
  exponencial;
- `429` e `5xx` são repetidos; demais `4xx` são terminais;
- concorrência limitada e `User-Agent` identificável;
- limite configurável para bytes compactados e limite adicional para o conteúdo
  descompactado;
- exatamente um CSV por ZIP;
- cache por fonte, recurso, competência e hash dos filtros, validado por SHA-256,
  com captura e validação do ETag publicado pela origem;
- o Parquet normalizado da base completa é reutilizado somente quando hash,
  versão do recurso e schema coincidem com seus metadados; corrupção dispara nova
  normalização a partir do ZIP já baixado;
- `cnes_purge` exige opt-in administrativo (`MCP_CNES_ALLOW_PURGE=true`) e
  confirmação explícita; a limpeza de cache remove ZIPs/metadados e Parquets
  normalizados do diretório remoto configurado, sem alcançar os Parquets
  imutáveis dos lotes;
- anos fechados são imutáveis; o ano corrente respeita TTL;
- escrita atômica e destinos confinados aos diretórios configurados.

## Fontes investigadas e não escolhidas

### API DEMAS

Especificação: `https://apidadosabertos.saude.gov.br/static/swagger.json`, versão
observada `1.8.29`. O documento Swagger 2.0 tinha uma vírgula terminal inválida,
não declarava `host`/`basePath`, e os paths sob `/v1` retornavam 404 enquanto os
paths raiz estavam operacionais.

- `/assistencia-a-saude/hospitais-e-leitos`: aceita `uf`, `limit` (até 1000) e
  `offset`; entregou município, tipo, natureza, gestão e leitos, mas não CNES nem
  competência. O filtro real `uf=AM` retornou 500.
- `/cnes/estabelecimentos`: aceita filtros de tipo, UF, município, status,
  atualização, `limit` e `offset`; entrega CNES, tipo, natureza e gestão, mas não
  leitos nem competência.

Não existe chave segura para unir os dois payloads. União por nome/município foi
rejeitada para evitar associação incorreta.

### CKAN e dados.gov.br

Os paths CKAN clássicos `/api/3/action/...` e `/api/action/...` retornaram 404, e
o host interno referenciado pelo portal não resolveu em DNS. A API do
`dados.gov.br` exige `chave-api-dados-abertos`/Bearer; ela foi descartada pela
regra explícita de operação sem credenciais.

### DATASUS DBC/DBF individual e ElasticNES

Os arquivos DBC/DBF individuais não foram escolhidos porque o ZIP mensal oficial
já reúne as tabelas CSV relacionais necessárias, sem dependência adicional de
descompressão DBC. ElasticNES permanece somente no fluxo manual de
`cnes_download_instructions`.
