from __future__ import annotations

from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QRectF, Qt, Property, QSize
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QAbstractButton, QWidget, QHBoxLayout, QLabel, QSizePolicy

from .theme import COLOR_BORDER, COLOR_DISABLED, COLOR_PRIMARY, COLOR_SURFACE_2, COLOR_SUBTLE, COLOR_TEXT


class ToggleSwitch(QAbstractButton):
    """Botão liga/desliga com thumb deslizante."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFixedSize(46, 26)
        self._offset = 3.0
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate_to_state)
        self._animate_to_state(self.isChecked(), animate=False)

    def sizeHint(self) -> QSize:
        return QSize(46, 26)

    def get_offset(self) -> float:
        return float(self._offset)

    def set_offset(self, value: float) -> None:
        self._offset = float(value)
        self.update()

    offset = Property(float, get_offset, set_offset)

    def _animate_to_state(self, checked: bool, animate: bool = True) -> None:
        end = 23.0 if checked else 3.0
        self._anim.stop()
        if not animate:
            self.set_offset(end)
            return
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(end)
        self._anim.start()

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        changed = checked != self.isChecked()
        super().setChecked(checked)
        if changed:
            self._animate_to_state(checked)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        enabled = self.isEnabled()
        checked = self.isChecked()

        if not enabled:
            track = QColor(COLOR_BORDER)
            thumb = QColor(COLOR_DISABLED)
        elif checked:
            track = QColor(COLOR_PRIMARY)
            thumb = QColor("#ffffff")
        else:
            track = QColor(COLOR_SURFACE_2)
            thumb = QColor(COLOR_SUBTLE)

        track_rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        path = QPainterPath()
        path.addRoundedRect(track_rect, 13, 13)
        p.fillPath(path, track)

        if not checked and enabled:
            p.setPen(QColor(COLOR_BORDER))
            p.drawRoundedRect(track_rect, 13, 13)

        diameter = self.height() - 8
        y = 4.0
        thumb_rect = QRectF(self._offset, y, diameter, diameter)
        p.setPen(Qt.NoPen)
        p.setBrush(thumb)
        p.drawEllipse(thumb_rect)
        p.end()


class ToggleRow(QWidget):
    """Linha com switch + rótulo à direita."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ToggleRow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(8)
        self.toggle = ToggleSwitch()
        self.label = QLabel(text)
        self.label.setObjectName("ToggleRowLabel")
        self.label.setWordWrap(False)
        self.label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        lay.addWidget(self.toggle, 0, Qt.AlignVCenter)
        lay.addWidget(self.label, 1, Qt.AlignVCenter)

    def isChecked(self) -> bool:  # noqa: N802
        return self.toggle.isChecked()

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        self.toggle.setChecked(checked)

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        super().setEnabled(enabled)
        self.toggle.setEnabled(enabled)
        self.label.setEnabled(enabled)

    def setText(self, text: str) -> None:  # noqa: N802
        self.label.setText(text)

    def text(self) -> str:
        return self.label.text()
