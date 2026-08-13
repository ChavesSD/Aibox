from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class PageHeader(QWidget):
    """Cabeçalho institucional: acento fino + título/subtítulo com fade ao trocar."""

    def __init__(self, title: str = "", subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PageHeader")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        root = QHBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 14)
        root.setSpacing(14)

        self._accent = QFrame()
        self._accent.setObjectName("PageAccent")
        self._accent.setFixedWidth(3)
        self._accent.setFixedHeight(40)
        root.addWidget(self._accent, 0, Qt.AlignVCenter)

        copy = QWidget()
        copy.setObjectName("PageHeaderCopy")
        copy_lay = QVBoxLayout(copy)
        copy_lay.setContentsMargins(0, 0, 0, 0)
        copy_lay.setSpacing(5)

        self.lbl_title = QLabel(title)
        self.lbl_title.setObjectName("PageTitle")
        self.lbl_title.setWordWrap(False)

        self.lbl_sub = QLabel(subtitle)
        self.lbl_sub.setObjectName("PageSubtitle")
        self.lbl_sub.setWordWrap(True)

        copy_lay.addWidget(self.lbl_title)
        copy_lay.addWidget(self.lbl_sub)
        root.addWidget(copy, 1)

        self._copy = copy
        self._opacity = QGraphicsOpacityEffect(copy)
        self._opacity.setOpacity(1.0)
        copy.setGraphicsEffect(self._opacity)

        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._pending: tuple[str, str] | None = None
        self._phase = "idle"
        self._fade.finished.connect(self._on_fade_finished)

    def set_texts(self, title: str, subtitle: str, *, animate: bool = True) -> None:
        if title == self.lbl_title.text() and subtitle == self.lbl_sub.text():
            return

        if not animate:
            self._fade.stop()
            self._phase = "idle"
            self._pending = None
            self.lbl_title.setText(title)
            self.lbl_sub.setText(subtitle)
            self._opacity.setOpacity(1.0)
            return

        self._pending = (title, subtitle)
        self._fade.stop()
        self._phase = "out"
        self._fade.setDuration(100)
        self._fade.setStartValue(float(self._opacity.opacity()))
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_finished(self) -> None:
        if self._phase == "out" and self._pending is not None:
            title, sub = self._pending
            self._pending = None
            self.lbl_title.setText(title)
            self.lbl_sub.setText(sub)
            self._phase = "in"
            self._fade.setDuration(220)
            self._fade.setStartValue(0.0)
            self._fade.setEndValue(1.0)
            self._fade.start()
            return

        self._phase = "idle"
        self._opacity.setOpacity(1.0)
