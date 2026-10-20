"""Kit visual: tema escuro, ícones SVG embutidos e widgets modernos."""

from __future__ import annotations

from PyQt6.QtCore import (
    QByteArray,
    QEasingCurve,
    QEvent,
    QEventLoop,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import weakref

# ---------- temas ----------
_THEMES = {
    "dark": {
        "BG": "#0d0d11", "SURFACE": "#15151b", "SURFACE2": "#1d1d25", "SURFACE3": "#262630",
        "HOVER": "#2f2f3b", "BORDER": "#2a2a35", "TEXT": "#ececf1", "MUTED": "#8d8d9c",
        "IDLE": "#71717a", "DANGER": "#f43f5e", "DANGER_BG": "#2a1218", "DANGER_LINE": "#4a1f2a",
        "SCRIM": (4, 4, 8, 175), "GLOW": 48,
    },
    "light": {
        "BG": "#f5f5f8", "SURFACE": "#ffffff", "SURFACE2": "#f0f0f4", "SURFACE3": "#e6e6ed",
        "HOVER": "#dcdce5", "BORDER": "#dedee6", "TEXT": "#16161d", "MUTED": "#6a6a7a",
        "IDLE": "#a1a1aa", "DANGER": "#e11d48", "DANGER_BG": "#fde7ec", "DANGER_LINE": "#f6c4cf",
        "SCRIM": (20, 20, 30, 110), "GLOW": 34,
    },
}
ACCENTS = {
    "instagram": ("Instagram", ["#feda75", "#fa7e1e", "#d62976", "#962fbf", "#4f5bd5"], "#d62976"),
    "violeta": ("Violeta", ["#c4b5fd", "#a855f7", "#7c3aed", "#4f46e5"], "#8b5cf6"),
    "oceano": ("Oceano", ["#a5f3fc", "#22d3ee", "#0ea5e9", "#2563eb"], "#0ea5e9"),
    "esmeralda": ("Esmeralda", ["#d9f99d", "#4ade80", "#10b981", "#0f766e"], "#10b981"),
}
PALETTE = ["#d62976", "#962fbf", "#4f5bd5", "#fa7e1e", "#10b981", "#0ea5e9", "#e11d48", "#eab308"]
OK = "#22c55e"

# Valores ativos (lidos em tempo de pintura, trocados por set_theme).
BG = SURFACE = SURFACE2 = SURFACE3 = HOVER = BORDER = TEXT = MUTED = IDLE = DANGER = DANGER_BG = DANGER_LINE = ""
SCRIM = (0, 0, 0, 0)
GLOW = 0
GRAD: list[str] = []
ACCENT = ""
QSS_GRAD = ""
MODE = "dark"


def _qss_grad(stops: list[str]) -> str:
    a, b, c = stops[1], stops[len(stops) // 2], stops[-2] if len(stops) > 3 else stops[-1]
    return f"qlineargradient(x1:0, y1:1, x2:1, y2:0, stop:0 {a}, stop:0.5 {b}, stop:1 {c})"


def set_theme(mode: str, accent: str):
    global MODE, GRAD, ACCENT, QSS_GRAD
    MODE = mode if mode in _THEMES else "dark"
    for k, v in _THEMES[MODE].items():
        globals()[k] = v
    _, GRAD, ACCENT = ACCENTS.get(accent, ACCENTS["instagram"])
    QSS_GRAD = _qss_grad(GRAD)


set_theme("dark", "instagram")

_ICONS = {
    "back": '<path d="M15 18l-6-6 6-6"/>',
    "forward": '<path d="M9 18l6-6-6-6"/>',
    "refresh": '<path d="M20 12a8 8 0 1 1-2.34-5.66"/><path d="M20 4v5h-5"/>',
    "home": '<path d="M4 11l8-7 8 7"/><path d="M6 10v10h12V10"/>',
    "ghost": '<path d="M12 3a7 7 0 0 0-7 7v11l2.33-1.75L9.67 21 12 19.25 14.33 21l2.34-1.75L19 21V10a7 7 0 0 0-7-7z"/>'
    '<path d="M9.5 10.5h.01M14.5 10.5h.01" stroke-width="2.6"/>',
    "download": '<path d="M12 4v11"/><path d="M7 10l5 5 5-5"/><path d="M5 20h14"/>',
    "stories": '<path d="M12 3a9 9 0 0 1 0 18" /><path d="M12 21a9 9 0 0 1-9-9" stroke-dasharray="2.5 3"/>'
    '<path d="M3 12a9 9 0 0 1 9-9" stroke-dasharray="2.5 3"/><path d="M12 8v7M9 12.5l3 3 3-3"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2.5"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "settings": '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "chevron": '<path d="M6 9l6 6 6-6"/>',
    "palette": '<path d="M12 3a9 9 0 1 0 0 18c1.1 0 1.5-.8 1.5-1.5 0-.9-.6-1.2-.6-2 0-.9.7-1.5 1.6-1.5H17a4 4 0 0 0 4-4c0-5-4-9-9-9z"/>'
    '<path d="M7.5 11h.01M10 7.5h.01M14.5 7.5h.01" stroke-width="2.6"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "keyboard": '<rect x="2.5" y="6" width="19" height="12" rx="2.5"/><path d="M6 10h.01M9.5 10h.01M13 10h.01M16.5 10h.01M7 14h10" stroke-width="2"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    "up": '<path d="M18 15l-6-6-6 6"/>',
    "down": '<path d="M6 9l6 6 6-6"/>',
    "zoom": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4M8 11h6M11 8v6"/>',
    "check": '<path d="M5 12l5 5 9-10"/>',
    "gift": '<rect x="3.5" y="9" width="17" height="11.5" rx="2"/><path d="M2.5 9h19M12 9v11.5M12 9c-1.5-4-6-4.5-6-2s3 2 6 2c3 0 6 0 6-2s-4.5-2-6 2z"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
    "play": '<path d="M7 4.5v15l12-7.5z" fill="currentColor"/>',
    "pause": '<path d="M8 5v14M16 5v14" stroke-width="3"/>',
    "volume": '<path d="M4 9.5v5h3.5L12 18V6L7.5 9.5z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>',
    "mute": '<path d="M4 9.5v5h3.5L12 18V6L7.5 9.5z"/><path d="M16 10l4 4M20 10l-4 4"/>',
    "image": '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><circle cx="9" cy="10" r="1.6"/><path d="M20.5 16l-5-5-8.5 8.5"/>',
    "external": '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
    "gallery": '<rect x="3.5" y="3.5" width="7" height="7" rx="1.8"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.8"/>'
    '<rect x="3.5" y="13.5" width="7" height="7" rx="1.8"/><path d="M17 13.5v7M13.5 17l3.5 3.5 3.5-3.5"/>',
    "folder": '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v7a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 16.5z"/>',
    "edit": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    "broom": '<path d="M4 20l7-7"/><path d="M14 4l6 6-5 5-6-6z"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3"/>',
    "shield": '<path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2.5"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    "paste": '<rect x="5" y="4" width="14" height="17" rx="2.5"/><path d="M9 4V3h6v1"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1"/><path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1"/>',
}


def icon(name: str, color: str | None = None, size: int = 20) -> QIcon:
    color = color or TEXT
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">{_ICONS[name].replace("currentColor", color)}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    ic = QIcon()
    for scale in (1, 2, 3):
        pm = QPixmap(size * scale, size * scale)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        renderer.render(p)
        p.end()
        pm.setDevicePixelRatio(scale)
        ic.addPixmap(pm)
    return ic


def ig_gradient(rect: QRectF) -> QLinearGradient:
    g = QLinearGradient(rect.bottomLeft(), rect.topRight())
    for i, c in enumerate(GRAD):
        g.setColorAt(i / (len(GRAD) - 1), QColor(c))
    return g


def initials(name: str) -> str:
    parts = [w for w in name.split() if w]
    return "".join(w[0] for w in parts[:2]).upper() or "?"


def build_style() -> str:
    return f"""
* {{ font-size: 13px; color: {TEXT}; }}
QMainWindow, #shell {{ background: {BG}; }}
QToolTip {{ background: {SURFACE3}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 9px; }}
#sidebar {{ background: {BG}; border-right: 1px solid {BORDER}; }}
#header {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
#sep {{ background: {BORDER}; }}
#title {{ font-size: 15px; font-weight: 650; }}
#subtitle {{ color: {MUTED}; font-size: 12px; }}
#pageTitle {{ font-size: 24px; font-weight: 750; }}
#sectionTitle {{ font-size: 12px; font-weight: 700; color: {MUTED}; letter-spacing: 0.6px; }}
QToolButton#icon {{ background: transparent; border: none; border-radius: 10px; }}
QToolButton#icon:hover {{ background: {SURFACE3}; }}
QToolButton#icon:pressed {{ background: {HOVER}; }}
QToolButton#icon:disabled {{ background: transparent; }}
QPushButton {{ background: {SURFACE3}; border: 1px solid {BORDER}; border-radius: 10px; padding: 8px 16px; font-weight: 600; }}
QPushButton:hover {{ background: {HOVER}; }}
QPushButton:disabled {{ color: {IDLE}; }}
QPushButton#pill {{ border-radius: 15px; padding: 7px 14px; }}
QPushButton#pill:checked {{ background: {QSS_GRAD}; border: none; color: white; }}
QPushButton#primary {{ background: {QSS_GRAD}; border: none; border-radius: 15px; padding: 8px 16px; color: white; }}
QPushButton#primaryWide {{ background: {QSS_GRAD}; border: none; border-radius: 12px; padding: 11px 18px; color: white; }}
QPushButton#primary:hover, QPushButton#primaryWide:hover {{ border: 2px solid rgba(255,255,255,0.18); }}
QPushButton#ghostBtn {{ background: transparent; border: none; color: {MUTED}; }}
QPushButton#ghostBtn:hover {{ color: {TEXT}; background: {SURFACE3}; }}
QPushButton#danger {{ background: transparent; border: 1px solid {DANGER_LINE}; color: {DANGER}; }}
QPushButton#danger:hover {{ background: {DANGER_BG}; }}
QPushButton#chip {{ background: transparent; border: 1px solid transparent; border-radius: 12px; padding: 4px 10px 4px 6px; text-align: left; }}
QPushButton#chip:hover {{ background: {SURFACE3}; border: 1px solid {BORDER}; }}
QPushButton#seg {{ background: transparent; border: none; border-radius: 9px; padding: 8px 14px; color: {MUTED}; }}
QPushButton#seg:hover {{ color: {TEXT}; }}
QPushButton#seg:checked {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; }}
#segBox {{ background: {SURFACE3}; border-radius: 12px; }}
QPushButton#nav {{ background: transparent; border: none; border-radius: 10px; padding: 10px 12px; text-align: left; font-weight: 550; color: {MUTED}; }}
QPushButton#nav:hover {{ background: {SURFACE3}; color: {TEXT}; }}
QPushButton#nav:checked {{ background: {SURFACE3}; color: {TEXT}; }}
QPushButton#accountRow {{ background: {SURFACE2}; border: 1px solid transparent; border-radius: 14px; padding: 10px; text-align: left; }}
QPushButton#accountRow:hover {{ border: 1px solid {BORDER}; }}
QPushButton#accountRow:checked {{ border: 1px solid {ACCENT}; background: {SURFACE3}; }}
QLineEdit, QComboBox {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 12px; padding: 10px 12px;
  selection-background-color: {ACCENT}; selection-color: white; }}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit#linkBar {{ border-radius: 17px; padding: 7px 12px; background: {SURFACE2}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 10px; padding: 4px;
  selection-background-color: {SURFACE3}; selection-color: {TEXT}; outline: none; }}
QSlider::groove:horizontal {{ height: 6px; background: {SURFACE3}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {QSS_GRAD}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: white; border: 1px solid {BORDER}; width: 18px; height: 18px; margin: -7px 0; border-radius: 9px; }}
#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 20px; }}
#panel {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 18px; }}
#prefsNav {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
#prefs, #prefsViewport {{ background: {BG}; }}
#prefsPage, #sideHolder, #sideViewport {{ background: transparent; }}
#sheetTitle {{ font-size: 18px; font-weight: 700; }}
#muted {{ color: {MUTED}; }}
#error {{ color: {DANGER}; }}
#rowTitle {{ font-weight: 600; }}
#row {{ background: {SURFACE2}; border-radius: 14px; }}
#mono {{ font-family: monospace; font-size: 12.5px; color: {TEXT}; }}
#kbd {{ background: {SURFACE3}; border: 1px solid {BORDER}; border-radius: 6px; padding: 3px 8px; font-family: monospace; }}
QProgressBar {{ background: {SURFACE3}; border: none; border-radius: 4px; }}
QProgressBar::chunk {{ background: {QSS_GRAD}; border-radius: 4px; }}
#popover {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 16px; }}
QPushButton#item {{ background: transparent; border: none; border-radius: 10px; padding: 9px 10px; text-align: left; font-weight: 500; }}
QPushButton#item:hover {{ background: {SURFACE3}; }}
QPushButton#itemDanger {{ background: transparent; border: none; border-radius: 10px; padding: 9px 10px; text-align: left; font-weight: 500; color: {DANGER}; }}
QPushButton#itemDanger:hover {{ background: {DANGER_BG}; }}
#toast {{ background: {SURFACE3}; border: 1px solid {BORDER}; border-radius: 18px; }}
#toast QLabel {{ background: transparent; }}
QPushButton#toastAction {{ background: transparent; border: none; color: {ACCENT}; padding: 4px 8px; font-weight: 700; }}
QPushButton#toastAction:hover {{ background: {HOVER}; }}
#viewerTitle {{ color: #ffffff; font-size: 16px; font-weight: 700; }}
#viewerInfo {{ color: #c4c4cf; }}
QPushButton#viewerBtn {{ background: rgba(255,255,255,0.10); border: none; border-radius: 20px; padding: 0; }}
QPushButton#viewerBtn:hover {{ background: rgba(255,255,255,0.22); }}
QPushButton#viewerNav {{ background: rgba(255,255,255,0.08); border: none; border-radius: 26px; padding: 0; }}
QPushButton#viewerNav:hover {{ background: rgba(255,255,255,0.2); }}
QListWidget#gallery {{ background: transparent; border: none; outline: none; }}
QListWidget#gallery::item {{ border-radius: 16px; padding: 6px; color: {TEXT}; }}
QListWidget#gallery::item:hover {{ background: {SURFACE2}; }}
QListWidget#gallery::item:selected {{ background: {SURFACE3}; color: {TEXT}; }}
QMenu {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 12px; padding: 6px; }}
QMenu::item {{ padding: 8px 18px 8px 12px; border-radius: 8px; }}
QMenu::item:selected {{ background: {SURFACE3}; color: {TEXT}; }}
QMenu::item:disabled {{ color: {IDLE}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}
QMenu::icon {{ padding-left: 8px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE3}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
"""


def system_scheme(app) -> str:
    hints = app.styleHints()
    if hasattr(hints, "unsetColorScheme"):
        hints.unsetColorScheme()
    scheme = hints.colorScheme() if hasattr(hints, "colorScheme") else None
    return "light" if scheme == Qt.ColorScheme.Light else "dark"


def resolve_mode(app, theme: str) -> str:
    return system_scheme(app) if theme == "system" else theme


def apply_theme(app, theme: str = "dark", accent: str = "instagram"):
    mode = resolve_mode(app, theme)
    set_theme(mode, accent)
    app.setStyle("Fusion")
    hints = app.styleHints()
    if theme != "system" and hasattr(hints, "setColorScheme"):
        hints.setColorScheme(Qt.ColorScheme.Dark if mode == "dark" else Qt.ColorScheme.Light)
    pal = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, BG),
        (QPalette.ColorRole.WindowText, TEXT),
        (QPalette.ColorRole.Base, SURFACE2),
        (QPalette.ColorRole.AlternateBase, SURFACE),
        (QPalette.ColorRole.Text, TEXT),
        (QPalette.ColorRole.Button, SURFACE3),
        (QPalette.ColorRole.ButtonText, TEXT),
        (QPalette.ColorRole.Highlight, ACCENT),
        (QPalette.ColorRole.HighlightedText, "#ffffff"),
        (QPalette.ColorRole.ToolTipBase, SURFACE3),
        (QPalette.ColorRole.ToolTipText, TEXT),
        (QPalette.ColorRole.PlaceholderText, MUTED),
        (QPalette.ColorRole.Link, ACCENT),
    ):
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    app.setStyleSheet(build_style())
    refresh_icons()
    for w in app.allWidgets():
        w.update()


# ---------- ícones que acompanham o tema ----------
_themed: list = []


def _role_color(role: str) -> str:
    return {"text": TEXT, "muted": MUTED, "white": "#ffffff", "danger": DANGER, "accent": ACCENT}.get(role, TEXT)


def themed_icon(widget, name: str, role: str = "text", size: int = 20):
    widget.setIcon(icon(name, _role_color(role), size))
    _themed.append((weakref.ref(widget), name, role, size))


def refresh_icons():
    alive = []
    for ref, name, role, size in _themed:
        w = ref()
        if w is None:
            continue
        try:
            w.setIcon(icon(name, _role_color(role), size))
        except RuntimeError:  # widget C++ já destruído
            continue
        alive.append((ref, name, role, size))
    _themed[:] = alive


def menu(parent) -> QMenu:
    m = QMenu(parent)
    m.setWindowFlags(m.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
    m.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    return m


def icon_button(name: str, tip: str, slot=None, size: int = 36) -> QToolButton:
    b = QToolButton()
    b.setObjectName("icon")
    themed_icon(b, name)
    b.setIconSize(QSize(20, 20))
    b.setFixedSize(size, size)
    b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if slot:
        b.clicked.connect(slot)
    return b


class Toggle(QAbstractButton):
    """Switch animado no estilo iOS/Material."""

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def sizeHint(self):  # noqa: N802
        return QSize(44, 24)

    def _animate(self, on: bool):
        self._anim.stop()
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _get(self):
        return self._pos

    def _set(self, v):
        self._pos = v
        self.update()

    pos = pyqtProperty(float, _get, _set)

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0, 0, 44, 24)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(BORDER))
        p.drawRoundedRect(r, 12, 12)
        if self._pos > 0:
            p.setOpacity(self._pos)
            p.setBrush(QBrush(ig_gradient(r)))
            p.drawRoundedRect(r, 12, 12)
            p.setOpacity(1)
        p.setBrush(QColor("white"))
        p.drawEllipse(QPointF(12 + 20 * self._pos, 12), 9, 9)


class AvatarButton(QAbstractButton):
    """Avatar da conta na barra lateral: anel em gradiente quando ativa e ponto de estado."""

    SIZE = 44

    def __init__(self, name: str, color: str, parent=None):
        super().__init__(parent)
        self.name, self.color, self.state = name, color, "off"
        self.setCheckable(True)
        self.setFixedSize(60, 54)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = False

    def set_data(self, name: str, color: str, state: str):
        self.name, self.color, self.state = name, color, state
        self.update()

    def enterEvent(self, e):  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, e):  # noqa: N802
        self._hover = False
        self.update()

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy, s = self.width() / 2 + 2, self.height() / 2, self.SIZE
        outer = QRectF(cx - s / 2, cy - s / 2, s, s)
        if self.isChecked():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(TEXT))
            p.drawRoundedRect(QRectF(0, cy - 12, 4, 24), 2, 2)
            p.setBrush(QBrush(ig_gradient(outer)))
            p.drawEllipse(outer)
            p.setBrush(QColor(BG))
            p.drawEllipse(outer.adjusted(2.5, 2.5, -2.5, -2.5))
        elif self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(TEXT))
            p.drawRoundedRect(QRectF(0, cy - 5, 4, 10), 2, 2)
        inner = outer.adjusted(5, 5, -5, -5)
        c = QColor(self.color)
        if self.state != "active" and not self._hover:
            c = c.darker(135)
        g = QLinearGradient(inner.topLeft(), inner.bottomRight())
        g.setColorAt(0, c.lighter(125))
        g.setColorAt(1, c)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(inner)
        f = p.font()
        f.setPixelSize(13)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("white"))
        p.drawText(inner, Qt.AlignmentFlag.AlignCenter, initials(self.name))
        if self.state in ("active", "sleep"):
            dot = QPointF(outer.right() - 6, outer.bottom() - 6)
            p.setBrush(QColor(BG))
            p.drawEllipse(dot, 6.5, 6.5)
            p.setBrush(QColor(OK if self.state == "active" else IDLE))
            p.drawEllipse(dot, 4.2, 4.2)


class AddButton(QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(60, 54)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Adicionar conta")
        self._hover = False

    def enterEvent(self, e):  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, e):  # noqa: N802
        self._hover = False
        self.update()

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() / 2 + 2, self.height() / 2
        r = QRectF(cx - 17, cy - 17, 34, 34)
        pen = QPen(QColor(ACCENT if self._hover else MUTED), 1.6)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(QColor(SURFACE2 if self._hover else BG))
        p.drawEllipse(r)
        icon("plus", TEXT if self._hover else MUTED, 18).paint(p, r.adjusted(8, 8, -8, -8).toRect())


class Toast(QFrame):
    def __init__(self, host: QWidget):
        super().__init__(host)
        self.setObjectName("toast")
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 8, 8, 8)
        h.setSpacing(6)
        self.label = QLabel()
        self.action = QPushButton()
        self.action.setObjectName("toastAction")
        self.action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action.clicked.connect(self._run_action)
        h.addWidget(self.label)
        h.addWidget(self.action)
        self._callback = None
        self._fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity", self)
        self._anim.setDuration(220)
        self._timer = QTimer(self, singleShot=True, timeout=self._fade_out)
        self.hide()

    def show_message(self, text: str, ms: int = 3500, action: str = "", callback=None):
        self.label.setText(text)
        self._callback = callback
        self.action.setVisible(bool(action and callback))
        self.action.setText(action)
        self.layout().setContentsMargins(18, 8, 8 if action else 18, 8)
        self.adjustSize()
        host = self.parentWidget()
        self.move((host.width() - self.width()) // 2, host.height() - self.height() - 28)
        self.raise_()
        self.show()
        self._anim.stop()
        self._anim.setStartValue(self._fx.opacity())
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._timer.start(ms + (2500 if action else 0))

    def _run_action(self):
        cb = self._callback
        self._fade_out()
        if cb:
            cb()

    def _fade_out(self):
        self._anim.stop()
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.start()
        QTimer.singleShot(240, lambda: self._fx.opacity() < 0.05 and self.hide())


class Sheet(QWidget):
    """Diálogo modal dentro da janela: fundo escurecido e card central."""

    def __init__(self, host: QWidget, title: str, subtitle: str = "", width: int = 440):
        super().__init__(host)
        self._host = host
        self.result = None
        self._loop: QEventLoop | None = None
        self._default = None
        self.card = QFrame(self)
        self.card.setObjectName("card")
        self.card.setFixedWidth(width)
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(70)
        shadow.setOffset(0, 18)
        shadow.setColor(QColor(0, 0, 0, 170))
        self.card.setGraphicsEffect(shadow)
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(26, 22, 26, 22)
        lay.setSpacing(14)
        head = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(3)
        t = QLabel(title)
        t.setObjectName("sheetTitle")
        col.addWidget(t)
        if subtitle:
            st = QLabel(subtitle)
            st.setObjectName("muted")
            st.setWordWrap(True)
            col.addWidget(st)
        head.addLayout(col, 1)
        head.addWidget(icon_button("close", "Fechar (Esc)", lambda: self.done(None), 32), 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(12)
        lay.addLayout(self.body)
        self.footer = QHBoxLayout()
        self.footer.setSpacing(10)
        self.footer.addStretch()
        lay.addSpacing(4)
        lay.addLayout(self.footer)

    def add_button(self, text: str, value, role: str = "", default: bool = False, left: bool = False):
        b = QPushButton(text)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        if role:
            b.setObjectName(role)
        b.clicked.connect(lambda: self.done(value))
        if left:
            self.footer.insertWidget(0, b)
        else:
            self.footer.addWidget(b)
        if default:
            self._default = value
        return b

    def done(self, value):
        self.result = value
        if self._loop:
            self._loop.quit()

    def accept_default(self):
        self.done(self._default)

    def exec(self, focus: QWidget | None = None):
        self._host.installEventFilter(self)
        self._place()
        self.show()
        self.raise_()
        (focus or self.card).setFocus()
        self._loop = QEventLoop()
        self._loop.exec()
        self._host.removeEventFilter(self)
        self.hide()
        self.deleteLater()
        return self.result

    def _place(self):
        self.setGeometry(self._host.rect())
        lay = self.card.layout()
        lay.activate()
        w = self.card.width()
        h = lay.totalHeightForWidth(w) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        self.card.setFixedHeight(max(h, lay.totalMinimumSize().height()))
        self.card.move((self.width() - self.card.width()) // 2, max(20, (self.height() - self.card.height()) // 2))

    def eventFilter(self, obj, ev):  # noqa: N802
        if obj is self._host and ev.type() == QEvent.Type.Resize:
            self._place()
        return False

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(*SCRIM))

    def mousePressEvent(self, e):  # noqa: N802
        if not self.card.geometry().contains(e.position().toPoint()):
            self.done(None)

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.done(None)
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._default is not None:
            self.accept_default()
        else:
            super().keyPressEvent(e)


def setting_row(title: str, desc: str, control: QWidget) -> QFrame:
    row = QFrame()
    row.setObjectName("row")
    h = QHBoxLayout(row)
    h.setContentsMargins(14, 12, 14, 12)
    h.setSpacing(12)
    col = QVBoxLayout()
    col.setSpacing(2)
    t = QLabel(title)
    t.setObjectName("rowTitle")
    d = QLabel(desc)
    d.setObjectName("muted")
    d.setWordWrap(True)
    col.addWidget(t)
    col.addWidget(d)
    h.addLayout(col, 1)
    h.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


class Backdrop(QWidget):
    """Fundo escuro com brilhos em gradiente (tela de bloqueio e estado vazio)."""

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(BG))
        w, h = self.width(), self.height()
        for (x, y, rad, color) in ((0.2, 0.15, 0.55, GRAD[-2]), (0.85, 0.9, 0.6, GRAD[len(GRAD) // 2]), (0.9, 0.1, 0.35, GRAD[1])):
            g = QRadialGradient(QPointF(w * x, h * y), max(w, h) * rad)
            c = QColor(color)
            c.setAlpha(GLOW)
            g.setColorAt(0, c)
            c.setAlpha(0)
            g.setColorAt(1, c)
            p.fillRect(self.rect(), QBrush(g))


class BigAvatar(QWidget):
    def __init__(self, name: str, color: str, size: int = 76, parent=None):
        super().__init__(parent)
        self.name, self.color, self._s = name, color, size
        self.setFixedSize(size, size)

    def set_data(self, name: str, color: str):
        self.name, self.color = name, color
        self.update()

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0, 0, self._s, self._s)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(ig_gradient(r)))
        p.drawEllipse(r)
        p.setBrush(QColor(SURFACE))
        p.drawEllipse(r.adjusted(3, 3, -3, -3))
        inner = r.adjusted(7, 7, -7, -7)
        c = QColor(self.color)
        g = QLinearGradient(inner.topLeft(), inner.bottomRight())
        g.setColorAt(0, c.lighter(125))
        g.setColorAt(1, c)
        p.setBrush(QBrush(g))
        p.drawEllipse(inner)
        f = p.font()
        f.setPixelSize(int(self._s * 0.3))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("white"))
        p.drawText(inner, Qt.AlignmentFlag.AlignCenter, initials(self.name))


class Swatch(QAbstractButton):
    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self.color = color
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setFixedSize(30, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0, 0, 30, 30)
        if self.isChecked():
            p.setPen(QPen(QColor(TEXT), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(r.adjusted(1, 1, -1, -1))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.color))
        p.drawEllipse(r.adjusted(5, 5, -5, -5))


class Segmented(QFrame):
    changed = pyqtSignal(str)

    def __init__(self, options: list[tuple[str, str]], value: str, parent=None):
        super().__init__(parent)
        self.setObjectName("segBox")
        h = QHBoxLayout(self)
        h.setContentsMargins(3, 3, 3, 3)
        h.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for key, label in options:
            b = QPushButton(label)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setChecked(key == value)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, key=key: self.changed.emit(key))
            self._group.addButton(b)
            h.addWidget(b)


class AccentSwatch(QAbstractButton):
    def __init__(self, stops: list[str], parent=None):
        super().__init__(parent)
        self.stops = stops
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setFixedSize(40, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0, 0, 40, 40)
        if self.isChecked():
            p.setPen(QPen(QColor(TEXT), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(r.adjusted(1, 1, -1, -1))
        g = QLinearGradient(r.bottomLeft(), r.topRight())
        for i, c in enumerate(self.stops):
            g.setColorAt(i / (len(self.stops) - 1), QColor(c))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(r.adjusted(6, 6, -6, -6))


def avatar_icon(name: str, color: str, size: int = 36) -> QIcon:
    ic = QIcon()
    for scale in (1, 2):
        pm = QPixmap(size * scale, size * scale)
        pm.fill(Qt.GlobalColor.transparent)
        pm.setDevicePixelRatio(scale)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0, 0, size, size)
        c = QColor(color)
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0, c.lighter(125))
        g.setColorAt(1, c)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(r)
        f = p.font()
        f.setPixelSize(int(size * 0.36))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("white"))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, initials(name))
        p.end()
        ic.addPixmap(pm)
    return ic


def circle_pixmap(pm: QPixmap, size: int) -> QPixmap:
    from PyQt6.QtGui import QPainterPath

    out = QPixmap(size * 2, size * 2)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addEllipse(QRectF(0, 0, size * 2, size * 2))
    p.setClipPath(path)
    if not pm.isNull():
        scaled = pm.scaled(size * 2, size * 2, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap((size * 2 - scaled.width()) // 2, (size * 2 - scaled.height()) // 2, scaled)
    p.end()
    out.setDevicePixelRatio(2)
    return out
