# Política de segurança

## Versões suportadas

| Versão ou branch | Suporte de segurança |
| --- | --- |
| `main` / versão mais recente | Sim |
| Versões e branches antigos | Não |

## Como reportar uma vulnerabilidade

Não abra uma issue pública para uma vulnerabilidade. Use o recurso privado
**Report a vulnerability** em **Security > Advisories** do repositório no
GitHub. Se esse recurso não estiver disponível, contate o mantenedor
`@kevyn-castelo` diretamente pelo GitHub.

Inclua, quando possível:

- commit, versão e ambiente afetados;
- passos mínimos para reproduzir o problema;
- impacto observado e uma avaliação de gravidade;
- logs e evidências sanitizados, sem dados pessoais, tokens ou credenciais.

O projeto confirmará o recebimento em até 5 dias úteis, fará uma triagem
inicial em até 10 dias úteis e manterá o relator informado sobre a correção e
a divulgação coordenada. Não envie segredos reais no relatório.

## Escopo

O escopo inclui o código em `src/mcp_cnes`, os fluxos de importação local e a
cadeia de dependências e CI do repositório.

Não são alvos, por si só, a disponibilidade de serviços externos do governo,
o ambiente operacional administrado pelo usuário ou o conteúdo público dos
dados CNES. Ainda assim, reporte qualquer caminho que cause exposição de dados
não públicos, execução arbitrária ou acesso indevido.

## Pesquisa responsável

Testes autorizados são bem-vindos quando não envolvem destruição de dados,
negação de serviço, exfiltração de dados de terceiros ou persistência no
ambiente. Pare os testes assim que houver acesso a dados que não pertencem ao
pesquisador e reporte o achado de forma privada.
