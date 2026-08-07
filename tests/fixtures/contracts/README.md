# Baseline de contrato legado

Estes arquivos congelam o comportamento observável antes da migração para o SDK
MCP oficial:

- `tools.json`: nomes, descrições e schemas retornados por `get_tools()`.
- `examples.json`: uma chamada representativa para cada uma das seis ferramentas.
- `sdk-tools.snapshot.json`: resumo legível e hash do catálogo completo gerado
  pelo SDK oficial, incluindo schemas de entrada e saída.

`ultima_atualizacao` e `arquivo_fonte` são normalizados porque dependem do instante
e do caminho de execução. Os demais campos são comparados integralmente. O F1
atualizou intencionalmente o contrato para incluir filtros opcionais de leitos,
contagens antes/depois do limite e o resumo da importação. Atualize as fixtures
somente quando a mudança de contrato for intencional e revisada.

O F3 preservou os seis nomes e introduziu deliberadamente `outputSchema`,
`additionalProperties: false`, limites tipados e erros MCP com `isError: true`.
O conteúdo textual JSON continua presente ao lado de `structuredContent` para a
janela de compatibilidade.
