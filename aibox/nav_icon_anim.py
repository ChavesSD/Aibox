from __future__ import annotations

import math
from enum import Enum

from PySide6.QtCore import QEasingCurve, QObject, QRect, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QStyle, QToolButton

# Fração do box preenchida pelo glifo (margem para a animação).
_NAV_ICON_FILL = 0.72
# Alpha mínimo para considerar pixel “visível” no crop óptico.
_NAV_ICON_ALPHA = 24


class NavAnimKind(Enum):
    PLUG = "plug"
    BUG = "bug"
    DPI = "dpi"
    RECORD = "record"
    DOWNLOAD = "download"
    CAMERA = "camera"
    UNDO = "undo"
    INFO = "info"
    TRASH = "trash"
    BOLT = "bolt"


_FA_TO_KIND: dict[str, NavAnimKind] = {
    "fa5s.plug": NavAnimKind.PLUG,
    "fa5s.bug": NavAnimKind.BUG,
    "fa5s.expand": NavAnimKind.DPI,
    "fa5s.video": NavAnimKind.RECORD,
    "fa5s.download": NavAnimKind.DOWNLOAD,
    "fa5s.camera": NavAnimKind.CAMERA,
    "fa5s.undo": NavAnimKind.UNDO,
    "fa5s.info-circle": NavAnimKind.INFO,
    "fa5s.trash-alt": NavAnimKind.TRASH,
    "fa5s.trash": NavAnimKind.TRASH,
    "fa5s.bolt": NavAnimKind.BOLT,
    "fa5s.tachometer-alt": NavAnimKind.BOLT,
}


def kind_for_fa(fa_name: str) -> NavAnimKind:
    return _FA_TO_KIND.get(fa_name, NavAnimKind.INFO)


def _content_bounds(img: QImage, *, alpha_min: int = _NAV_ICON_ALPHA) -> QRect | None:
    """Retângulo do conteúdo opaco (None se vazio)."""
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return None
    min_x, min_y = w, h
    max_x, max_y = -1, -1
    for y in range(h):
        for x in range(w):
            if img.pixelColor(x, y).alpha() >= alpha_min:
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if x > max_x:
                    max_x = x
                if y > max_y:
                    max_y = y
    if max_x < min_x or max_y < min_y:
        return None
    return QRect(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)


def normalize_glyph_pixmap(
    src: QPixmap,
    size: QSize,
    *,
    fill: float = _NAV_ICON_FILL,
) -> QPixmap:
    """
    Recorta o glifo pelo conteúdo e escala para ocupar a mesma área óptica
    em todos os ícones (centrado num canvas transparente fixo).
    """
    out = QPixmap(size)
    out.fill(Qt.transparent)
    if src.isNull() or size.width() <= 0 or size.height() <= 0:
        return out

    img = src.toImage().convertToFormat(QImage.Format_ARGB32)
    bounds = _content_bounds(img)
    if bounds is None or bounds.width() <= 0 or bounds.height() <= 0:
        scaled = src.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        p = QPainter(out)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.drawPixmap((size.width() - scaled.width()) // 2, (size.height() - scaled.height()) // 2, scaled)
        p.end()
        return out

    cropped = QPixmap.fromImage(img.copy(bounds))
    box = min(size.width(), size.height())
    target = max(1, int(round(box * fill)))
    scaled = cropped.scaled(target, target, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    p.drawPixmap((size.width() - scaled.width()) // 2, (size.height() - scaled.height()) // 2, scaled)
    p.end()
    return out


def _clamp01(t: float) -> float:
    return 0.0 if t <= 0 else 1.0 if t >= 1 else float(t)


def _ease_out_cubic(t: float) -> float:
    t = _clamp01(t)
    return 1.0 - (1.0 - t) ** 3


def _ease_in_out_cubic(t: float) -> float:
    t = _clamp01(t)
    if t < 0.5:
        return 4.0 * t * t * t
    return 1.0 - (-2.0 * t + 2.0) ** 3 / 2.0


def _pose(kind: NavAnimKind, t: float) -> tuple[float, float, float, float, float]:
    """Retorna (tx, ty, degrees, scale, flash) para o progresso t ∈ [0, 1]."""
    t = _clamp01(t)

    if kind is NavAnimKind.PLUG:
        # Encaixe sutil do plug (movimento curto p/ não clipar no box).
        if t < 0.62:
            u = _ease_out_cubic(t / 0.62)
            return (-2.2 + 3.0 * u, 0.0, -5.0 + 5.0 * u, 1.0, 0.0)
        u = _ease_out_cubic((t - 0.62) / 0.38)
        return (0.8 * (1.0 - u), 0.0, 0.0, 1.0, 0.0)

    if kind is NavAnimKind.BUG:
        # Bicho agitadinho, amortecendo.
        decay = 1.0 - t
        wig = math.sin(t * math.pi * 4.0) * 11.0 * decay
        bob = math.sin(t * math.pi * 2.0) * 1.4 * decay
        return (wig * 0.12, bob, wig, 1.0 + 0.04 * abs(math.sin(t * math.pi * 2.0)) * decay, 0.0)

    if kind is NavAnimKind.DPI:
        # Expansão tipo zoom de densidade.
        pulse = math.sin(t * math.pi)
        sc = 0.78 + 0.34 * _ease_out_cubic(min(1.0, t * 1.35))
        if t > 0.55:
            sc = 1.12 - 0.12 * _ease_out_cubic((t - 0.55) / 0.45)
        return (0.0, 0.0, 0.0, sc + 0.02 * pulse * (1.0 - t), 0.0)

    if kind is NavAnimKind.RECORD:
        # Pulso de “gravando”.
        pulse = math.sin(t * math.pi)
        return (0.0, 0.0, 0.0, 1.0 + 0.14 * pulse, 0.55 * pulse)

    if kind is NavAnimKind.DOWNLOAD:
        # Seta descendo com bounce leve.
        if t < 0.58:
            u = _ease_in_out_cubic(t / 0.58)
            return (0.0, -5.0 + 9.0 * u, 0.0, 1.0, 0.0)
        u = (t - 0.58) / 0.42
        bounce = 3.2 * (1.0 - u) * math.cos(u * math.pi * 1.5)
        return (0.0, bounce, 0.0, 1.0, 0.0)

    if kind is NavAnimKind.CAMERA:
        # Clique do obturador + flash.
        if t < 0.22:
            u = t / 0.22
            return (0.0, 0.0, 0.0, 1.0 - 0.18 * u, 0.0)
        if t < 0.38:
            u = (t - 0.22) / 0.16
            return (0.0, 0.0, 0.0, 0.82 + 0.28 * u, 0.85 * (1.0 - u))
        u = _ease_out_cubic((t - 0.38) / 0.62)
        return (0.0, 0.0, 0.0, 1.1 - 0.1 * u, 0.15 * (1.0 - u))

    if kind is NavAnimKind.UNDO:
        # Rebobina no sentido anti-horário.
        ang = -300.0 * _ease_out_cubic(t)
        sc = 1.0 + 0.06 * math.sin(t * math.pi)
        return (0.0, 0.0, ang, sc, 0.0)

    if kind is NavAnimKind.TRASH:
        # Sacode a lixeira.
        decay = 1.0 - t
        wig = math.sin(t * math.pi * 5.0) * 9.0 * decay
        return (wig * 0.08, 0.0, wig * 0.35, 1.0, 0.0)

    if kind is NavAnimKind.BOLT:
        # Raio de performance.
        pulse = math.sin(t * math.pi)
        return (0.0, -1.2 * pulse, 0.0, 0.92 + 0.16 * pulse, 0.35 * pulse)

    # INFO — pulse in-place (leve descida; sem subir, evita corte no topo)
    pulse = math.sin(t * math.pi)
    return (0.0, 0.9 * pulse, 0.0, 1.0 + 0.06 * pulse, 0.0)


def render_nav_icon_frame(
    base: QPixmap,
    kind: NavAnimKind,
    t: float,
    display: QSize,
    *,
    accent: str = "#2f7bff",
) -> QPixmap:
    """Desenha um frame animado do ícone (com AA em buffer 2×)."""
    dw = max(1, display.width())
    dh = max(1, display.height())
    bw, bh = dw * 2, dh * 2

    canvas = QPixmap(bw, bh)
    canvas.fill(Qt.transparent)

    tx, ty, deg, scale, flash = _pose(kind, t)
    # coordenadas no buffer 2×
    tx *= 2.0
    ty *= 2.0

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

    cx, cy = bw / 2.0, bh / 2.0
    painter.translate(cx + tx, cy + ty)
    painter.rotate(deg)
    painter.scale(scale, scale)

    # base já vem normalizado no square; desenha em 2×
    src = base
    if src.width() != bw or src.height() != bh:
        src = base.scaled(bw, bh, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    painter.drawPixmap(int(-src.width() / 2), int(-src.height() / 2), src)

    # Extra: anel/pulso de gravação
    if kind is NavAnimKind.RECORD and flash > 0.02:
        painter.resetTransform()
        painter.translate(cx, cy)
        ring = max(bw, bh) * (0.42 + 0.28 * flash)
        c = QColor(accent)
        c.setAlphaF(0.55 * flash)
        painter.setBrush(Qt.NoBrush)
        pen = QPen(c)
        pen.setWidthF(2.4)
        painter.setPen(pen)
        painter.drawEllipse(-ring / 2, -ring / 2, ring, ring)

        # ponto “rec”
        soft = QColor(accent)
        soft.setAlphaF(0.35 * flash)
        painter.setBrush(soft)
        painter.setPen(Qt.NoPen)
        d = 5.5 + 2.5 * flash
        painter.drawEllipse(-d / 2, -d / 2, d, d)

    # Extra: flash da câmera
    if kind is NavAnimKind.CAMERA and flash > 0.02:
        painter.resetTransform()
        painter.translate(cx, cy)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        glow = QColor("#ffffff")
        glow.setAlphaF(0.55 * flash)
        painter.setBrush(glow)
        painter.setPen(Qt.NoPen)
        r = max(bw, bh) * (0.55 + 0.2 * flash)
        painter.drawEllipse(-r / 2, -r / 2, r, r)

    painter.end()
    return canvas.scaled(dw, dh, Qt.KeepAspectRatio, Qt.SmoothTransformation)


class NavIconAnimator(QObject):
    """Anima o ícone de um QToolButton da sidebar no clique."""

    def __init__(
        self,
        button: QToolButton,
        *,
        fa_name: str,
        fallback: QStyle.StandardPixmap,
        color: str,
        size: QSize,
        icon_factory,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent or button)
        self._button = button
        self._kind = kind_for_fa(fa_name)
        self._size = QSize(size)
        self._color = color
        self._busy = False

        hi = QSize(max(48, size.width() * 4), max(48, size.height() * 4))
        icon = icon_factory(fa_name, fallback, hi, color, button.style())
        raw = icon.pixmap(hi)
        if raw.isNull():
            raw = icon.pixmap(size)
        # Mesmo tamanho óptico para todos os glifos Font Awesome.
        self._base = normalize_glyph_pixmap(raw, QSize(size.width() * 2, size.height() * 2))

        self._rest_icon = QIcon(render_nav_icon_frame(self._base, self._kind, 0.0, self._size))
        button.setIcon(self._rest_icon)
        button.setIconSize(self._size)

        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(self._duration_for(self._kind))
        self._anim.setEasingCurve(QEasingCurve.Linear)
        self._anim.valueChanged.connect(self._on_frame)
        self._anim.finished.connect(self._on_finished)

    @staticmethod
    def _duration_for(kind: NavAnimKind) -> int:
        return {
            NavAnimKind.PLUG: 460,
            NavAnimKind.BUG: 520,
            NavAnimKind.DPI: 440,
            NavAnimKind.RECORD: 480,
            NavAnimKind.DOWNLOAD: 500,
            NavAnimKind.CAMERA: 420,
            NavAnimKind.UNDO: 560,
            NavAnimKind.INFO: 440,
        }.get(kind, 460)

    def play(self) -> None:
        if self._base.isNull():
            return
        self._busy = True
        self._anim.stop()
        self._anim.setCurrentTime(0)
        self._anim.start()

    def _on_frame(self, value: object) -> None:
        t = float(value)  # type: ignore[arg-type]
        frame = render_nav_icon_frame(self._base, self._kind, t, self._size)
        self._button.setIcon(QIcon(frame))

    def _on_finished(self) -> None:
        self._button.setIcon(self._rest_icon)
        self._busy = False
