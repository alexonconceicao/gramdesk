"""Perfis isolados, página endurecida e ponte de download por conta."""

from __future__ import annotations

import json
import re
import secrets
from importlib import resources
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWebEngineCore import (
    QWebEngineDownloadRequest,
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView

from .session_cookies import track_session

from . import config, security

HOME = QUrl("https://www.instagram.com/")
_JS_TEMPLATE = resources.files(__package__).joinpath("inject.js").read_text(encoding="utf-8")
_QT_TOKEN = re.compile(r"QtWebEngine/[\d.]+ ")


class Interceptor(QWebEngineUrlRequestInterceptor):
    def __init__(self, owner: "AccountSession"):
        super().__init__(owner)
        self._owner = owner
        self.blocked = 0

    def interceptRequest(self, info):  # noqa: N802 (API do Qt)
        o = self._owner
        reason = security.classify_request(
            info.requestUrl().toString(),
            block_third_party=o.settings.block_third_party,
            block_telemetry=o.settings.block_telemetry,
            ghost=o.account.ghost,
        )
        if reason:
            self.blocked += 1
            info.block(True)


class GuardedPage(QWebEnginePage):
    externalLink = pyqtSignal(QUrl)
    bridgeMessage = pyqtSignal(str)
    bridge_prefix = ""

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):  # noqa: N802
        s = url.toString()
        if url.scheme() in ("about", "data", "blob"):
            return not is_main_frame or url.scheme() == "about"
        # Link externo do Instagram (l.instagram.com/?u=...): abre fora e fica no perfil atual.
        target = security.unwrap_link_shim(s)
        if target:
            if is_main_frame:
                self.externalLink.emit(QUrl(target))
            return False
        if security.is_nav_allowed(s):
            return True
        if not is_main_frame and security.is_captcha(s):
            return True
        if is_main_frame and url.scheme() in ("http", "https"):
            self.externalLink.emit(url)
        return False

    def createWindow(self, _type):  # noqa: N802
        # Pop-ups nunca abrem dentro do app: links do Instagram voltam para a
        # página atual, o resto vai para o navegador do sistema (com aviso).
        catcher = QWebEnginePage(self.profile(), self)

        def route(url: QUrl):
            target = security.unwrap_link_shim(url.toString())
            if target:
                self.externalLink.emit(QUrl(target))
            elif security.is_nav_allowed(url.toString()):
                self.setUrl(url)
            elif url.scheme() in ("http", "https"):
                self.externalLink.emit(url)
            catcher.deleteLater()

        catcher.urlChanged.connect(route)
        return catcher

    def javaScriptConsoleMessage(self, _level, message, _line, _source):  # noqa: N802
        # Não registra logs do site. Só aceita mensagens assinadas pelo script do app.
        if self.bridge_prefix and message.startswith(self.bridge_prefix) and len(message) < 2_000_000:
            self.bridgeMessage.emit(message[len(self.bridge_prefix):])


def _deny_permission(permission):
    permission.deny()


class AccountSession(QObject):
    """Uma conta = um perfil Chromium próprio (cookies, cache e storage separados).

    A página só é criada na primeira vez que a conta é aberta e é congelada
    ou descartada quando o usuário troca para outra conta.
    """

    status = pyqtSignal(str)
    externalLink = pyqtSignal(QUrl)
    downloaded = pyqtSignal(str, str)  # caminho salvo, mensagem

    def __init__(self, account: config.Account, settings: config.Config, download_root: Path, parent=None):
        super().__init__(parent)
        self.account = account
        self.settings = settings
        self.download_root = download_root
        self.profile: QWebEngineProfile | None = None
        self.page: GuardedPage | None = None
        self.view: QWebEngineView | None = None
        self._key = "__gd_" + secrets.token_hex(8)
        self._token = secrets.token_hex(16)
        self._bridge_hits: list[float] = []
        self._pending_downloads: dict[str, dict] = {}
        self._interceptor: Interceptor | None = None
        # Só o fato "existe cookie de sessão do Instagram"; o valor nunca é lido nem guardado.
        self.logged_in = False
        # Cookies "sessionid" presentes, por (domínio, caminho, valor): a rotação do Instagram
        # adiciona o novo antes de remover o antigo, então a remoção não pode zerar o login.
        self._sessions: set[tuple[str, str, bytes]] = set()

    # ---------- ciclo de vida ----------
    @property
    def loaded(self) -> bool:
        return self.view is not None

    @property
    def hibernating(self) -> bool:
        return self.page is not None and self.page.lifecycleState() != QWebEnginePage.LifecycleState.Active

    def ensure_view(self) -> QWebEngineView:
        if self.view is None:
            self._build()
        return self.view

    def _build(self):
        storage, cache = config.profile_dirs(self.account.id)
        config.private_dir(storage)
        config.private_dir(cache)

        # QWebEngineProfileBuilder não é instanciável no PyQt6; os setters
        # funcionam desde que chamados antes de criar qualquer página.
        self.profile = QWebEngineProfile("gd-" + self.account.id, self)
        self.profile.setPersistentStoragePath(str(storage))
        self.profile.setCachePath(str(cache))
        self.profile.setHttpCacheMaximumSize(128 * 1024 * 1024)

        p = self.profile
        p.setHttpUserAgent(_QT_TOKEN.sub("", p.httpUserAgent()))
        p.setSpellCheckEnabled(False)
        self._interceptor = Interceptor(self)
        p.setUrlRequestInterceptor(self._interceptor)
        p.downloadRequested.connect(self._on_download)
        store = p.cookieStore()
        store.cookieAdded.connect(lambda c: self._session_cookie(c, True))
        store.cookieRemoved.connect(lambda c: self._session_cookie(c, False))
        store.loadAllCookies()
        self._install_script()
        self._harden(p.settings())

        self.view = QWebEngineView()
        self.page = GuardedPage(p, self.view)
        self.page.bridge_prefix = "\x00GD:" + self._token + ":"
        self.page.bridgeMessage.connect(self._on_bridge)
        self._harden(self.page.settings())
        self.page.externalLink.connect(self.externalLink)
        if hasattr(self.page, "permissionRequested"):
            self.page.permissionRequested.connect(_deny_permission)
        else:  # Qt < 6.8
            self.page.featurePermissionRequested.connect(
                lambda origin, feature: self.page.setFeaturePermission(
                    origin, feature, QWebEnginePage.PermissionPolicy.PermissionDeniedByUser
                )
            )
        self.page.fileSystemAccessRequested.connect(lambda req: req.reject())
        self.page.fullScreenRequested.connect(lambda req: req.accept())
        self.view.setPage(self.page)
        self.page.loadFinished.connect(lambda _ok: self.apply_zoom())
        self.view.setUrl(HOME)

    def _session_cookie(self, cookie, added: bool):
        self.logged_in = track_session(self._sessions, bytes(cookie.name()), cookie.domain(), cookie.path(), bytes(cookie.value()), added)

    @staticmethod
    def _harden(s: QWebEngineSettings):
        A = QWebEngineSettings.WebAttribute
        off = (
            A.HyperlinkAuditingEnabled,
            A.DnsPrefetchEnabled,
            A.PluginsEnabled,
            A.PdfViewerEnabled,
            A.LocalContentCanAccessRemoteUrls,
            A.LocalContentCanAccessFileUrls,
            A.JavascriptCanOpenWindows,
            A.JavascriptCanAccessClipboard,
            A.AllowRunningInsecureContent,
            A.AllowGeolocationOnInsecureOrigins,
            A.ScreenCaptureEnabled,
            A.NavigateOnDropEnabled,
            A.AllowWindowActivationFromJavaScript,
            A.XSSAuditingEnabled,
        )
        for attr in off:
            s.setAttribute(attr, False)
        s.setAttribute(A.WebRTCPublicInterfacesOnly, True)
        s.setAttribute(A.FullScreenSupportEnabled, True)
        s.setAttribute(A.JavascriptCanPaste, True)

    def _install_script(self):
        scripts = self.profile.scripts()
        for old in scripts.find("gramdesk"):
            scripts.remove(old)
        src = (
            _JS_TEMPLATE.replace("__GD_KEY__", self._key)
            .replace("__GD_GHOST__", "true" if self.account.ghost else "false")
            .replace("__GD_TOKEN__", self._token)
            .replace("__GD_BUTTONS__", "true" if self.settings.feed_buttons else "false")
        )
        sc = QWebEngineScript()
        sc.setName("gramdesk")
        sc.setSourceCode(src)
        sc.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        sc.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        sc.setRunsOnSubFrames(False)
        scripts.insert(sc)

    def activate(self):
        view = self.ensure_view()
        if self.page.lifecycleState() != QWebEnginePage.LifecycleState.Active:
            self.page.setLifecycleState(QWebEnginePage.LifecycleState.Active)
        self.page.setAudioMuted(False)
        self.apply_zoom()
        return view

    def apply_zoom(self):
        if self.page is not None:
            self.page.setZoomFactor(self.account.zoom / 100)

    def hibernate(self):
        if self.page is None or self.view.isVisible():
            return
        if self.page.lifecycleState() != QWebEnginePage.LifecycleState.Active:
            return
        self.page.setAudioMuted(True)
        if self.account.keep_alive:
            return  # segue rodando (DMs, notificações na página), só sem som
        target = (
            QWebEnginePage.LifecycleState.Discarded
            if self.settings.hibernate == "discard"
            else QWebEnginePage.LifecycleState.Frozen
        )
        try:
            self.page.setLifecycleState(target)
        except Exception:  # noqa: BLE001 (Qt recusa em estados transitórios)
            pass

    def reload_scripts(self):
        if self.profile is not None:
            self._install_script()
            self.page.triggerAction(QWebEnginePage.WebAction.Reload)

    def navigate(self, url: QUrl):
        self.ensure_view()
        self.page.setUrl(url)

    # ---------- pesquisa (página oculta, mesma sessão) ----------
    def worker_page(self, on_event, on_ready) -> GuardedPage:
        """Página separada para a pesquisa: o usuário pode navegar à vontade sem interromper a coleta."""
        self.ensure_view()
        page = GuardedPage(self.profile, self)
        self._harden(page.settings())
        page.setAudioMuted(True)
        page.bridge_prefix = self.page.bridge_prefix
        if hasattr(page, "permissionRequested"):
            page.permissionRequested.connect(_deny_permission)

        def bridge(raw: str):
            try:
                msg = json.loads(raw)
            except ValueError:
                return
            if isinstance(msg, dict) and msg.get("type") == "research":
                on_event(str(msg.get("rid", "")), str(msg.get("phase", "")), msg.get("data"))

        page.bridgeMessage.connect(bridge)
        page.loadFinished.connect(on_ready)
        page.setUrl(QUrl("https://www.instagram.com/robots.txt"))
        return page

    def run_research(self, page: GuardedPage, rid: str, username: str, opts: dict, cb):
        args = json.dumps([rid, username, opts])
        page.runJavaScript(
            f"(window[{json.dumps(self._key)}]||null)&&window[{json.dumps(self._key)}].research(...{args})",
            QWebEngineScript.ScriptWorldId.MainWorld,
            cb,
        )

    def cancel_research(self, page: GuardedPage, rid: str):
        page.runJavaScript(
            f"window[{json.dumps(self._key)}]&&window[{json.dumps(self._key)}].cancelResearch({json.dumps(rid)})",
            QWebEngineScript.ScriptWorldId.MainWorld,
        )

    def release_research(self, page: GuardedPage, rid: str, ok: bool, reason: str = ""):
        """Libera (ou nega) a próxima requisição da coleta; o ritmo é decidido pelo app."""
        args = json.dumps([rid, bool(ok), reason])
        page.runJavaScript(
            f"window[{json.dumps(self._key)}]&&window[{json.dumps(self._key)}].release(...{args})",
            QWebEngineScript.ScriptWorldId.MainWorld,
        )

    def release_worker_page(self, page: GuardedPage):
        page.triggerAction(QWebEnginePage.WebAction.Stop)
        QTimer.singleShot(3000, page.deleteLater)  # deixa downloads em andamento se registrarem

    def queue_file(self, url: str, target: Path, name: str, done, page=None):
        self._pending_downloads[QUrl(url).toString()] = {"name": name, "dir": target, "on_done": done}
        (page or self.page).download(QUrl(url), name)

    def _on_bridge(self, raw: str):
        import time

        now = time.monotonic()
        self._bridge_hits = [t for t in self._bridge_hits if now - t < 10] + [now]
        if len(self._bridge_hits) > 20:  # página não pode disparar downloads em massa
            self.status.emit("Muitos pedidos de download seguidos; aguarde alguns segundos.")
            return
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if not isinstance(msg, dict) or msg.get("type") != "download":
            return
        files = msg.get("files") if isinstance(msg.get("files"), list) else []
        self._handle_result(json.dumps({"ok": True, "files": files[:60]}))
        if msg.get("partial") and files:
            self.status.emit("Não deu para saber qual item do carrossel; baixando o post inteiro.")

    def set_ghost(self, enabled: bool):
        self.account.ghost = enabled
        if self.profile is not None:
            self._install_script()
            self.page.triggerAction(QWebEnginePage.WebAction.ReloadAndBypassCache)

    def close(self):
        if self.view is not None:
            self.view.setPage(None)
            self.page.deleteLater()
            self.view.deleteLater()
            self.page = self.view = None
        if self.profile is not None:
            self.profile.deleteLater()
            self.profile = None

    def wipe(self):
        """Apaga cookies e cache já em memória; os arquivos são removidos ao reiniciar."""
        if self.profile is not None:
            self.profile.cookieStore().deleteAllCookies()
            self._sessions.clear()
            self.logged_in = False
            self.profile.clearHttpCache()
            self.profile.clearAllVisitedLinks()

    def clear_cache(self):
        if self.profile is not None:
            self.profile.clearHttpCache()
            self.status.emit("Cache da conta limpo.")

    @property
    def blocked_count(self) -> int:
        return self._interceptor.blocked if self._interceptor else 0

    # ---------- download ----------
    def collect(self, mode: str, x: float | None = None, y: float | None = None):
        if self.page is None or mode not in ("current", "stories"):
            return
        req = security.new_request_id()
        args = json.dumps([req, mode, x, y])
        self.page.runJavaScript(
            f"(window[{json.dumps(self._key)}]||null)&&window[{json.dumps(self._key)}].start(...{args})",
            QWebEngineScript.ScriptWorldId.MainWorld,
            lambda ok: self._poll(req, 0) if ok else self.status.emit("Página ainda carregando, tente de novo."),
        )
        self.status.emit("Procurando mídia...")

    def _poll(self, req: str, tries: int):
        if self.page is None:
            return
        if tries > 100:  # ~20 s
            self.status.emit("Tempo esgotado ao buscar a mídia.")
            return

        def got(raw):
            if raw is None:
                QTimer.singleShot(200, lambda: self._poll(req, tries + 1))
                return
            self._handle_result(raw)

        self.page.runJavaScript(
            f"window[{json.dumps(self._key)}].take({json.dumps(req)})",
            QWebEngineScript.ScriptWorldId.MainWorld,
            got,
        )

    def _handle_result(self, raw):
        try:
            data = json.loads(raw) if isinstance(raw, str) and len(raw) < 2_000_000 else {}
        except ValueError:
            data = {}
        if not data.get("ok"):
            self.status.emit("Falha ao localizar mídia: " + str(data.get("error", "resposta inválida"))[:120])
            return
        files = data.get("files") or []
        target = self._target_dir()
        queued = skipped = 0
        for f in files[:200]:
            if not isinstance(f, dict):
                continue
            url = f.get("url")
            if not security.is_safe_media_url(url):
                continue
            ext = security.media_ext(url)
            name = security.safe_filename(f.get("name", ""), ext)
            if (target / name).exists():
                skipped += 1  # mesmo post/story já baixado
                continue
            thumb = f.get("thumb") if ext == ".mp4" and security.is_safe_media_url(f.get("thumb")) else None
            self._queue(url, {"name": name, "dir": target, "thumb": thumb})
            queued += 1
        if queued:
            extra = f" ({skipped} já baixado(s))" if skipped else ""
            self.status.emit(f"Baixando {queued} arquivo(s){extra}...")
        elif skipped:
            self.downloaded.emit("", "Já baixado antes.")
        else:
            self.status.emit("Nenhuma mídia encontrada aqui. Abra o post ou story e tente de novo.")

    def _queue(self, url: str, job: dict):
        self._pending_downloads[QUrl(url).toString()] = job
        self.page.download(QUrl(url), job["name"])

    def _target_dir(self) -> Path:
        d = self.download_root / security.folder_name(self.account.name)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _on_download(self, req: QWebEngineDownloadRequest):
        # Só aceita downloads pedidos pelo próprio app, de CDN de mídia, com nome saneado.
        url = req.url().toString()
        job = self._pending_downloads.pop(url, None)
        if job is None or not security.is_safe_media_url(url):
            req.cancel()
            return
        target: Path = job["dir"]
        target.mkdir(parents=True, exist_ok=True)
        name = job["name"]
        if (target / name).exists():
            req.cancel()
            if job.get("on_done"):
                job["on_done"](True)
            return
        req.setDownloadDirectory(str(target))
        req.setDownloadFileName(name)
        req.isFinishedChanged.connect(lambda: self._finished(req, job))
        req.accept()

    def _finished(self, req: QWebEngineDownloadRequest, job: dict):
        ok = req.state() == QWebEngineDownloadRequest.DownloadState.DownloadCompleted
        if job.get("on_done"):
            job["on_done"](ok)
            return
        if job.get("is_thumb"):
            if ok:
                self.downloaded.emit(str(job["dir"].parent / job["video"]), "")
            return
        path = job["dir"] / job["name"]
        if not ok:
            self.status.emit(f"Download falhou: {job['name']}")
            return
        if job.get("thumb") and self.page is not None:
            thumb_ext = security.media_ext(job["thumb"]) or ".jpg"
            stem = job["name"].rsplit(".", 1)[0]
            self._queue(job["thumb"], {
                "name": stem + thumb_ext,
                "dir": job["dir"] / ".thumbs",
                "is_thumb": True,
                "video": job["name"],
            })
        self.downloaded.emit(str(path), f"Salvo: {job['name']}")


def open_external(parent, url: QUrl, ask: Callable[[str], bool]):
    if url.scheme() in ("http", "https") and ask(url.toString()):
        QDesktopServices.openUrl(url)
