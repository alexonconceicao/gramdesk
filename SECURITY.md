# Política de segurança

## Modelo de ameaça

O Gramdesk protege contra:

1. **Rastreamento por terceiros** carregado dentro do Instagram Web (pixels, analytics, scripts de outros domínios).
2. **Vazamento entre contas**: uma conta nunca acessa cookies, storage ou cache de outra.
3. **Abuso da página**: o conteúdo web não abre janelas, não navega para fora, não pede permissões de hardware, não lê clipboard, não acessa arquivos e não dispara downloads por conta própria.
4. **Uso casual da máquina destravada** (bloqueio por senha opcional).
5. **Entrada maliciosa local**: config adulterada, nomes de arquivo com `../`, URLs fora do allowlist.

Não protege contra: malware rodando com seu usuário, acesso físico ao disco sem criptografia, nem contra o que o próprio Instagram coleta sobre a sua conta logada.

## Mapeamento OWASP

| Referência | Medida no código |
|---|---|
| A01 Broken Access Control | Allowlist de navegação e de recursos (`security.py`); downloads só de requisições criadas pelo app. |
| A02 Cryptographic Failures | Senha com scrypt (N=2^15, r=8, p=1), sal aleatório de 16 bytes, comparação em tempo constante; só HTTPS/WSS. |
| A03 Injection | Valores enviados ao JS são serializados com `json.dumps`; IDs validados por regex; nomes de arquivo saneados. |
| A04 Insecure Design | Sem servidor, sem conta, sem coleta. Dados locais por conta em diretórios `0700`. |
| A05 Security Misconfiguration | Plugins, PDF, conteúdo inseguro, acesso local a URLs remotas, DNS prefetch, hyperlink auditing desligados. |
| A06 Vulnerable Components | Flatpak sobre o runtime KDE + PyQt BaseApp do Flathub, que recebem atualizações de segurança do Chromium. |
| A08 Data Integrity | Config validada campo a campo, gravação atômica, arquivo corrompido é isolado. |
| A09 Logging Failures | O app não registra URLs, mensagens nem console do site. O `research.log` (0600) guarda só eventos da coleta (início, requisições, pausas, bloqueios, fim) com campos filtrados: nada de cookies, tokens, URLs ou textos do site. |
| Canal página → app | Botões na página falam com o app por mensagem de console assinada com token aleatório por sessão (o site não conhece o token); limite de 20 pedidos a cada 10 s; os arquivos passam pelas mesmas validações de URL/extensão/nome. |
| Instância única | Socket local restrito ao usuário (`UserAccessOption`); só aceita links do Instagram validados, pedidos de pesquisa (sempre com aprovação na janela) e a consulta `STATUS`, que responde só nomes das contas, se estão logadas e o estado de cota/resfriamento (nada quando o app está bloqueado). |
| Proteção da conta (pesquisa) | Ritmo humano decidido pelo app antes de cada requisição (2,5 a 6 s com jitter, pausa longa a cada 8, teto de 8/min e 60/h, concorrência 1), cota diária (3 pesquisas, 200 requisições), parada imediata em 429/limite/checkpoint/login sem novas tentativas e resfriamento exponencial persistente por conta (30 min até 24 h, `ratelimit.json` 0600). A coleta só começa com a janela aberta e a conta logada; a CLI nunca abre o app. Detecção de login usa só a existência do cookie de sessão, nunca o valor. |
| A10 SSRF | Downloads restritos a `*.cdninstagram.com` e `*.fbcdn.net`, porta 443, sem credenciais na URL. |
| MASVS-STORAGE / PRIVACY | Sem telemetria; remoção de conta apaga cookies/cache e o perfil do disco. |

## Reportar uma vulnerabilidade

Abra um [security advisory privado](https://github.com/alexonconceicao/gramdesk/security/advisories/new) no GitHub. Não publique detalhes em issues abertas antes da correção.
