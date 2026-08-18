from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon, QFont
from PySide6.QtCore import Qt

from .main_window import MainWindow
from .paths import app_icon_path
from .theme import APP_NAME, aibox_stylesheet


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Intelite")
    app.setFont(QFont("Bahnschrift", 10))
    app.setStyleSheet(aibox_stylesheet())
    icon_path = app_icon_path()
    if icon_path is not None:
        app.setWindowIcon(QIcon(str(icon_path)))
    w = MainWindow()
    if icon_path is not None:
        w.setWindowIcon(QIcon(str(icon_path)))
    # Janela compacta: só o espaço necessário por aba
    w.setMinimumSize(900, 800)
    w.resize(920, 820)
    screen = app.primaryScreen()
    if screen is not None:
        geo = screen.availableGeometry()
        w.move(
            geo.x() + (geo.width() - w.width()) // 2,
            geo.y() + (geo.height() - w.height()) // 2,
        )
    w.setWindowState(Qt.WindowNoState)
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
