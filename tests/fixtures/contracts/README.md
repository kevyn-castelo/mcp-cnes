# Fixtures de contrato do SDK MCP

Estes arquivos congelam o catálogo e os schemas do SDK MCP oficial:

- `sdk-tools.snapshot.json`: resumo legível e hash do catálogo completo,
  incluindo schemas de entrada e saída.
- `sdk-expansion-tools.snapshot.json`: snapshot das ferramentas adicionadas nas
  expansões do contrato.

Atualize os snapshots somente quando a mudança de contrato for intencional e
revisada. O teste `tests/unit/test_mcp_sdk_contract.py` valida nomes, schemas,
hashes, resposta estruturada, modo legado e os seis fluxos históricos.
