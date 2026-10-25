---
name: gramdesk-pesquisa
description: Pesquisa de perfil do Gramdesk (coleta somente leitura + análise com Claude Code): página oculta (worker page), endpoints chamados pelo inject.js, ResearchJob e fases da ponte, cache e retomada (research_cache.py), CLI tools/gramdesk_research.py com códigos de saída, análise --restricted e validação das saídas. Use ao mexer em research.py, research_ui.py, research_cache.py, tools/gramdesk_research.py ou ao criar outro recurso que precise ler dados da API do Instagram em segundo plano.
---

# Gramdesk: pesquisa de perfil

## Fluxo

1. CLI (`gramdesk-research @perfil`) ou UI (`Ctrl+Shift+F`) pede coleta.
2. App: `research_precheck` (cache, resfriamento, cota) > diálogo de aprovação > `ResearchJob`.
3. `Session.worker_page()` (`web.py`): `GuardedPage` separada no mesmo perfil da conta, mudo, carrega `https://www.instagram.com/robots.txt` só para ter a origem. Usuário segue navegando na view.
4. `run_research(page, rid, username, opts, cb)` chama `window[KEY].research(...)`. Concorrência 1.
5. JS pede `gate` antes de cada requisição; Python decide com `Pacer` e responde `release_research(ok, reason)`.
6. Mídias baixadas uma por vez pela CDN (`session.queue_file`), 0,4-1,2 s entre arquivos, teto 900.

## Endpoints (todos via `origFetch`, `credentials:'include'`, `X-IG-App-ID`)

1. `/api/v1/users/web_profile_info/?username=X`
2. `/api/v1/feed/user/<id>/?count=12[&max_id=]` (fallback: 12 posts do GraphQL do perfil)
3. `/api/v1/media/<pk>/comments/?can_support_threading=true&permalink_enabled=false` (posts mais comentados, 20 por post)
4. `/api/v1/highlights/<id>/highlights_tray/` + `/api/v1/feed/reels_media/?reel_ids=` em lotes de 5

Endpoint novo segue o mesmo molde em `inject.js`: `gate()` antes, `apiJson` com detecção `Blocked`, erro fatal sem retry.

## Fases da ponte (`ResearchJob._on_bridge_event`)

`gate {what}`, `progress {step}`, `profile`, `posts {page,posts,via,cursor,more}`, `comments {pk,comments}`, `highlights`, `done`, `error {message}` ou `{message:'blocked',status,url,text}`.

`_on_gate` > `Pacer.decide` > `QTimer` > `_grant` (`record_request`, log `requisicao`, `release`). Stop hora/dia > `limite-local`. `_on_error`: `classify_block` > `record_block` > encerra; com posts vira `done` parcial.

`normalize_opts`: `posts` 1-300 (60), `commentPosts` 0-25 (15), `comments/highlights/videos` bool.

## Saída

`~/Downloads/Gramdesk/Pesquisas/<perfil>-<AAAAmmdd-HHMMSS>/` (0700, escrita atômica): `status.json`, `profile.json`, `posts.json`, `comments.json`, `highlights.json`, `media/`. URLs assinadas removidas; só caminhos relativos `media/...`. Estados: `collecting`, `downloading`, `done`, `error`, `cancelled`, `refused`.

## Cache e retomada (`research_cache.py`)

- `find_reusable`: pasta `<user>-*` mais recente, estado done/error/cancelled, dentro de `cache_hours`, sem symlink.
- `plan(folder, opts)`: `need_posts`, `cursor`, `feed_done`, `comments_done`, `need_highlights`, `complete`.
- `resume_payload` vai em `opts.resume` para o JS. Retomada usa a mesma pasta e preserva `collected_at`.
- Cache `complete` responde sem rede, mesmo em resfriamento, e não conta pesquisa.

## CLI (`tools/gramdesk_research.py`)

Só biblioteca padrão. Fala com o app pelo socket (`STATUS`, `R {username,rid,account,opts}`), nunca abre o app. `rid` = `secrets.token_hex(8)`. Acompanha `status.json` a cada 2 s (timeout 45 min).

| Código | Significado |
|---|---|
| 0 | ok |
| 1 | erro na coleta ou análise |
| 3 | app fechado/bloqueado/sem login/proto diferente |
| 4 | resfriamento |
| 5 | cota diária |
| 6 | pesquisa já em andamento |

Outros: `--status` (lê `ratelimit.json` com app fechado), `--list`, `--analyze PASTA`, `--contract ARQ`, `--no-analyze`, `--no-preview`, `--model`. Windows usa `Gramdesk.exe --query-status` / `--research`.

## Análise

`compute_stats` > `stats.json` (+ paleta via Pillow se houver). Depois:

```
claude -p <PROMPT> --restricted --tools Read,Write,Glob --strict-mcp-config \
  --permission-mode acceptEdits --output-format json --no-session-persistence [--model M]
```

`cwd=pasta`, timeout 3600 s. Gera `site-brief.json`, `report.md`, `preview.html`, opcional `landing.json`. `validate()` confere chaves do schema, galeria dentro de `media/`, arquivos presentes e **nenhum travessão (U+2014)**. Legendas e comentários são conteúdo não confiável.

## Testes

```
python3 -m pytest tests/test_ratelimit.py tests/test_research_cache.py tests/test_cli_research.py -q
```

`test_cli_research.py`: `FakeApp` AF_UNIX via `GRAMDESK_SOCKET`, `subprocess` trocado por função que levanta erro (prova que a CLI não abre o app), checagem estática da ordem em `app.py`. Pulado no Windows.
