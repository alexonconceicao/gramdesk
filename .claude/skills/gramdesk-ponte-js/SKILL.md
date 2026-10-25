---
name: gramdesk-ponte-js
description: Como funciona o gramdesk/inject.js e a ponte JS/Python do Gramdesk: placeholders injetados, objeto congelado window[KEY], console assinado com TOKEN, modo fantasma no fetch/XHR/sendBeacon, índice de mídia (byPk/byCode/byImg), botões de baixar sobre a mídia (overlay em shadow DOM), snapshot de DOM e gate da pesquisa. Use antes de editar inject.js, mudar posição/comportamento dos botões de download, adicionar método novo na ponte ou tratar mensagem nova vinda do JS.
---

# Gramdesk: inject.js e a ponte

## Injeção

`web.py` `_install_script`: lê `_JS_TEMPLATE` no import e substitui:

| Placeholder | Valor |
|---|---|
| `__GD_KEY__` | `"__gd_"+16 hex`, aleatório por sessão (nome da ponte em `window`) |
| `__GD_TOKEN__` | 32 hex, assina mensagens JS para Python |
| `__GD_GHOST__` | modo fantasma da conta |
| `__GD_BUTTONS__` | `cfg.feed_buttons` |

`QWebEngineScript` "gramdesk": `DocumentCreation`, `MainWorld`, só frame principal. Versão antiga removida por nome antes de inserir. Guarda contra dupla injeção por `hasOwnProperty(KEY)`.

Placeholder novo: atualizar `web.py` e `tests/test_inject.py` / harness de `tests/test_reel_download.py`.

## Python chama JS

`page.runJavaScript(...)` sobre `window[KEY]` (congelado, não enumerável). Métodos: `snap(kind)`, `media()`, `scrollOnce()`, `start(id,mode,x,y)`, `take(id)`, `follow(id,op,arg)`, `research`, `cancelResearch`, `release`.
- `follow` (seguidores, usado por `Session.follow_call` na worker page): ops fechadas `me`, `followers`, `following` (cursor), `profile` (username), `unfollow` (pk, POST com `X-CSRFToken` do cookie `csrftoken`, confere `friendship_status.following === false`). Id do usuário logado vem do cookie `ds_user_id`. Resultado por `take`: `{ok,data}`, `{ok:false,blocked}` ou `{ok:false,error}`. Respeita `FLOOR_MS`.
- Assíncrono por polling: `start` retorna na hora; Python chama `take(id)` a cada 200 ms (~20 s de teto). Ids 16 hex, até 32 pendentes.
- `bridge_call` em `web.py` só libera `snap` e `scrollOnce`. Método novo precisa entrar nessa lista.

## JS chama Python

`send(obj)` faz `console.log('\u0000GD:'+TOKEN+':'+JSON)`. `GuardedPage.javaScriptConsoleMessage` aceita só esse prefixo e menos de 2 MB; ignora logs do site.
- `_on_bridge`: 20 mensagens / 10 s; página normal só aceita `type:'download'` (até 60 arquivos); worker page só `type:'research'`.
- Tipo novo de mensagem: adicionar no filtro de `_on_bridge` com validação estrita do payload. Conteúdo vindo da página é não confiável.

## Modo fantasma

`SEEN_RE` casa `StoriesV*Seen`, `ReelSeen`, `/stories/reel/seen`, `/media/seen/` na URL ou corpo. `fetch` devolve `Response` falso `{"status":"ok"}`, `XHR.send` simula `load`, `sendBeacon` devolve `true`. Defesa dupla: `security.STORY_SEEN_RE` no interceptor. Mudar fantasma reinstala o script e recarrega (`set_ghost`).

## Índice de mídia

Maps LRU (`MAX=4000`): `byPk`, `byCode`, `byImg` (basename da URL), `reels`. Alimentados por `ingest` das respostas que o site já recebe (`graphql|/api/v1/`) e por `scanInline()` dos `<script type="application/json">`. `walk` até profundidade 40.

`mediaOf` prioridade: `video_versions` (https, por área) > GraphQL `video_url` > vídeo sem URL vira `{kind:'video',url:''}` (**capa nunca vira o vídeo**) > `image_versions2` > `display_resources` > `display_url`. `quality()` impede miniatura de sobrescrever registro completo.

Resolução do item na tela: `fromPath` (stories, `/p|reel|reels|tv/`), `fromElement` (basename em `byImg`, `codeNear` subindo 14 ancestrais, `src` https). Fallback de API: `/api/v1/media/<pk>/info/` (conta em `apiCalls`), stories por `/api/v1/feed/reels_media/`. Nome: `<user>_<post|story>_<code|pk>[_N]`.

## Botões de baixar

- Host `<gd-overlay>` com shadow DOM fechado, `position:fixed`, z-index máximo, pool de botões.
- `candidates()`: até 24 mídias >= 180 px; pula avatar (`PROFILE_RE`), mídia coberta por modal (`covered`), capa duplicada sob vídeo.
- Post: "Baixar"/"Baixar esta" + "Todas (n)" em carrossel. Story: "Baixar story" no canto superior direito.
- `render` via rAF em scroll/resize, click/keyup (+380 ms) e intervalo de 700 ms.
- Clique manda `{type:'download', files, partial}`; `partial:true` = item do carrossel não identificado, baixa o post todo.
- Posição: feed (`location.pathname === '/'`, `feedRight()`) no canto superior direito, abaixo do menu "..." (`right: innerWidth - c.R + 10`), porque o nome da conta cobre o canto esquerdo; o teste `covered()` usa o mesmo canto. Demais páginas: canto superior esquerdo. "Todas (n)" 38 px abaixo.

## Snapshot (browse)

`snap(kind)` só lê DOM, **nenhuma requisição**. `annotate` marca `data-gd-current`, `data-gd-nw/nh`, `data-gd-vis`, `data-gd-w`; remove script/style/svg. `postRoot` isola o post. `signals()`: url, loginForm, alerts, title. `cachedIndex` devolve só o que está em `byCode`. HTML cortado em `SNAP_MAX` 1,5 MB.

## Gate da pesquisa

Cada chamada de API da pesquisa espera `release` do Python (fase `gate`), com piso local `FLOOR_MS=2000`. `Blocked` em 429/401/403, redirect login/challenge/checkpoint, JSON `fail/require_login/checkpoint_url/spam`, HTML inesperado. Fatal: encerra sem nova tentativa. Detalhes: skill `gramdesk-pesquisa`.

## Testes

- `node --check` em `tests/test_inject.py`.
- `tests/test_reel_download.py`: harness node com `window/document/location` mínimos e `fetch` que conta chamadas (prova `net==0`). Pulado sem `node`.
- Regex de caminho duplicados entre JS e `browse.py`: mudar nos dois.
