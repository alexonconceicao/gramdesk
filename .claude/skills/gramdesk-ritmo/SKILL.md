---
name: gramdesk-ritmo
description: Política de ritmo (rate limiting) do Gramdesk em gramdesk/ratelimit.py: Limits e faixas, Pacer (intervalo aleatório, pausa longa, tetos por minuto/hora), RateState (ratelimit.json, cota diária, browse), classify_block, resfriamento escalonado, ResearchLog (research.log sanitizado). Use SEMPRE que for criar ou alterar qualquer recurso que faça requisição ao Instagram (pesquisa, navegação, fila, ações em massa), mudar limites padrão, ou adicionar evento de log.
---

# Gramdesk: ritmo e proteção da conta

Módulo puro, sem Qt: `gramdesk/ratelimit.py`. Toda requisição automatizada ao Instagram passa por aqui. Recurso novo que fala com o Instagram **deve** usar `RateState` + `Pacer`/checagem própria, `classify_block`, `record_block` e `ResearchLog`.

## Regras inegociáveis

1. Uma requisição por vez, intervalo aleatório, nunca laço apertado.
2. Checkpoint/login: para na hora, sem nova tentativa, `record_block`, salva o parcial. "Vá mais devagar" (429/limite) em tarefas de seguidores: **não para**; `soft_backoff(n)` espera 5, 10, 20, 40 min (±20%), `record_soft` deixa o ritmo 2x mais lento (até 8x, vale 6 h para as próximas tarefas via `slow_factor`/`Pacer.slow`) e repete a mesma requisição. Só desiste (com `record_block`) depois de 4 avisos seguidos ou 6 na tarefa. Em remoção, `feedback_required`/"restringimos" é bloqueio de ação e para na hora. Pesquisa e navegação ainda param no primeiro aviso.
3. Respeitar resfriamento e cotas antes de começar (precheck) e durante.
4. Ação iniciada por CLI pede aprovação na janela.
5. Log sem cookies, tokens, URLs ou texto do site.

## `Limits` (salvo em `config.json > research_limits`)

`from_dict` ajusta cada valor à faixa em `RANGES`, ignora bool e força coerência (`max_delay >= min_delay+0.5`, `long_max >= long_min`, `per_minute <= per_hour`).

| campo | padrão | faixa |
|---|---|---|
| daily_researches | 3 | 1-10 |
| daily_items | 400 | 30-1000 |
| per_hour / per_minute | 60 / 8 | 10-150 / 2-12 |
| min_delay / max_delay | 2,5 / 6 s | 2-30 / 2,5-60 |
| long_every | 8 | 3-30 |
| long_min / long_max | 20 / 45 s | 5-300 / 5-600 |
| cache_hours | 24 | 1-168 |
| browse_interval / browse_jitter / browse_daily_posts | 8 / 12 / 150 | 5-300 / 0-120 / 1-300 |
| unfollow_min_delay / unfollow_max_delay | 30 / 60 s | 20-600 / 25-900 |
| unfollow_per_hour / unfollow_daily | 20 / 100 | 5-40 / 10-`UNFOLLOW_HARD_CAP` (200) |
| unfollow_long_every | 10 (pausa `UNFOLLOW_LONG` 180-420 s) | 5-30 |
| famous_threshold | 10000 | 1000-100 mi (não é ritmo; mora aqui para reusar `research_limits`) |
| follow_check | 1 | 0-1 (checagem diária automática) |

Campo novo: dataclass + `RANGES` + `from_dict` + controle em `prefs.py` (seção research) + teste.

## `Pacer.decide(now)`

- Intervalo uniforme `min_delay..max_delay`; a cada `long_every` itens soma pausa longa.
- Primeira requisição sem histórico: 0,5-1,5 s.
- Teto por minuto/hora: espera o item mais antigo da janela sair (+ folga).
- Espera > `MAX_WAIT_IN_JOB` (300 s): `stop="hora"` (a pesquisa encerra parcial; seguidores esperam a janela e seguem). Itens do dia esgotados: `stop="dia"`.
- `slow` multiplica intervalo e pausa longa. Contagem de seguidores (`web_profile_info`, endpoint mais vigiado) começa com `COUNTS_SLOW` = 3x.
- Criado por `RateState.pacer()`, herda janela da última hora e saldo do dia.

## `RateState` (`DATA_DIR/ratelimit.json`)

`{"version":1,"accounts":{<id>:{level, cooldown_until, last_block, last_block_at, day, researches, items, recent[], browse_posts, browse_actions, browse_next}}}`. Escrita atômica 0600. `load` tolera corrupção (máx. 50 contas, chave `[A-Za-z0-9_-]{1,64}`, 400 `recent`). `_acc` zera contadores na virada do dia local.

API: `check()`, `status()`, `begin()`, `record_request()`, `record_block(kind)`, `record_success()`, `browse_check/record_browse/browse_touch/browse_status`, `unfollow_check/record_unfollow/unfollow_status`. Browse divide `items` e `recent` com a pesquisa. Remoções têm contadores próprios (`unfollows`, `unfollow_recent`, `unfollow_next`) e também entram em `recent` (teto por hora geral), mas não em `items`.

Contador novo por conta (ex.: ações em massa): adicionar campos em `_acc` com zeragem diária e sanitização no `load`, e expor em `status()` para o snapshot do socket.

## Bloqueio e resfriamento

`classify_block(status, url, text)`: 429 > `rate-limit`; `_CHECKPOINT` > `checkpoint`; `_RATE_TEXT` (inclui `feedback_required`, "Tente novamente mais tarde", "restringimos determinadas atividades") > `rate-limit`; `_LOGIN` ou 401/403 > `login`; senão `None`.

`cooldown_seconds(level)`: 30 min, 1 h, 2 h, 4 h, 8 h, 16 h, 24 h (teto). `record_block` sobe o nível; `record_success` zera (só chamar ao concluir com sucesso; parcial por bloqueio mantém).

## `ResearchLog` (`DATA_DIR/research.log`)

JSON por linha, 0600, gira acima de 256 KB. `clean` descarta chaves com `cookie|token|session|csrf|url|auth|pass`, strings com `http(s)://`, corta em 120, **descarta listas sem aviso**. Campo chamado `profile_url` some. Eventos atuais: `inicio`, `requisicao`, `pausa`, `limite-local`, `bloqueio`, `erro`, `fim`, `recusada`, `nav-acao`, `nav-pausa`, `nav-api`, `seg-inicio`, `seg-remocao`, `seg-fim`. Nomes de usuário vão para a auditoria de seguidores (`follows/<conta>/audit.jsonl`), não para o `research.log`.

## Testes

`tests/test_ratelimit.py`: `NOW` fixo, `random.Random(seed)`, helper `_run` simula tempo. Cobre jitter, pausa longa, tetos, stop hora/dia, clamp, escalada, `classify_block` parametrizado, 0600, corrupção, virada de dia, log limpo. Rode `python3 -m pytest tests/test_ratelimit.py -q`.

## Armadilhas

- Mensagens em português ("resfriamento", "cota diária") são usadas pela CLI para mapear códigos 4/5. Mudar texto quebra código de saída.
- `day_key`/`next_midnight` usam hora local.
- `Pacer.decide` não relê resfriamento no meio da coleta; checagem é no precheck e no bloqueio.
