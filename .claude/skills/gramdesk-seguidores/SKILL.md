---
name: gramdesk-seguidores
description: Tela de Seguidores do Gramdesk: checagem das listas de seguidores/seguindo, não seguidores comuns e famosos (limite configurável), lista permanente de quem deixou de seguir, lote de "deixar de seguir" com ritmo próprio, auditoria, checagem diária automática e CLI gramdesk-follows. Use ao mexer em follows.py, follows_qt.py, follows_ui.py, tools/gramdesk_follows.py, na op `follow` do inject.js, ou ao criar qualquer outra ação de escrita no Instagram.
---

# Gramdesk: seguidores

## Camadas

| Arquivo | Papel |
|---|---|
| `gramdesk/follows.py` | Puro, sem Qt. `FollowStore(root, acc_id)` em `DATA_DIR/follows/<conta>/`: `state.json` (foto completa, foto parcial com cursor, contagens `pk -> [seguidores, quando]`, lista `keep`), `unfollowers.json` (permanente), `batch.json` (lote retomável), `audit.jsonl` (gira em 2 MB para `audit-<data>.jsonl`, sem apagar). Tudo 0600 |
| `gramdesk/follows_qt.py` | `FollowsController`: uma tarefa por vez (`check`, `counts`, `unfollow`) numa worker page da conta; ritmo; bloqueio; checagem diária; comando `F` do socket |
| `gramdesk/follows_ui.py` | `FollowsPage` (`Ctrl+Shift+U`, ícone `users`): abas Não seguidores, Famosos, Sem contagem, Manter, Lote, Deixaram, Auditoria; caixa de seleção por linha (`store.selected`, salva em `state.json`, vale entre abas); barra fixa embaixo (selecionar visíveis, limpar, manter/tirar de manter, deixar de seguir / adicionar ao lote); filtro instantâneo por `setVisible`; linhas atualizadas ao vivo pelo sinal `item(pk, estado)` sem redesenhar; `changed` redesenha preservando a rolagem |
| `gramdesk/inject.js` | `bridge.follow(id, op, arg)` + `take(id)`; ops `me`, `followers`, `following`, `profile`, `unfollow` |
| `gramdesk/web.py` | `Session.follow_call(page, op, arg, cb)`: dispara e faz polling de `take` a cada 300 ms |
| `tools/gramdesk_follows.py` | CLI só leitura: `status`, `check`, `list nao-seguidores|famosos|deixaram [--json]` |

## Fluxos

- **check**: `begin_partial` > páginas de `followers` (50 por página, cursor `next_max_id`) > páginas de `following` > `finish_snapshot`: compara com a foto anterior, acrescenta/atualiza `unfollowers` (`lost_at`, `back_at`, `times`, `i_follow`), audita `deixou-de-seguir`/`voltou-a-seguir`/`checagem`. Primeira checagem não gera perdas. Parou por teto: foto parcial fica salva (até 3 dias) e a próxima continua.
- **counts**: até 120 perfis por rodada, `web_profile_info` por username, contagem vale 30 dias (`COUNT_MAX_AGE`).
- **split(threshold, verified)**: `comuns` e `famosos`; famoso = verificado (selo da lista, `famous_verified`) ou contagem >= limite. Sem ir perfil a perfil. `need_counts` (refinamento opcional) ignora verificados. Também `mutuals()`, `fans()`, `ghost_score(u)` (0-100, heurística local: sem foto `a`, números, padrão bot, teclado, nome).
- **Remover seguidor**: lote com `batch["remove"]` (`extend_batch(..., action="remove")`), op JS `remove_follower` (confere `followed_by:false`), `record_removed` guarda `removed_by_me` para a próxima checagem não marcar "deixou de seguir". Seleção separada `selected_remove` (aba Fãs).
- **Meta**: `read_meta_export(zip|pasta)` > `parse_meta_files` (HTML por links, JSON por `value`/`title`/`href`), `meta_analysis`, `meta_diff`, `save_meta/load_meta(prev)`, `pks_by_username`.
- **Relatório**: `follows_report.py` (puro): `to_csv`, `report_html`, `history_svg`. Histórico em `state.history` ([ts, seguidores, seguindo] por checagem).
- **Lote pela seleção**: `FollowsController.queue(s, pks)` entra no lote em andamento (`extend_batch`), no salvo, ou cria um (`mode="selecao"`) e inicia; se outra tarefa roda, inicia quando ela terminar. `unqueue` tira perfil pendente. `store()` é cache por conta: tela e tarefa usam a mesma instância.
- **Ação do lote (duas vias)**: 1) `gql_unfollow` (`POST /api/graphql`, `doc_id` da mutação `usePolarisUnfollowMutation`, assinada com `fb_dtsg`/`lsd`/`jazoest` lidos de `window.require` ou do HTML) direto na página da conta, sem navegar; 2) se falhar, o mesmo item vai pelo botão (`job["ui_pk"]`). `doc_id` e nome da mutação rotacionam: quando a mutação parar, a via do botão assume. Remover seguidor só tem a via do botão.
- **Ação pela interface**: o Instagram não responde mais às chamadas diretas de unfollow (devolve a página HTML). O lote navega na página da conta (`Session.open_for_action`) e o `inject.js` clica no botão real (`ui_unfollow`, `ui_remove_follower`: `pickByText` por texto em vários idiomas, diálogo `[role=dialog]`, confirma pela mudança do botão). Sem endpoint interno nem token. Falha de um perfil não derruba o lote (`acao-recusada` na auditoria); 3 seguidas pausam.
- **unfollow**: `new_batch(mode, pks)` filtra quem não é seguido e quem está em `keep`; `next_in_batch` pula feitos/erros. Cada remoção: `rate.unfollow_check` > espera `wait` > `follow_call('unfollow')` > `record_unfollow` + `record_unfollowed` + salva lote. Erro comum: registra em `errors`, conta no ritmo; 3 seguidas pausam. Bloqueio: `record_block`, audita, para; lote continua salvo.
- **Checagem diária**: timer de 30 min no app; roda `check` com `auto=True` em conta logada com foto anterior com mais de 20 h (ou parcial pendente), se `follow_check` ligado e nada mais rodando. Avisa por toast se alguém saiu.

## Ritmo

Aviso de limite (429) não para a tarefa: `_slow_down` pausa 5/10/20/40 min, dobra a lentidão e repete a mesma requisição (evento `desacelerar` na auditoria); desiste só após 4 seguidos ou 6 na tarefa. Teto por hora: espera a janela em vez de parar. Contagem começa 3x mais lenta, 60 perfis por rodada. Leitura usa `Pacer` da pesquisa (conta em `items`); remoção usa contadores próprios (skill `gramdesk-ritmo`): 30-60 s, pausa 3-7 min a cada 10, 20/h, 100/dia, teto rígido 200. Exclusão mútua: `precheck` recusa com pesquisa ou navegação ativa; pesquisa e navegação recusam com tarefa de seguidores ativa.

## Regras

- Deixar de seguir só pela janela, com confirmação mostrando quantidade e estimativa. CLI e socket nunca removem (há teste estático para isso).
- CLI `check` roda sem perguntar só se `follow_check` está ligado e já existe checagem manual; senão pede aprovação.
- Auditoria guarda usernames e nomes (é o propósito), nunca URLs, cookies ou tokens.
- A lista `unfollowers.json` nunca é apagada pelo app.
- Endpoints são internos do Instagram Web e mudam sem aviso: resposta sem `friendship_status.following === false` é erro.

## Testes

```bash
python3 -m pytest -q tests/test_follows.py tests/test_follow_bridge.py tests/test_cli_follows.py
```

`test_follow_bridge.py` roda o `inject.js` no node com `fetch` falso (confere URL, método, `X-CSRFToken`, bloqueio 429 e que argumentos inválidos não tocam a rede). O controlador Qt foi validado com sessão falsa dentro do runtime do Flatpak (`flatpak run --command=sh ... QT_QPA_PLATFORM=offscreen python3 script.py`).
