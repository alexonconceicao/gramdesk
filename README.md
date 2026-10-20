# Gramdesk

Cliente desktop leve para o Instagram no Linux e no Windows, no estilo do ZapZap, com motor Chromium (Qt WebEngine): várias contas lado a lado, cada uma isolada, sem telemetria e com ferramentas de privacidade e download.

> Projeto independente, sem vínculo com a Meta ou o Instagram. Usa o Instagram Web oficial dentro de um navegador embutido (Qt WebEngine); não usa API privada nem pede sua senha fora da página do próprio Instagram.

## Recursos

- **Multi-conta isolada**: cada conta tem seu próprio perfil Chromium (cookies, cache, localStorage e processos separados). Uma conta não enxerga a sessão da outra.
- **Hibernação**: só a conta aberta consome CPU/RAM. Ao trocar, a anterior é descartada (padrão, libera memória) ou congelada, e volta ao ser aberta. Contas nunca abertas na sessão nem chegam a carregar.
- **Modo fantasma** (por conta, `Ctrl+G`): stories abertos não marcam visualização. O app descarta as requisições de "visto" do story (REST e GraphQL) antes de saírem.
- **Botão de baixar em cada mídia**: no feed, perfil, reels e stories aparece um botão sobre cada foto e vídeo. Em carrossel, o botão baixa só o slide visível e o *Todas (N)* baixa o post inteiro. Some quando um pop-up do site cobre a mídia. Desligável em Preferências > Downloads.
- **Download pelo cabeçalho** (`Ctrl+D` ou botão direito > *Baixar esta mídia*): foto, vídeo (com áudio), carrossel, reels e story atual, sempre na maior resolução disponível.
- **Abrir link ou @perfil** (`Ctrl+K`): cole um link do Instagram ou um @perfil na barra do cabeçalho e ele abre na conta selecionada. Ao voltar para o app com um link do Instagram copiado, aparece o atalho *Abrir em <conta>*. `gramdesk <link>` entrega o link para a janela já aberta.
- **Baixar todos os stories** do perfil ou destaque aberto (`Ctrl+Shift+D`).
- **Bloqueio por senha** (`Ctrl+L`), com scrypt e atraso progressivo após erros.
- **Temas**: Sistema, Claro ou Escuro, com 4 cores de destaque (Instagram, Violeta, Oceano, Esmeralda). O Instagram acompanha o tema do app.
- **Preferências** (`Ctrl+,`): Aparência, Contas, Privacidade e segurança, Downloads, Atalhos e Sobre.
- **Configurações por conta**: nome, cor, modo fantasma, manter ativa em segundo plano, zoom próprio, ordem na barra lateral, limpar cache e remover.
- **Acesso rápido à conta atual**: clique no nome da conta no cabeçalho, no avatar da conta ativa, no ícone de usuário ou `Ctrl+Shift+,`.
- **Bloqueio por inatividade** (5 a 60 min) quando há senha definida.
- Atalhos: `Ctrl+1..9` troca de conta, `Ctrl+R` recarrega, `Alt+←/→` navega.

- **Meus downloads** (`Ctrl+J`, ícone de grade na barra lateral ou no cabeçalho): galeria com tudo que já foi baixado, filtro por conta, fotos/vídeos e busca. Visualizador com setas, player de vídeo embutido, copiar imagem, abrir no app padrão e mover para a lixeira. Vídeos ganham capa automática. Itens já baixados não são duplicados.

Downloads ficam em `~/Downloads/Gramdesk/<conta>/`.

## Pesquisa de perfil + análise com Claude

Coleta somente leitura de um perfil (bio, links, posts, legendas, hashtags, engajamento, comentários dos posts mais comentados, destaques e imagens), usando a conta aberta, no ritmo de uma pessoa navegando. Abre em `Ctrl+Shift+F`, no ícone de lupa do cabeçalho ou pela CLI:

```bash
ln -s "$PWD/tools/gramdesk_research.py" ~/.local/bin/gramdesk-research
gramdesk-research @perfil --posts 60            # coleta (aprovação na janela) + análise
gramdesk-research --analyze ~/Downloads/Gramdesk/Pesquisas/perfil-AAAAMMDD-HHMMSS
gramdesk-research --list
```

Saída em `~/Downloads/Gramdesk/Pesquisas/<perfil>-<data>/`:

| Arquivo | Conteúdo |
|---|---|
| `profile.json`, `posts.json`, `comments.json`, `highlights.json`, `media/` | coleta |
| `stats.json` | estatísticas calculadas por script (frequência, engajamento, hashtags, melhores posts, paleta de cores) |
| `site-brief.json` | briefing para site: identidade, público, voz, ofertas, agenda, locais, depoimentos, mídia, marca, plano de seções, SEO, FAQ e lacunas, tudo com evidências |
| `landing.json` (opcional) | com `--contract ARQUIVO`, um JSON no formato que você descrever, pronto para o importador do seu site |
| `report.md`, `preview.html` | relatório e prévia estática da página |

Segurança:
- Pedidos vindos da linha de comando sempre pedem aprovação na janela.
- A coleta roda numa página oculta da mesma sessão, com pausas aleatórias, e para se o Instagram pedir para ir mais devagar.
- A análise usa o Claude Code em `--restricted` (sem terminal, sem rede, arquivos só da pasta). Legendas e comentários são tratados como conteúdo não confiável.

## Instalação

### Flatpak (recomendado)

Baixe `gramdesk-<versão>.flatpak` na página de [Releases](https://github.com/alexonconceicao/gramdesk/releases) e instale:

```bash
flatpak install --user gramdesk-0.7.1.flatpak
flatpak run io.github.alexonconceicao.Gramdesk
```

O runtime KDE 6.11 do Flathub é baixado automaticamente. O Flatpak é a forma recomendada porque o Qt WebEngine dele inclui os codecs H.264/AAC usados nos vídeos do Instagram, e o sandbox só dá acesso a `~/Downloads/Gramdesk`.

### Windows

Na página de [Releases](https://github.com/alexonconceicao/gramdesk/releases):

- `Gramdesk-Setup-<versão>.exe`: instalador, só para o seu usuário (não pede administrador). Dados em `%APPDATA%\Gramdesk` e `%LOCALAPPDATA%\Gramdesk`.
- `Gramdesk-<versão>-windows-portable.zip`: versão portátil. Descompacte e rode `Gramdesk.exe`; com o arquivo `portable.txt` presente, tudo fica na pasta `data` ao lado do executável (bom para pendrive).
- `SHA256SUMS-windows.txt`: hashes para conferir os arquivos.

O executável não é assinado digitalmente, então o Windows SmartScreen pode avisar na primeira execução ("Mais informações" > "Executar assim mesmo"). Os builds saem do GitHub Actions a partir deste código ([workflow](.github/workflows/windows.yml)), com autoteste do executável antes da publicação.

Limitação: o Qt WebEngine distribuído pelo PyPI (usado no build Windows) não traz os codecs H.264/AAC, então alguns vídeos do feed podem não tocar ou ficar sem som dentro do app. Downloads funcionam normalmente, e o player da galeria (Meus downloads) usa outro decodificador (QtMultimedia), que lê H.264.

### Do código (pip)

```bash
git clone https://github.com/alexonconceicao/gramdesk && cd gramdesk
python3 -m venv .venv && .venv/bin/pip install .
.venv/bin/gramdesk
```

Atenção: o PyQt6-WebEngine do PyPI vem **sem** H.264/AAC, então vídeos podem não tocar ou ficar sem som. Para uso diário, prefira o Flatpak.

### Gerar o Flatpak localmente

```bash
flatpak install --user flathub org.flatpak.Builder org.kde.Sdk//6.11 com.riverbankcomputing.PyQt.BaseApp//6.11
./build-flatpak.sh
```

## Segurança e privacidade

Resumo do que o app faz para proteger o usuário (detalhes em [SECURITY.md](SECURITY.md)):

| Controle | Como |
|---|---|
| Sem telemetria do app | Nenhum servidor próprio, nenhuma estatística, nenhuma atualização automática. O app só fala com o Instagram. |
| Isolamento entre contas | Um `QWebEngineProfile` persistente por conta, em diretórios `0700`. |
| Bloqueio de terceiros | Só hosts do Instagram/Meta e CDNs de mídia carregam; o resto é bloqueado (reCAPTCHA liberado só no caminho `/recaptcha/`). |
| Telemetria do Instagram | Endpoints de log conhecidos bloqueados (opcional). |
| Só HTTPS | `http://` e outros esquemas de rede são bloqueados. |
| Navegação restrita | Links externos não abrem dentro do app; pedem confirmação e vão para o navegador do sistema. Pop-ups bloqueados. |
| Permissões negadas | Câmera, microfone, localização, notificações, leitura de clipboard e acesso a arquivos são negados. |
| Vazamento de IP local | WebRTC limitado a interfaces públicas; DNS prefetch e hyperlink auditing desligados. |
| Downloads | Só aceita downloads pedidos pelo próprio app, de CDN de mídia do Instagram, extensões de mídia, nome saneado (sem path traversal). |
| Arquivos locais | `umask 077`; configuração gravada de forma atômica com `0600` e validada na leitura. |
| Remoção de conta | Apaga cookies e cache na hora e remove o perfil do disco na próxima inicialização. |

## Limitações honestas

- **Termos de uso**: baixar conteúdo e ocultar visualizações não é permitido pelos termos do Instagram. Use por sua conta e risco e respeite os direitos de quem publicou. O app não faz scraping em massa nem automação.
- O modo fantasma e os downloads dependem de detalhes internos do Instagram Web, que mudam sem aviso. Se pararem de funcionar, abra uma issue.
- A senha de bloqueio impede uso casual da interface, mas **não criptografa** os dados do perfil no disco. Para isso, use criptografia de disco (LUKS) ou de home.
- Mensagens diretas (DM) não têm "ocultar visto".

## Licença

MIT
