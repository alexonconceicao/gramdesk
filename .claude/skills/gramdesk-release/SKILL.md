---
name: gramdesk-release
description: Como testar, versionar e empacotar o Gramdesk: comando certo do pytest (nunca sem argumento na raiz), bump de versão nos 3 lugares, build e instalação do Flatpak local, env de release da doação, build Windows no GitHub Actions (PyInstaller + Inno Setup + selftest) e publicação por tag. Use antes de rodar testes, gerar .flatpak, mudar versão, criar tag ou mexer em packaging/, flatpak/ ou .github/workflows/.
---

# Gramdesk: testes, versão e build

## Testes

```bash
python3 -m pytest -q tests        # o mesmo do CI
```

- **Nunca** `pytest` sem argumento na raiz: a coleta varre `flatpak/build-dir`, `flatpak/repo` e `.flatpak-builder` (GBs) e trava.
- `test_gallery.py` pula sem PyQt6 no Python do sistema. `test_inject.py` e `test_reel_download.py` precisam de `node` (pulam sem).
- Ruff não está instalado nem configurado; lint não faz parte do fluxo.
- CI de testes: `.github/workflows/tests.yml` (ubuntu, Python 3.12, Node 22, só `pytest`).

## Bump de versão (manual, 3 lugares juntos)

1. `pyproject.toml` `version = "X.Y.Z"` (nome do bundle).
2. `gramdesk/config.py` `VERSION = "X.Y.Z"` (app, STATUS do socket, build Windows via `make_version.py`).
3. `data/io.github.alexonconceicao.Gramdesk.metainfo.xml`: `<release version="X.Y.Z" date="AAAA-MM-DD"/>` no topo de `<releases>`.

O bump vai no mesmo commit da feature (`feat(escopo): ...`, em português). Commits seguem a skill `commits-neutros`.

## Flatpak local

Pré-requisitos: `flatpak install --user flathub org.flatpak.Builder org.kde.Sdk//6.11 com.riverbankcomputing.PyQt.BaseApp//6.11`.

```bash
git status                     # o manifesto empacota a árvore de trabalho, inclusive mudanças não commitadas
./build-flatpak.sh             # gera gramdesk-X.Y.Z.flatpak na raiz
flatpak install --user -y gramdesk-X.Y.Z.flatpak
flatpak run io.github.alexonconceicao.Gramdesk
flatpak list --app | grep -i gramdesk
```

- O script carrega `$GRAMDESK_RELEASE_ENV` ou `~/.config/gramdesk-build/release.env` (`GRAMDESK_DONATION_URL`, `GRAMDESK_DONATION_LIQUID`, opcionais `..._TITLE`, `..._TEXT`, `..._INTERVAL_DAYS`, `..._DELAY_DAYS`, `..._LIQUID_ASSETS`), roda `tools/make_build_info.py` (gera `gramdesk/_build_info.json`, apagado por `trap`) e chama o builder com `--disable-rofiles-fuse` (não remover).
- Nunca imprimir nem commitar valores do env, `_build_info.json` ou `packaging/windows/version.txt`.
- Manifesto `flatpak/io.github.alexonconceicao.Gramdesk.yml`: instala `gramdesk/*.py *.js *.svg`; permissões só rede, wayland/x11, áudio, dri, bandeja e `xdg-download/Gramdesk:create`. Mudou permissão: atualizar `SECURITY.md`.
- Arquivo de dados novo (`.js`/`.svg`): adicionar em `packaging/gramdesk.spec` (`datas`), `pyproject.toml` (`package-data`) e globs do manifesto.
- Arquivo `.py` novo em `gramdesk/` entra sozinho (glob), mas confira o spec do PyInstaller se for importado dinamicamente.

## Windows (GitHub Actions)

`.github/workflows/windows.yml`, dispara em tag `v*` ou `workflow_dispatch`: pytest > `make_version.py` > `make_build_info.py` (secrets) > `pyinstaller packaging/gramdesk.spec` > `Gramdesk.exe --selftest` offscreen > zip portátil (`portable.txt`) > Inno Setup (`packaging/windows/gramdesk.iss`, AppId fixo, **não mudar**) > `SHA256SUMS-windows.txt` > upload na release.

Publicar: bump + commit, `git tag vX.Y.Z && git push origin vX.Y.Z`. A tag precisa bater com `config.VERSION`. Perguntar ao usuário antes de criar tag ou dar push.
