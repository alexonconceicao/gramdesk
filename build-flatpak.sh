#!/usr/bin/env bash
# Gera o bundle .flatpak instalável (requer org.flatpak.Builder, org.kde.Sdk//6.11
# e com.riverbankcomputing.PyQt.BaseApp//6.11 do Flathub).
set -euo pipefail
cd "$(dirname "$0")"
APP=io.github.alexonconceicao.Gramdesk
VERSION=$(grep -m1 '^version' pyproject.toml | cut -d'"' -f2)
# Configuração de release (doação etc.) mora fora do repositório.
RELEASE_ENV="${GRAMDESK_RELEASE_ENV:-$HOME/.config/gramdesk-build/release.env}"
if [ -f "$RELEASE_ENV" ]; then set -a; . "$RELEASE_ENV"; set +a; fi
python3 tools/make_build_info.py
trap 'rm -f gramdesk/_build_info.json' EXIT
flatpak run org.flatpak.Builder --force-clean --disable-rofiles-fuse --user --install-deps-from=flathub \
  --repo=flatpak/repo flatpak/build-dir flatpak/$APP.yml
flatpak build-bundle --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo \
  flatpak/repo "gramdesk-$VERSION.flatpak" "$APP"
echo "OK: gramdesk-$VERSION.flatpak"
