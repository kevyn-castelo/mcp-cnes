# Runbook de cutover do servidor MCP CNES

O entrypoint oficial é `uv run mcp-cnes`. O arquivo `mcp_server.py` existe apenas
como baseline temporário de paridade; ele não implementa o handshake do SDK MCP e
não é um rollback válido para clientes oficiais.

## 1. Preparar e validar

1. Execute `uv sync --locked` em um checkout limpo.
2. Configure `MCP_CNES_DATA_DIR` e `MCP_CNES_DATABASE_PATH` para diretórios
   autorizados. Use `.env.example` como referência, sem versionar dados ou segredos.
3. Execute a suíte de contrato do SDK oficial:

   ```powershell
   uv run pytest tests/unit/test_mcp_sdk_contract.py tests/contract -m "not live"
   ```

4. Registre a revisão exata que será validada:

   ```powershell
   $revision = git rev-parse HEAD
   ```

5. Gere o manifesto do smoke com um registro conhecido do CSV real e um caminho
   de banco **novo e descartável**:

   ```powershell
   uv run mcp-cnes-cutover-smoke `
     --data-dir downloads `
     --database-path downloads/cnes-cutover.sqlite3 `
     --csv downloads/exportacao.csv `
     --municipio Manaus --uf AM --cnes 1234567 `
     --revision $revision --timeout-seconds 30 `
     --output cutover-smoke.json
   ```

O comando recusa bancos preexistentes para não substituir a projeção nem expurgar
lotes do catálogo real. Em uma nova execução, escolha outro nome; não aponte o
smoke para `MCP_CNES_DATABASE_PATH` de produção.

O comando compara `--revision` com o `HEAD` realmente executado e registra também
um SHA-256 do pacote, `pyproject.toml`, `uv.lock` e README. Para o cutover real, o
manifesto deve apresentar `source.dirty: false`. Cada `--output` precisa ser novo:
uma evidência existente nunca é sobrescrita.

O manifesto registra versão, revisão verificada, digest da fonte, protocolo,
timeout, hashes dos schemas, volume importado e o sucesso semântico das seis tools.
Ele não inclui caminhos absolutos nem conteúdo dos estabelecimentos. Arquive-o fora
do repositório quando contiver metadados operacionais do ambiente real.

## 2. Inventariar consumidores

Antes da troca, procure `mcp_server.py`, `python mcp_server.py` e o nome do servidor
antigo em todos os arquivos de configuração dos clientes conhecidos. Registre para
cada consumidor: responsável, localização da configuração, entrypoint anterior,
data do teste e resultado. Uma busca somente neste repositório não comprova a
ausência de clientes externos.

## 3. Alterar o cliente real

Use o entrypoint instalado e mantenha as variáveis no campo `env` do cliente:

```json
{
  "mcpServers": {
    "cnes": {
      "command": "uv",
      "args": ["--directory", "C:/caminho/mcp_cnes", "run", "mcp-cnes"],
      "env": {
        "MCP_CNES_DATA_DIR": "C:/dados/cnes",
        "MCP_CNES_DATABASE_PATH": "C:/dados/cnes/cnes.sqlite3"
      }
    }
  }
}
```

Reinicie o cliente, confirme a listagem das seis tools e repita carga, buscas e
estatísticas com o mesmo probe do manifesto.

## 4. Preparar e ensaiar o rollback oficial

Antes do cutover, escolha uma revisão `last-known-good` que já use o SDK oficial,
crie um checkout separado e reproduzível e mantenha-o intacto durante a janela:

```powershell
git worktree add C:/operacao/mcp-cnes-last-known-good <REVISAO_APROVADA>
uv --directory C:/operacao/mcp-cnes-last-known-good sync --locked
```

Prepare uma segunda configuração do cliente apontando para esse checkout:

```json
{
  "mcpServers": {
    "cnes": {
      "command": "uv",
      "args": [
        "--directory",
        "C:/operacao/mcp-cnes-last-known-good",
        "run",
        "--locked",
        "mcp-cnes"
      ],
      "env": {
        "MCP_CNES_DATA_DIR": "C:/dados/cnes",
        "MCP_CNES_DATABASE_PATH": "C:/dados/cnes/cnes.sqlite3",
        "MCP_CNES_ALLOWED_CSV_FILES": "exportacao.csv"
      }
    }
  }
}
```

Copie para o rollback o mesmo bloco `env` aprovado no cliente ativo; não use os
defaults do checkout alternativo. Valide a compatibilidade do schema SQLite e
execute as seis tools contra o mesmo catálogo. Se o cutover falhar, restaure essa
configuração, reinicie o cliente e registre revisão e motivo do rollback.

O teste `tests/integration/test_official_rollback.py` comprova em CI que esse formato
de configuração negocia com o SDK oficial. Ele não substitui o ensaio da revisão
`last-known-good` no cliente real.

## 5. Gate de retirada do legado

Remova `mcp_server.py` somente quando o responsável aprovar explicitamente que:

- o manifesto do ambiente real está íntegro;
- as seis ferramentas funcionam no cliente real;
- o rollback foi ensaiado;
- a janela de compatibilidade terminou ou foi dispensada;
- todos os consumidores inventariados usam `mcp-cnes`.

Após a aprovação, remova também testes e referências exclusivas do rollback e
execute Ruff, Pyright, pytest e o smoke novamente. A remoção é uma mudança separada
e reversível por Git.
