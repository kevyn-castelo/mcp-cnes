# Política mínima de dados

O servidor MCP persiste somente a projeção pública de estabelecimentos necessária para
consulta: identificação CNES, nome, município, UF, classificação, gestão, convênio,
leitos e competência. Identificadores pessoais de profissionais de saúde não são
coletados, exportados nem persistidos.

Os lotes importados mantêm apenas contadores agregados, motivos de rejeição sem o
conteúdo das linhas e o nome-base do arquivo. A projeção ativa é substituída
atomicamente; lotes anteriores permanecem no staging para auditoria operacional e
podem ser removidos conforme a política de retenção da instalação. Arquivos CSV de
origem ficam sob responsabilidade do operador no diretório configurado e não são
copiados pelo servidor.
