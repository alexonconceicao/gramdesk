---
name: gramdesk-arquitetura
description: Mapa da arquitetura do Gramdesk (PyQt6 + Qt WebEngine): inicialização, instância única e socket local (STATUS, B, R, show), contas com QWebEngineProfile isolado, hibernação, interceptor de requisições, configuração atômica, bloqueio por senha, bandeja e diretórios de dados. Use antes de mexer em app.py, web.py, config.py, security.py, prefs.py ou ui.py, ou ao adicionar comando novo no socket, opção nova nas Preferências ou campo novo na config.
---

# Gramdesk: arquitetura do app

Cliente desktop do Instagram Web. Python 3.10+, PyQt6 + PyQt6-WebEngine. Pacote `gramdesk/`, entrada `gramdesk.app:main`.

## Mapa de arquivos

| Arquivo | Papel |
|---|---|
| `app.py` | `MainWindow` (orquestra tudo), `LockScreen`, `EmptyState`, `main()`, socket local, `research_snapshot`, `set_option` |
| `web.py` | `AccountSession` (1 conta = 1 perfil + página + view), `GuardedPage`, `Interceptor`, `MediaFetch`, injeção do `inject.js`, ponte |
| `security.py` | Regras puras sem Qt: allowlists de host, `classify_request`, `safe_filename`, `is_safe_media_url`, scrypt, `parse_instagram_input` |
| `config.py` | Dataclasses `Config`/`Account`, `_parse` (validação), `save` atômico, diretórios, `VERSION` |
| `prefs.py` | `PreferencesPage`, seções appearance/accounts/privacy/downloads/research/shortcuts/about; `render()` refaz tudo |
| `ui.py` | Tema global (`MODE`, `ACCENT`, `PALETTE`), `apply_theme`, widgets `Toast`, `Sheet`, `Toggle`, `AvatarButton`, `Segmented` |
| `session_cookies.py` | `track_session`: logado se cookie `sessionid` ou `ds_user_id` em `*instagram.com` |
| `donation.py` | Doação opcional lida de `_build_info.json` (gerado só no build de release) |
| `ratelimit.py`, `research*.py`, `browse*.py`, `profile_queue.py`, `queue_qt.py`, `gallery.py`, `follows*.py` | Ver skills `gramdesk-ritmo`, `gramdesk-pesquisa`, `gramdesk-navegacao`, `gramdesk-downloads`, `gramdesk-seguidores` |

Padrão do projeto: **lógica pura em módulo sem Qt** (testável com pytest) + **cola Qt** separada. Siga isso em recurso novo.

## Inicialização (`app.py` `main()`)

1. `os.umask(0o077)`.
2. `--query-status`: imprime o snapshot JSON e sai (0, ou 3 se app fechado). Nunca abre janela.
3. `config.load()`, `QApplication` com desktop file `io.github.alexonconceicao.Gramdesk`.
4. `--selftest`: usado no CI Windows, confere que a ponte JS existe após 25 s.
5. `_forward_to_running(argv)`: se já há instância, manda comando pelo socket e sai.
6. `--research` sem instância aberta sai com 3 (pesquisa nunca abre o app).
7. `_chromium_flags`: flags do Chromium só valem antes do primeiro perfil. Por isso mudar o tema do site exige reiniciar.
8. `config.purge_pending` apaga perfis de contas removidas. Depois `MainWindow` e `_listen`.

## Socket local (instância única)

Caminho (`_socket_name`): `$XDG_RUNTIME_DIR/app/$FLATPAK_ID/gramdesk.sock` (Flatpak), `$XDG_RUNTIME_DIR/gramdesk.sock`, named pipe `gramdesk-<usuário>` no Windows. CLIs aceitam `GRAMDESK_SOCKET`. `QLocalServer` com `UserAccessOption`.

Protocolo: uma linha UTF-8 com `\n`, no máximo 4096 bytes.

| Comando | Comportamento |
|---|---|
| `STATUS` | Responde JSON de `research_snapshot()` (`proto:1`, `locked`, `research_running`, `browse`, `accounts[{id,name,open,logged_in,current,rate,browse}]`, `limits`) e fecha. Bloqueado: só `{proto,app,locked:true}` |
| `B <json>` | Navegação passo a passo. Conexão fica aberta até `win.browse.handle(req, reply)` responder |
| `F <json>` | Seguidores (`op`: status, check, list). Conexão aberta até `win.follows.handle(req, reply)` responder. Nunca aceita remoção |
| `R <json>` | Pedido de pesquisa. Sem resposta; sempre pede aprovação na janela |
| `show` | Traz a janela |
| outro texto | Traz a janela e `open_link(texto)` |

Comando novo: siga o modelo do `B` (JSON validado, `proto:1`, resposta única, códigos de saída coerentes com as CLIs). Nunca deixe o socket abrir a janela ou iniciar ação de rede sem aprovação do usuário.

## Contas e isolamento

- `Account`: `id` 12 hex, `name` até 40, `ghost=True`, `color` 0-7, `zoom` 50-200, `keep_alive=False`. Máximo 20 contas.
- `AccountSession._build` (lazy, na primeira ativação): `QWebEngineProfile("gd-"+id)`, storage `DATA_DIR/profiles/<id>`, cache `CACHE_DIR/profiles/<id>` (0700). **Setters do perfil antes de criar qualquer página.**
- `_harden()`: sem plugins/PDF/janelas JS/clipboard, WebRTC só interfaces públicas, todas as permissões negadas.
- Cookies dos hosts de mídia espelhados num `QNetworkCookieJar` usado pelo `MediaFetch`.
- Remover conta: `wipe()` imediato; diretórios só somem na próxima inicialização (`pending_delete`).

## Hibernação

`switch_to` ativa a nova e agenda `prev.hibernate()`. `hibernate()` silencia; com `keep_alive` para aí; senão lifecycle `Discarded` (padrão `cfg.hibernate="discard"`) ou `Frozen`. `activate()` volta a Active. Página de pesquisa/worker é separada e não hiberna com a view.

## Rede e navegação

`security.classify_request` (chamado pelo `Interceptor`, lê config ao vivo):
1. `data/blob/about/qrc` passam; fora de `https/wss` bloqueia.
2. `block_third_party`: só sufixos de `RESOURCE_HOSTS` (instagram.com, cdninstagram.com, fbcdn.net, facebook.com, fbsbx.com) + reCAPTCHA em `/recaptcha/`.
3. `block_telemetry`: `TELEMETRY_RE`.
4. `ghost`: `STORY_SEEN_RE` (REST). GraphQL "visto" é barrado no JS (skill `gramdesk-ponte-js`).

`GuardedPage.acceptNavigationRequest`: só https em instagram.com/facebook.com; link externo vira `externalLink` com confirmação antes do navegador do sistema; pop-ups capturados.

## Configuração

- Chaves: `accounts`, `last_account`, `hibernate`, `block_third_party`, `block_telemetry`, `theme`, `accent`, `site_follows_theme`, `idle_lock`, `feed_buttons`, `tray`, `donation`, `lock`, `pending_delete`, `research_limits`.
- `_parse` descarta campo inválido e volta ao padrão. JSON corrompido vira `config.json.bad`.
- `save`: `mkstemp` + `fsync` + `chmod 0600` + `os.replace`.
- UI grava só por `win.set_option` (whitelist em `app.py`), `win.update_account`, `win.set_appearance`.

**Opção nova:** campo no dataclass + validação em `_parse` + serialização + whitelist do `set_option` + controle em `prefs.py` + teste em `tests/test_config.py`. Se o valor vai embutido no JS (como `ghost`, `feed_buttons`), precisa reinstalar o script e recarregar.

## Diretórios

1. Portátil: `GRAMDESK_HOME` absoluto, ou `portable.txt` ao lado do exe congelado (`<exe>/data`).
2. Windows: `%APPDATA%\Gramdesk`, `%LOCALAPPDATA%\Gramdesk\{Data,Cache}`.
3. Linux: XDG + `/gramdesk`. No Flatpak o XDG já aponta para `~/.var/app/io.github.alexonconceicao.Gramdesk/`.

`DATA_DIR/ratelimit.json`, `DATA_DIR/research.log`, `DATA_DIR/queues/`, `DATA_DIR/follows/<conta>/`. Downloads em `~/Downloads/Gramdesk/`.

## Bloqueio, bandeja, tema

- Senha: scrypt N=2^15 r=8 p=1, sal 16 bytes, `hmac.compare_digest`; mínimo 6 caracteres; após 5 erros espera `min(300, 15*2**(n-5))` s. Protege só a interface.
- `idle_lock` 0/5/15/30/60 min. Com app bloqueado, links e pedidos ficam pendentes até desbloquear; `switch_to` e aberturas de páginas não fazem nada.
- Bandeja: `cfg.tray` (padrão True); fechar só esconde; `quit_app` encerra de fato.

## Invariantes

- Sem telemetria, sem servidor próprio, sem atualização automática.
- Todo arquivo do app com 0600, diretórios 0700, escrita atômica.
- Nada de URL, cookie ou token em log.
- Textos de UI em português, sem travessão.
