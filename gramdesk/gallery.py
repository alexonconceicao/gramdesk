"""Galeria dos itens baixados: grade com miniaturas, filtros e visualizador com player."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import (
    QObject,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QDesktopServices,
    QIcon,
    QImage,
    QImageReader,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import security, ui

VIDEO_EXTS = {".mp4"}
IMAGE_EXTS = security.MEDIA_EXTS - VIDEO_EXTS - {".m4a"}
TILE = 200
THUMB_DIR = ".thumbs"


@dataclass
class Entry:
    path: Path
    folder: str
    mtime: float
    size: int

    @property
    def video(self) -> bool:
        return self.path.suffix.lower() in VIDEO_EXTS

    @property
    def thumb(self) -> Path:
        return thumb_for(self.path)


def thumb_for(path: Path) -> Path:
    base = path.parent / THUMB_DIR
    for ext in (".jpg", ".webp", ".png", ".heic"):
        cand = base / (path.stem + ext)
        if cand.exists() and not cand.is_symlink():
            return cand
    return base / (path.stem + ".jpg")


def scan(root: Path) -> list[Entry]:
    """Lista mídias em root/<conta>/ sem seguir links simbólicos nem sair da pasta."""
    out: list[Entry] = []
    try:
        root_real = root.resolve(strict=True)
    except OSError:
        return out
    dirs = [root]
    try:
        dirs += [d for d in root.iterdir() if d.is_dir() and not d.is_symlink() and not d.name.startswith(".")]
    except OSError:
        return out
    for d in dirs:
        try:
            files = list(d.iterdir())
        except OSError:
            continue
        for f in files:
            if f.is_symlink() or not f.is_file() or f.suffix.lower() not in security.MEDIA_EXTS:
                continue
            try:
                if not f.resolve().is_relative_to(root_real):
                    continue
                st = f.stat()
            except OSError:
                continue
            out.append(Entry(f, "" if d == root else d.name, st.st_mtime, st.st_size))
    out.sort(key=lambda e: e.mtime, reverse=True)
    return out


def trash(path: Path) -> bool:
    """Manda para a lixeira (portal no Flatpak, QFile fora dele)."""
    if os.environ.get("FLATPAK_ID"):
        try:
            from PyQt6.QtDBus import QDBusConnection, QDBusInterface, QDBusUnixFileDescriptor

            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                iface = QDBusInterface(
                    "org.freedesktop.portal.Desktop",
                    "/org/freedesktop/portal/desktop",
                    "org.freedesktop.portal.Trash",
                    QDBusConnection.sessionBus(),
                )
                reply = iface.call("TrashFile", QDBusUnixFileDescriptor(fd))
                ok = bool(reply.arguments()) and reply.arguments()[0] == 1
            finally:
                os.close(fd)
            if ok:
                return True
        except Exception:  # noqa: BLE001
            pass
    from PyQt6.QtCore import QFile

    ok, _where = QFile.moveToTrash(str(path))
    return bool(ok)


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def _label_for(e: Entry) -> str:
    stem = e.path.stem
    user = stem.split("_post_")[0].split("_story_")[0]
    return f"{user}\n{time.strftime('%d/%m/%Y %H:%M', time.localtime(e.mtime))}"


# ---------- miniaturas em segundo plano ----------
class _ThumbSignals(QObject):
    done = pyqtSignal(str, QImage)


class _ThumbJob(QRunnable):
    def __init__(self, key: str, path: Path, side: int, signals: _ThumbSignals):
        super().__init__()
        self.key, self.path, self.side, self.signals = key, path, side, signals

    def run(self):
        img = QImage()
        if self.path.exists():
            reader = QImageReader(str(self.path))
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid() and size.width() > 0 and size.height() > 0:
                scale = max(self.side / size.width(), self.side / size.height())
                reader.setScaledSize(QSize(max(1, round(size.width() * scale)), max(1, round(size.height() * scale))))
            img = reader.read()
            if not img.isNull():
                x = max(0, (img.width() - self.side) // 2)
                y = max(0, (img.height() - self.side) // 2)
                img = img.copy(x, y, min(self.side, img.width()), min(self.side, img.height()))
        self.signals.done.emit(self.key, img)


def _tile(img: QImage | None, video: bool, dpr: float) -> QIcon:
    side = int(TILE * dpr)
    pm = QPixmap(side, side)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, side, side), 16 * dpr, 16 * dpr)
    p.setClipPath(path)
    if img is not None and not img.isNull():
        p.drawImage(QRectF(0, 0, side, side), img)
    else:
        p.fillRect(pm.rect(), QColor(ui.SURFACE3))
        ui.icon("play" if video else "image", ui.MUTED, 40).paint(p, int(side / 2 - 28 * dpr), int(side / 2 - 28 * dpr), int(56 * dpr), int(56 * dpr))
    if video:
        r = QRectF(side - 44 * dpr, 10 * dpr, 34 * dpr, 34 * dpr)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawEllipse(r)
        ui.icon("play", "#ffffff", 18).paint(p, r.adjusted(8 * dpr, 8 * dpr, -8 * dpr, -8 * dpr).toRect())
    p.end()
    pm.setDevicePixelRatio(dpr)
    return QIcon(pm)


class VideoThumbnailer(QObject):
    """Gera capa para vídeos sem miniatura: abre o vídeo mudo e salva o primeiro quadro."""

    done = pyqtSignal(str, QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: list[tuple[str, Path, Path, int]] = []
        self._busy = False
        self._player = None
        self._job = None
        self._timer = QTimer(self, singleShot=True, interval=6000, timeout=self._give_up)

    def add(self, key: str, video: Path, thumb: Path, side: int):
        self._queue.append((key, video, thumb, side))
        if not self._busy:
            self._next()

    def _next(self):
        if not self._queue:
            self._busy = False
            if self._player is not None:
                self._player.setSource(QUrl())
            return
        self._busy = True
        self._job = self._queue.pop(0)
        if self._player is None:
            from PyQt6.QtMultimedia import QMediaPlayer, QVideoSink

            self._player = QMediaPlayer(self)
            self._sink = QVideoSink(self)
            self._player.setVideoSink(self._sink)
            self._sink.videoFrameChanged.connect(self._frame)
            self._player.errorOccurred.connect(lambda *_: self._give_up())
        self._timer.start()
        self._player.setSource(QUrl.fromLocalFile(str(self._job[1])))
        self._player.play()

    def _frame(self, frame):
        if self._job is None or not frame.isValid():
            return
        img = frame.toImage()
        if img.isNull():
            return
        key, _video, thumb, side = self._job
        self._job = None
        self._timer.stop()
        self._player.stop()
        try:
            thumb.parent.mkdir(parents=True, exist_ok=True)
            img.scaled(640, 640, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation).save(
                str(thumb), "JPG", 85
            )
        except OSError:
            pass
        scaled = img.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        x, y = (scaled.width() - side) // 2, (scaled.height() - side) // 2
        self.done.emit(key, scaled.copy(x, y, side, side))
        QTimer.singleShot(0, self._next)

    def _give_up(self):
        if self._job is not None:
            key = self._job[0]
            self._job = None
            if self._player is not None:
                self._player.stop()
            self.done.emit(key, QImage())
        QTimer.singleShot(0, self._next)


# ---------- visualizador ----------
class _ImageView(QWidget):
    def __init__(self):
        super().__init__()
        self.pm = QPixmap()

    def set_image(self, path: Path):
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        self.pm = QPixmap.fromImage(reader.read())
        self.update()

    def paintEvent(self, _):  # noqa: N802
        if self.pm.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        s = self.pm.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        if s.width() > self.pm.width() and s.height() > self.pm.height():
            s = self.pm.size()  # não amplia além do original
        x, y = (self.width() - s.width()) // 2, (self.height() - s.height()) // 2
        p.drawPixmap(x, y, s.width(), s.height(), self.pm)


class Viewer(QWidget):
    closed = pyqtSignal()
    deleted = pyqtSignal(Path)

    def __init__(self, host: QWidget):
        super().__init__(host)
        self.entries: list[Entry] = []
        self.index = 0
        self._player = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(6)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.name = QLabel()
        self.name.setObjectName("viewerTitle")
        self.info = QLabel()
        self.info.setObjectName("viewerInfo")
        col.addWidget(self.name)
        col.addWidget(self.info)
        top.addLayout(col, 1)
        for icon_name, tip, fn in (
            ("copy", "Copiar imagem", self._copy),
            ("external", "Abrir no app padrão", self._open_external),
            ("folder", "Abrir pasta", self._open_folder),
            ("trash", "Mover para a lixeira (Delete)", self._delete),
            ("close", "Fechar (Esc)", self.close_viewer),
        ):
            b = QPushButton()
            b.setObjectName("viewerBtn")
            b.setIcon(ui.icon(icon_name, "#ffffff", 20))
            b.setIconSize(QSize(20, 20))
            b.setFixedSize(40, 40)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(fn)
            top.addWidget(b)
            if icon_name == "copy":
                self.copy_btn = b
        v.addLayout(top)

        mid = QHBoxLayout()
        self.prev_btn = self._nav_button("back", lambda: self.step(-1))
        self.next_btn = self._nav_button("forward", lambda: self.step(1))
        self.pages = QStackedWidget()
        self.image = _ImageView()
        self.pages.addWidget(self.image)
        self.video_box = QWidget()
        vb = QVBoxLayout(self.video_box)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(8)
        from PyQt6.QtMultimediaWidgets import QVideoWidget

        self.video = QVideoWidget()
        self.video.setStyleSheet("background: black;")
        vb.addWidget(self.video, 1)
        ctl = QHBoxLayout()
        self.play_btn = QPushButton()
        self.play_btn.setObjectName("viewerBtn")
        self.play_btn.setFixedSize(40, 40)
        self.play_btn.clicked.connect(self._toggle_play)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.sliderMoved.connect(lambda v: self._player and self._player.setPosition(v))
        self.time = QLabel("0:00")
        self.time.setObjectName("viewerInfo")
        self.mute_btn = QPushButton()
        self.mute_btn.setObjectName("viewerBtn")
        self.mute_btn.setFixedSize(40, 40)
        self.mute_btn.clicked.connect(self._toggle_mute)
        ctl.addWidget(self.play_btn)
        ctl.addWidget(self.seek, 1)
        ctl.addWidget(self.time)
        ctl.addWidget(self.mute_btn)
        vb.addLayout(ctl)
        self.pages.addWidget(self.video_box)
        mid.addWidget(self.prev_btn)
        mid.addWidget(self.pages, 1)
        mid.addWidget(self.next_btn)
        v.addLayout(mid, 1)
        self.hide()

    def _nav_button(self, icon_name, fn):
        b = QPushButton()
        b.setObjectName("viewerNav")
        b.setIcon(ui.icon(icon_name, "#ffffff", 26))
        b.setIconSize(QSize(26, 26))
        b.setFixedSize(52, 52)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(fn)
        return b

    def paintEvent(self, _):  # noqa: N802
        QPainter(self).fillRect(self.rect(), QColor(8, 8, 12))

    def open(self, entries: list[Entry], index: int):
        self.entries, self.index = entries, index
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.setFocus()
        self._show_current()

    def close_viewer(self):
        self._stop()
        self.hide()
        self.closed.emit()

    def step(self, delta: int):
        if not self.entries:
            return
        self.index = (self.index + delta) % len(self.entries)
        self._show_current()

    def _current(self) -> Entry | None:
        return self.entries[self.index] if 0 <= self.index < len(self.entries) else None

    def _show_current(self):
        e = self._current()
        if e is None:
            self.close_viewer()
            return
        self._stop()
        self.name.setText(e.path.name)
        when = time.strftime("%d/%m/%Y %H:%M", time.localtime(e.mtime))
        self.info.setText(f"{e.folder or 'Downloads'}  ·  {when}  ·  {_human_size(e.size)}  ·  {self.index + 1} de {len(self.entries)}")
        multi = len(self.entries) > 1
        self.prev_btn.setVisible(multi)
        self.next_btn.setVisible(multi)
        self.copy_btn.setVisible(not e.video)
        if e.video:
            self.pages.setCurrentWidget(self.video_box)
            self._play(e.path)
        else:
            self.pages.setCurrentWidget(self.image)
            self.image.set_image(e.path)

    def _play(self, path: Path):
        from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer

        if self._player is None:
            self._player = QMediaPlayer(self)
            self._audio = QAudioOutput(self)
            self._player.setAudioOutput(self._audio)
            self._player.setVideoOutput(self.video)
            self._player.durationChanged.connect(lambda d: self.seek.setRange(0, d))
            self._player.positionChanged.connect(self._on_position)
            self._player.playbackStateChanged.connect(lambda _s: self._sync_controls())
            self._player.setLoops(QMediaPlayer.Loops.Infinite)
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._player.play()
        self._sync_controls()

    def _on_position(self, pos: int):
        if not self.seek.isSliderDown():
            self.seek.setValue(pos)
        dur = self._player.duration() if self._player else 0
        fmt = lambda ms: f"{ms // 60000}:{(ms // 1000) % 60:02d}"
        self.time.setText(f"{fmt(pos)} / {fmt(dur)}")

    def _sync_controls(self):
        from PyQt6.QtMultimedia import QMediaPlayer

        playing = self._player is not None and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setIcon(ui.icon("pause" if playing else "play", "#ffffff", 20))
        muted = self._player is not None and self._audio.isMuted()
        self.mute_btn.setIcon(ui.icon("mute" if muted else "volume", "#ffffff", 20))

    def _toggle_play(self):
        from PyQt6.QtMultimedia import QMediaPlayer

        if not self._player:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _toggle_mute(self):
        if self._player:
            self._audio.setMuted(not self._audio.isMuted())
            self._sync_controls()

    def _stop(self):
        if self._player is not None:
            self._player.stop()
            self._player.setSource(QUrl())

    def _copy(self):
        e = self._current()
        if e and not e.video and not self.image.pm.isNull():
            QApplication.clipboard().setPixmap(self.image.pm)

    def _open_external(self):
        e = self._current()
        if e:
            self._stop()
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(e.path)))

    def _open_folder(self):
        e = self._current()
        if e:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(e.path.parent)))

    def _delete(self):
        e = self._current()
        if not e:
            return
        self._stop()
        self.deleted.emit(e.path)

    def remove_current(self):
        if 0 <= self.index < len(self.entries):
            self.entries.pop(self.index)
        if not self.entries:
            self.close_viewer()
            return
        self.index = min(self.index, len(self.entries) - 1)
        self._show_current()

    def keyPressEvent(self, e):  # noqa: N802
        k = e.key()
        if k == Qt.Key.Key_Escape:
            self.close_viewer()
        elif k == Qt.Key.Key_Left:
            self.step(-1)
        elif k == Qt.Key.Key_Right:
            self.step(1)
        elif k == Qt.Key.Key_Space and self._current() and self._current().video:
            self._toggle_play()
        elif k == Qt.Key.Key_Delete:
            self._delete()
        else:
            super().keyPressEvent(e)


# ---------- página ----------
class GalleryPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName("prefs")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.entries: list[Entry] = []
        self.visible_entries: list[Entry] = []
        self.folder = ""
        self.kind = "all"
        self._icons: dict[str, QIcon] = {}
        self._pending: set[str] = set()
        self._signals = _ThumbSignals()
        self._signals.done.connect(self._thumb_ready)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(2, min(4, os.cpu_count() or 2)))
        self._failed: set[str] = set()
        self._video_thumbs = VideoThumbnailer(self)
        self._video_thumbs.done.connect(self._thumb_ready)

        v = QVBoxLayout(self)
        v.setContentsMargins(28, 18, 28, 18)
        v.setSpacing(14)

        top = QHBoxLayout()
        top.setSpacing(10)
        back = QPushButton("  Voltar ao Instagram")
        back.setObjectName("nav")
        ui.themed_icon(back, "back", "text", 18)
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(self.win.close_gallery)
        top.addWidget(back)
        top.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar por perfil ou nome")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(260)
        self.search.textChanged.connect(lambda _t: self._apply_filter())
        top.addWidget(self.search)
        folder = QPushButton("  Abrir pasta")
        ui.themed_icon(folder, "folder", "text", 18)
        folder.setCursor(Qt.CursorShape.PointingHandCursor)
        folder.clicked.connect(lambda: self.win.open_downloads())
        top.addWidget(folder)
        refresh = ui.icon_button("refresh", "Atualizar", self.reload)
        top.addWidget(refresh)
        v.addLayout(top)

        head = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        t = QLabel("Meus downloads")
        t.setObjectName("pageTitle")
        self.count = QLabel("")
        self.count.setObjectName("muted")
        title_col.addWidget(t)
        title_col.addWidget(self.count)
        head.addLayout(title_col, 1)
        self.kind_seg = ui.Segmented([("all", "Tudo"), ("image", "Fotos"), ("video", "Vídeos")], "all")
        self.kind_seg.changed.connect(self._set_kind)
        head.addWidget(self.kind_seg, 0, Qt.AlignmentFlag.AlignBottom)
        v.addLayout(head)

        self.folder_bar = QHBoxLayout()
        self.folder_bar.setSpacing(6)
        v.addLayout(self.folder_bar)

        self.grid = QListWidget()
        self.grid.setObjectName("gallery")
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setIconSize(QSize(TILE, TILE))
        self.grid.setGridSize(QSize(TILE + 20, TILE + 62))
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setWordWrap(True)
        self.grid.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.grid.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.grid.verticalScrollBar().setSingleStep(24)
        self.grid.itemActivated.connect(self._open_item)
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._context_menu)
        self.empty = QLabel("Nada baixado ainda.\nAbra um post, reel ou story e use Baixar (Ctrl+D).")
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body = QStackedWidget()
        self.body.addWidget(self.grid)
        self.body.addWidget(self.empty)
        v.addWidget(self.body, 1)

        self.viewer = Viewer(self)
        self.viewer.deleted.connect(self._delete_paths_from_viewer)
        self.viewer.closed.connect(lambda: self.grid.setFocus())

        self._reload_timer = QTimer(self, singleShot=True, interval=400, timeout=self.reload)

    # ---------- dados ----------
    def open(self, folder: str = ""):
        self.folder = folder
        self.reload()
        self.grid.setFocus()

    def schedule_reload(self):
        if self.isVisible():
            self._reload_timer.start()

    def reload(self):
        self.entries = scan(self.win.download_root)
        folders = sorted({e.folder for e in self.entries if e.folder})
        if self.folder and self.folder not in folders:
            folders.append(self.folder)
        while self.folder_bar.count():
            item = self.folder_bar.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if len(folders) > 0:
            seg = ui.Segmented([("", "Todas as contas")] + [(f, f) for f in folders], self.folder)
            seg.changed.connect(self._set_folder)
            self.folder_bar.addWidget(seg)
        self.folder_bar.addStretch()
        self._apply_filter()

    def _set_folder(self, folder: str):
        self.folder = folder
        self._apply_filter()

    def _set_kind(self, kind: str):
        self.kind = kind
        self._apply_filter()

    def _apply_filter(self):
        q = self.search.text().strip().lower()
        out = []
        for e in self.entries:
            if self.folder and e.folder != self.folder:
                continue
            if self.kind == "video" and not e.video:
                continue
            if self.kind == "image" and e.video:
                continue
            if q and q not in e.path.name.lower() and q not in e.folder.lower():
                continue
            out.append(e)
        self.visible_entries = out
        total = sum(e.size for e in out)
        n_vid = sum(1 for e in out if e.video)
        self.count.setText(f"{len(out)} itens  ·  {len(out) - n_vid} fotos  ·  {n_vid} vídeos  ·  {_human_size(total)}")
        self.grid.clear()
        dpr = self.devicePixelRatioF()
        for e in out:
            key = f"{e.path}|{e.mtime}"
            icon = self._icons.get(key) if key not in self._failed else None
            icon = icon or _tile(None, e.video, dpr)
            it = QListWidgetItem(icon, _label_for(e))
            it.setData(Qt.ItemDataRole.UserRole, str(e.path))
            it.setToolTip(f"{e.path.name}\n{_human_size(e.size)}")
            it.setSizeHint(QSize(TILE + 20, TILE + 62))
            self.grid.addItem(it)
            if key not in self._icons and key not in self._pending and key not in self._failed:
                self._pending.add(key)
                if e.video and not e.thumb.exists():
                    self._video_thumbs.add(key, e.path, e.thumb, int(TILE * dpr))
                else:
                    src = e.thumb if e.video else e.path
                    self._pool.start(_ThumbJob(key, src, int(TILE * dpr), self._signals))
        self.body.setCurrentWidget(self.grid if out else self.empty)

    def _thumb_ready(self, key: str, img: QImage):
        self._pending.discard(key)
        path_str = key.rsplit("|", 1)[0]
        video = Path(path_str).suffix.lower() in VIDEO_EXTS
        icon = _tile(img if not img.isNull() else None, video, self.devicePixelRatioF())
        if img.isNull():
            self._failed.add(key)
        else:
            self._icons[key] = icon
        if len(self._icons) > 3000:
            self._icons.pop(next(iter(self._icons)))
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == path_str:
                it.setIcon(icon)
                break

    # ---------- ações ----------
    def _entry_index(self, path_str: str) -> int:
        return next((i for i, e in enumerate(self.visible_entries) if str(e.path) == path_str), -1)

    def _open_item(self, item: QListWidgetItem):
        i = self._entry_index(item.data(Qt.ItemDataRole.UserRole))
        if i >= 0:
            self.viewer.open(list(self.visible_entries), i)

    def _selected_paths(self) -> list[Path]:
        return [Path(it.data(Qt.ItemDataRole.UserRole)) for it in self.grid.selectedItems()]

    def _context_menu(self, pos):
        item = self.grid.itemAt(pos)
        if item is None:
            return
        if not item.isSelected():
            self.grid.clearSelection()
            item.setSelected(True)
        paths = self._selected_paths()
        m = ui.menu(self)
        if len(paths) == 1:
            p = paths[0]
            m.addAction(ui.icon("image"), "Visualizar", lambda: self._open_item(item))
            m.addAction(ui.icon("external"), "Abrir no app padrão", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(p))))
            m.addAction(ui.icon("folder"), "Abrir pasta", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.parent))))
            if p.suffix.lower() not in VIDEO_EXTS:
                m.addAction(ui.icon("copy"), "Copiar imagem", lambda: QApplication.clipboard().setImage(QImage(str(p))))
            m.addAction(ui.icon("link"), "Copiar caminho", lambda: QApplication.clipboard().setText(str(p)))
            m.addSeparator()
        label = "Mover para a lixeira" if len(paths) == 1 else f"Mover {len(paths)} itens para a lixeira"
        m.addAction(ui.icon("trash", ui.DANGER), label, lambda: self._delete_paths(paths))
        m.exec(self.grid.viewport().mapToGlobal(pos))

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() == Qt.Key.Key_Delete and self.grid.selectedItems():
            self._delete_paths(self._selected_paths())
        elif e.key() == Qt.Key.Key_Escape:
            self.win.close_gallery()
        else:
            super().keyPressEvent(e)

    def _delete_paths(self, paths: list[Path], confirm: bool = True) -> bool:
        if not paths:
            return False
        if confirm and not self.win.confirm(
            "Mover para a lixeira?" if len(paths) == 1 else f"Mover {len(paths)} itens para a lixeira?",
            "Você pode recuperar pela lixeira do sistema.",
            "Mover para a lixeira",
            danger=True,
        ):
            return False
        root = self.win.download_root.resolve()
        failed = 0
        for p in paths:
            try:
                if p.is_symlink() or not p.resolve().is_relative_to(root):
                    failed += 1
                    continue
            except OSError:
                failed += 1
                continue
            if trash(p):
                thumb = thumb_for(p)
                if thumb.exists() and not thumb.is_symlink():
                    try:
                        thumb.unlink()
                    except OSError:
                        pass
            else:
                failed += 1
        self.reload()
        if failed:
            self.win.toast.show_message(f"{failed} item(ns) não puderam ir para a lixeira.")
        else:
            self.win.toast.show_message("Movido para a lixeira.")
        return failed == 0

    def _delete_paths_from_viewer(self, path: Path):
        if self._delete_paths([path]):
            self.viewer.remove_current()
        self.viewer.setFocus()

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        if self.viewer.isVisible():
            self.viewer.setGeometry(self.rect())
