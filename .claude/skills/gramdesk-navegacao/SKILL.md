---
name: gramdesk-navegacao
description: Navegação passo a passo do Gramdesk (gramdesk-browse) e fila de download do perfil: regras puras em browse.py (target_of, parse_grid, parse_post, pick_source, classify_signals), BrowseController/_Ctx em browse_qt.py (aprovação, ritmo, snapshot, bloqueio, download), protocolo B no socket com códigos 0/1/3/4/5/6/7, profile_queue.py + queue_qt.py. Use ao mexer nesses arquivos, em tools/gramdesk_browse.py, ou ao criar ação nova que navega pela janela como uma pessoa.
---

# Gramdesk: navegação passo a passo e fila do perfil

Princípio: o app navega na **própria janela da conta**, uma ação por chamada, lendo o DOM e os dados que a página já recebeu. Sem laço automático na CLI.

## Regras puras: `gramdesk/browse.py` (sem Qt, sem rede)

- `ACTIONS`, `NAV_ACTIONS`.
- `target_of(url)`: aceita perfil (`/u/`, `/u/reels/`, `/u/tagged/`) e post/reel; normaliza `/reels/X` > `/reel/X/`, `/u/p/X` > `/p/X/`; recusa `RESERVED`.
- `parse_html` (HTMLParser próprio, teto 60k tags), `best_srcset`, `media_of`.
- `parse_grid`: dedupe por code, filtro por dono, tipo por aria-label/title, fixado, teto 300. `parse_avatar`.
- `parse_post`: ignora mídia dentro de link para outro post, capa que repete poster; reel = 1 vídeo; usa `index` só se `index.code == code`.
- `index_sources` (só CDN segura; vídeo exige `.mp4`), `pick_source`, `pick_file`, `classify_signals`, `file_name`.

## Cola Qt: `gramdesk/browse_qt.py`

Códigos: `OK=0, ERROR=1, APP=3, COOLDOWN=4, QUOTA=5, BUSY=6, REFUSED=7`.

`BrowseController.handle(req, reply)` valida em ordem: id/action, janela desbloqueada, nada em andamento (nem pesquisa), sessão logada, URL via `target_of`, `index` 1-50, `list-grid` só em perfil, `post_open`, `rate.browse_check`. Guarda `_Ctx` em `self.active` (PyQt só guarda referência fraca de método).

`_Ctx`:
1. `approve`: confirmação na primeira ação de cada conta por sessão; recusa = 7.
2. `pace`: espera o restante do intervalo com toast.
3. `run`: `record_browse` (1 item, +1 post se `post_open`). `open-url` = load + espera 2-3,5 s + snapshot. `list-grid --scroll` = `scrollOnce` + 2,5-4 s.
4. `snapshot`: repete a cada 1 s por até 15 s; cada leitura passa por `blocked()`.
5. `blocked`: `classify_signals` ou sessão caída > `record_block` > código 4.
6. `download`: `pick_source` (zero rede) > fallback `s.current_files` (no máximo 1 chamada de API, log `nav-api`). Vídeo só `.mp4`, nunca `blob:`.
7. `fetch`: `download_root/<conta>/<dono>/<dono>_<code>[_NN].ext`; existente = "Já baixado antes." sem rede; senão `MediaFetch` (GET único, `.part`, magic bytes, 90 s).
8. `finish`: uma única vez; `browse_touch` (intervalo conta do fim da ação), exceto código 7.

`reply` é chamado exatamente uma vez; todo callback confere `self.done`.

## Ritmo atual

`browse_check`: resfriamento > `daily_items` > `browse_daily_posts` (só `post_open`) > `per_hour`; `per_minute` só aumenta espera. Padrões: 8 s + 0 a 12 s entre ações, 150 posts/dia (até 300), 400 itens/dia. Limites em `Limits` (skill `gramdesk-ritmo`). `handle` também recusa (6) com tarefa de seguidores em andamento.

## CLI `tools/gramdesk_browse.py`

`status | open @perfil | grid [--scroll] | inspect URL | download URL [--index N] [--out PASTA]`. Só biblioteca padrão. `evaluate` faz precheck (3/4/5/6) antes de mandar `B {json}`; resposta `{proto:1,id,ok,code,message,data,waited}`; timeout 900 s. Nunca abre o app.

## Fila do perfil

- `profile_queue.py` (puro): estado em `DATA_DIR/queues/<conta>-<perfil>.json` (0600). `load` sanitiza, `merge` mantém ordem da grade, `_on_disk` marca já baixados, `next_task` > `(code,url,index)`, `apply(code)`: OK > next, BUSY > retry, ERROR > registra e segue, 3/4/5/7 > pause. Filtro `only` (`video`/`image`).
- `queue_qt.py`: `ProfileQueueRunner` chama `win.browse.handle` direto (sem socket, mesmos limites). Clique no botão vale como aprovação. Grade: rola até 2 rodadas sem crescer ou 60 rolagens. `QueueBar` pausar/retomar/cancelar.

Ação em lote nova (ex.: deixar de seguir) deve seguir esse desenho: estado puro persistido e retomável + runner Qt que passa por um controlador com aprovação, ritmo e parada no bloqueio.

## Testes

- `tests/test_browse.py`: fixtures HTML anotadas com `data-gd-*`, `RateState` com `NOW` fixo.
- `tests/test_cli_browse.py`: `FakeApp` AF_UNIX, `subprocess` proibido, checagem estática de um único `talk(b"B "` e nenhum laço em `request`.
- `tests/test_profile_queue.py`, `tests/test_reel_download.py` (node).
