---
name: gramdesk-downloads
description: Downloads e galeria do Gramdesk: caminho do botão/Ctrl+D (collect, _handle_result, page.download com allowlist), MediaFetch direto da CDN, nomes e pastas por origem, miniaturas em .thumbs, galeria "Meus downloads" (scan, thumbnails, lixeira via portal). Use ao mexer em downloads em web.py, gallery.py, nomes de arquivo ou detecção de "já baixado".
---

# Gramdesk: downloads e galeria

## Caminhos de download

1. **Botão/Ctrl+D/stories** (`web.py`): `collect(mode)` (`current`/`stories`) > JS `start/take` > `_handle_result`. Cada URL passa por `security.is_safe_media_url` (https, porta 443, sem credencial, host em `MEDIA_HOSTS` cdninstagram.com/fbcdn.net, extensão em `MEDIA_EXTS`). `_queue` registra em `_pending_downloads` e chama `page.download`; `_on_download` cancela qualquer download não pedido pelo app. Vídeo baixa a capa em `.thumbs/`.
2. **`MediaFetch`** (browse, fila, pesquisa): `QNetworkAccessManager` com cookies de CDN espelhados, `.<nome>.part` + `os.replace`, 1 GB máx., 20 s sem dados, timeout total (90 s no browse), redirect só para host de mídia, rejeita `text/*`/JSON, confere magic bytes.

## Nomes e pastas

| Origem | Caminho |
|---|---|
| Botão/Ctrl+D | `~/Downloads/Gramdesk/<conta>/<user>_<post\|story>_<code>[_N].ext` |
| Browse e fila | `~/Downloads/Gramdesk/<conta>/<dono>/<dono>_<code>[_NN].ext` |
| Pesquisa | `~/Downloads/Gramdesk/Pesquisas/<perfil>-<data>/media/` |

`safe_filename` (até 120, só extensões permitidas), `folder_name`, `.jpeg` > `.jpg`. Convenções diferentes: item baixado pelo botão não é reconhecido como "já baixado" pelo browse. Dedupe = nome determinístico + `path.exists()`.

## Galeria (`gallery.py`)

- `scan`: `root`, `root/<conta>`, `root/<conta>/<perfil>`; pula `Pesquisas` e pastas com "."; ignora symlink; exige caminho dentro de `root`; ordena por mtime.
- Miniaturas: `.thumbs/<stem>.{jpg,webp,png,heic}`; imagens por `_ThumbJob` em `QThreadPool`; vídeo sem capa por `VideoThumbnailer` (QMediaPlayer + QVideoSink, 6 s). Cache `path|mtime`.
- Lixeira: portal `org.freedesktop.portal.Trash` com fd `O_NOFOLLOW` no Flatpak, `QFile.moveToTrash` fora. `_delete_paths` recusa symlink e caminho fora de `download_root`.
- `schedule_reload` só com a página visível (debounce 400 ms).

## Testes

`tests/test_gallery.py` (só `scan`, `importorskip` PyQt6), `tests/test_security.py` (nomes e URLs).
