from __future__ import annotations

import ipaddress
import os
import re
import secrets
import socket
import time
from pathlib import Path

from PySide6.QtCore import Qt, QEasingCurve, QEvent, QPropertyAnimation, QSize, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .adb import uninstall_protection, Adb, AdbDevice, AdbError, AutostartConfigResult, InstalledApp, TtsConfigResult
from .apks_catalog import (
    APK_CATALOG,
    APK_CATEGORIES,
    ApkEntry,
    ensure_apks_tree,
    entries_for,
    resolve_apk,
)
from .apk_sync import ApkSyncResult, sync_apks
from .device_audio import DeviceAudioRecordThread
from .logcat import LogcatThread
from .reboot_validate import wait_and_validate
from .mic_record import MicRecordThread, list_microphones
from .recording import ScreenRecordThread, _resolve_ffmpeg, mux_audios_into_mp4
from .nav_icon_anim import NavIconAnimator
from .page_header import PageHeader
from .paths import is_frozen, resource_path
from .toggle_switch import ToggleRow, ToggleSwitch
from .updater import (
    UpdateCheckResult,
    check_for_updates,
    format_notes_plain,
    install_dir_for_update,
    launch_update_helper,
    stage_update_package,
)
from .procutil import run_hidden
from .theme import (
    APP_DIR_NAME,
    APP_NAME,
    APP_VERSION,
    COLOR_ICON,
    COLOR_ICON_ON_PRIMARY,
)
from .workers import Background


def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "?"
    units = ["B", "KB", "MB", "GB", "TB"]
    v = float(n)
    i = 0
    while v >= 1024 and i < len(units) - 1:
        v /= 1024.0
        i += 1
    if i == 0:
        return f"{int(v)} {units[i]}"
    return f"{v:.2f} {units[i]}"


def _fa_icon(name: str, color: str) -> QIcon | None:
    try:
        import qtawesome as qta

        return qta.icon(name, color=color)
    except Exception:
        return None


def _tinted(icon: QIcon, size: QSize, color: str) -> QIcon:
    pm = icon.pixmap(size)
    if pm.isNull():
        return icon
    out = pm.copy()
    painter = QPainter(out)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(out.rect(), QColor(color))
    painter.end()
    return QIcon(out)


def _nav_icon(fa_name: str, fallback: QStyle.StandardPixmap, size: QSize, color: str, style: QStyle) -> QIcon:
    icon = _fa_icon(fa_name, color=color)
    if icon is not None:
        return icon
    return _tinted(style.standardIcon(fallback), size=size, color=color)


def _repolish(w: QWidget) -> None:
    try:
        w.style().unpolish(w)
        w.style().polish(w)
        w.update()
    except Exception:
        pass


class OutlinedField(QWidget):
    """Campo com rótulo na borda superior (estilo outline/web)."""

    def __init__(self, label: str, editor: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("OutlinedFieldHost")
        self._editor = editor

        names = {
            QLineEdit: "OutlinedInner",
            QComboBox: "OutlinedInner",
            QSpinBox: "OutlinedInner",
            QTextEdit: "OutlinedInner",
        }
        for cls, obj_name in names.items():
            if isinstance(editor, cls):
                editor.setObjectName(obj_name)
                break

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 10, 0, 0)
        root.setSpacing(0)

        self._box = QFrame()
        self._box.setObjectName("OutlinedField")
        self._box.setAttribute(Qt.WA_StyledBackground, True)
        self._box.setProperty("focused", False)
        box_lay = QVBoxLayout(self._box)
        box_lay.setContentsMargins(12, 12, 12, 10)
        box_lay.setSpacing(0)
        box_lay.addWidget(editor)
        root.addWidget(self._box)

        self._legend = QLabel(label, self)
        self._legend.setObjectName("OutlinedFieldLabel")
        self._legend.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._legend.adjustSize()

        editor.installEventFilter(self)
        self._reposition_legend()

    @property
    def editor(self) -> QWidget:
        return self._editor

    def eventFilter(self, obj, event) -> bool:
        if obj is self._editor:
            if event.type() == QEvent.FocusIn:
                self._box.setProperty("focused", True)
                self._legend.setProperty("focused", True)
                _repolish(self._box)
                _repolish(self._legend)
            elif event.type() == QEvent.FocusOut:
                self._box.setProperty("focused", False)
                self._legend.setProperty("focused", False)
                _repolish(self._box)
                _repolish(self._legend)
        return super().eventFilter(obj, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reposition_legend()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._reposition_legend()

    def _reposition_legend(self) -> None:
        self._legend.adjustSize()
        box = self._box.geometry()
        y = box.y() - self._legend.height() // 2
        self._legend.move(box.x() + 14, y)
        self._legend.raise_()


class PreviewFrame(QLabel):
    """Área de preview inline (tela Gravar)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PreviewFrame")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._last_pixmap: QPixmap | None = None
        self.setText("Preview da gravação aparecerá aqui")

    def clear_preview(self) -> None:
        self._last_pixmap = None
        self.setPixmap(QPixmap())
        self.setText("Preview da gravação aparecerá aqui")

    def set_status(self, text: str) -> None:
        if self._last_pixmap is None:
            self.setPixmap(QPixmap())
            self.setText(text or "Preview da gravação aparecerá aqui")

    def set_frame(self, pixmap: QPixmap) -> None:
        self._last_pixmap = pixmap
        self.setText("")
        self._apply_scaled()

    def _apply_scaled(self) -> None:
        if self._last_pixmap is None:
            return
        pm = self._last_pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.setPixmap(pm)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_scaled()


def install_apk_phase_percent(
    done: int,
    total: int,
    *,
    has_post_install: bool,
) -> int:
    """Progresso só da instalação dos APKs. Com pós-config, teto 60%."""
    if total <= 0:
        return 0
    span = 60 if has_post_install else 100
    return min(span, int(round(span * done / total)))


def is_autostart_apk(entry: ApkEntry) -> bool:
    return entry.filename == "Autostart.apk" or entry.label.lower() == "autostart"


def wants_boot_autostart(entry: ApkEntry) -> bool:
    """Apps que entram no autoinício. Autostart e TTS não abrem sozinhos no boot."""
    if is_autostart_apk(entry):
        return False
    if entry.post_install == "tts_pt_br_voice_v":
        return False
    return True


def expand_install_dependencies(
    entries: list[ApkEntry],
    *,
    tts: ApkEntry | None,
    autostart: ApkEntry | None,
) -> list[ApkEntry]:
    """Painel puxa TTS; qualquer APK (exceto o próprio Autostart) puxa Autostart.apk."""
    if not entries:
        return entries

    has_painel = any(e.category == "Painel" for e in entries)
    has_non_autostart = any(not is_autostart_apk(e) for e in entries)
    tts_dep = tts if has_painel else None
    autostart_dep = autostart if has_non_autostart else None

    seen: set[tuple[str, str]] = set()
    out: list[ApkEntry] = []

    def add(e: ApkEntry | None) -> None:
        if e is None:
            return
        key = (e.category, e.filename)
        if key in seen:
            return
        seen.add(key)
        out.append(e)

    add(tts_dep)
    add(autostart_dep)
    for e in entries:
        add(e)
    return out


class MainWindow(QMainWindow):
    _install_phase_progress = Signal(int, str)
    _reboot_status = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(920, 820)
        self.setMinimumSize(900, 800)
        self._install_phase_progress.connect(self._on_install_phase_progress)
        self._reboot_status.connect(lambda m: self._append(self.txt_apps_out, m))

        self.bg = Background()
        self.adb: Adb | None = None
        self.devices: list[AdbDevice] = []
        self._last_device_status: str | None = None
        self.logcat_thread: LogcatThread | None = None
        self.record_thread: ScreenRecordThread | None = None
        self.audio_thread: DeviceAudioRecordThread | None = None
        self._audio_out_path: Path | None = None
        self.mic_thread: MicRecordThread | None = None
        self._mic_out_path: Path | None = None
        self._audio_expected: int = 0  # fontes de áudio que precisam estar prontas antes do vídeo
        self._audio_ready: int = 0
        self._mics: list = []
        self._audio_delay_s: float = 0.0
        self._record_started_at: float = 0.0
        self._retired_threads: list = []
        self._thread_reaper = QTimer(self)
        self._thread_reaper.setInterval(500)
        self._thread_reaper.timeout.connect(self._reap_threads)
        self._preview_token: int = 0
        self._quick_busy: set[str] = set()
        self._factory_reset_busy: bool = False
        self._reset_requested_at: float | None = None  # quando o reset foi pedido (janela de tolerância)
        self._reset_notified: bool = False
        self._closing: bool = False
        self._adb_initing: bool = False
        self._video_pending: bool = False
        self._pending_serial: str = ""
        self._pending_want_audio: bool = False
        self._autostart_auto: bool = False  # Autostart marcado automaticamente (não pelo usuário)
        self._last_apk_avail_msg: str = ""
        self.recording_state: str = "idle"
        self.recording_started_at: float | None = None
        self.recording_timer = QTimer(self)
        self.recording_timer.setInterval(250)
        self.recording_timer.timeout.connect(self._tick_recording_ui)
        self.preview_panel: PreviewFrame | None = None
        self.preview_serial: str | None = None
        self.preview_inflight: bool = False
        self.preview_last_refresh_ms: int = 0
        self.preview_errors: int = 0
        self.wifi_state: str = "disconnected"
        self.wifi_connected_address: str | None = None
        self.wifi_attempt_id: str | None = None
        self.wifi_last_error: str | None = None
        self.wifi_reconnect_failures: int = 0
        self.wifi_reconnect_inflight: bool = False
        self.wifi_monitor_timer = QTimer(self)
        self.wifi_monitor_timer.setInterval(10000)
        self.wifi_monitor_timer.timeout.connect(self._wifi_monitor_tick)

        self.sidebar: QFrame | None = None
        self.sidebar_collapsed: bool = False

        self.output_dir = Path.home() / APP_DIR_NAME / "Capturas"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.apks_dir = ensure_apks_tree()
        self._install_queue: list[ApkEntry] = []
        self._install_busy: bool = False
        self._reboot_busy: bool = False
        self._install_reboot_serial: str | None = None
        self._install_reboot_pkgs: list[str] = []
        self._install_total: int = 0
        self._install_done: int = 0
        self._install_progress_anim: QPropertyAnimation | None = None
        self._install_boot_packages: list[str] = []
        self._install_boot_labels: dict[str, str] = {}
        self._install_configure_boot: bool = False
        self._install_need_tts_config: bool = False
        self._install_tts_apk_ok: bool = False
        self._install_post_pending: bool = False
        self._uninstall_rows: list[tuple[InstalledApp, ToggleRow]] = []
        self._uninstall_busy: bool = False
        self._uninstall_queue: list[InstalledApp] = []
        self._uninstall_total: int = 0
        self._uninstall_done: int = 0
        self._uninstall_progress_anim: QPropertyAnimation | None = None
        self._optimize_busy: bool = False
        self._apk_sync_busy: bool = False

        self._startup_done = False
        self._build_ui()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._startup_done:
            return
        self._startup_done = True
        # Depois da janela aparecer: ADB (sem bloquear nem abrir console).
        QTimer.singleShot(0, self._init_adb)
        QTimer.singleShot(400, self._auto_sync_apks)
        QTimer.singleShot(900, self.refresh_microphones)
        # Depois de rodar com sucesso por um tempo, o backup da versão anterior pode sair.
        QTimer.singleShot(45000, self._cleanup_after_update)

    def _build_ui(self) -> None:
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        self.sidebar = sidebar
        sidebar.setFixedWidth(196)
        sidebar_lay = QVBoxLayout(sidebar)
        sidebar_lay.setContentsMargins(0, 10, 0, 10)
        sidebar_lay.setSpacing(2)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        self.pages = QStackedWidget()
        self.pages.setObjectName("Pages")

        self.nav_buttons: list[QToolButton] = []
        self._nav_animators: list[NavIconAnimator] = []
        self._nav_section_labels: list[QWidget] = []
        # Ordem da sidebar (índice = página)
        self._page_titles = [
            ("Conectar", "Conexão USB e Wi‑Fi com o dispositivo"),
            ("DPI", "Densidade de tela do dispositivo"),
            ("Instalar", "Catálogo de aplicativos APK"),
            ("Desinstalar", "Remover aplicativos do dispositivo"),
            ("Debug", "Logs do sistema em tempo real"),
            ("Otimizar", "Limpeza e desempenho do aparelho"),
            ("Restauração", "Restauração de fábrica do dispositivo"),
            ("Gravar", "Captura de tela em vídeo"),
            ("Print", "Captura de tela em PNG"),
            ("Sobre", "Sobre o Aibox e a Intelite"),
        ]
        self._add_nav_page("Conectar", "fa5s.plug", QStyle.SP_DriveNetIcon, self._tab_connect())
        self._add_nav_page("DPI", "fa5s.expand", QStyle.SP_DesktopIcon, self._tab_dpi())
        self._add_nav_page("Instalar", "fa5s.download", QStyle.SP_DriveHDIcon, self._tab_apps())
        self._add_nav_page("Desinstalar", "fa5s.trash-alt", QStyle.SP_TrashIcon, self._tab_uninstall())
        self._add_nav_page("Debug", "fa5s.bug", QStyle.SP_FileDialogDetailedView, self._tab_logs())
        self._add_nav_page("Otimizar", "fa5s.bolt", QStyle.SP_BrowserReload, self._tab_optimize())
        self._add_nav_page("Restauração", "fa5s.undo", QStyle.SP_BrowserReload, self._tab_factory_reset())
        self._add_nav_page("Gravar", "fa5s.video", QStyle.SP_MediaPlay, self._tab_record())
        self._add_nav_page("Print", "fa5s.camera", QStyle.SP_ComputerIcon, self._tab_print())
        self._add_nav_page("Sobre", "fa5s.info-circle", QStyle.SP_MessageBoxInformation, self._tab_about())

        by_name = {str(b.property("fullText")): b for b in self.nav_buttons}
        sidebar_lay.setSpacing(4)
        sidebar_lay.setContentsMargins(8, 12, 8, 10)

        def add_section(title: str) -> QVBoxLayout:
            """Cabeçalho + grupo visual; retorna o layout onde entram os botões."""
            block = QFrame()
            block.setObjectName("SidebarSectionBlock")
            block_lay = QVBoxLayout(block)
            block_lay.setContentsMargins(0, 10, 0, 0)
            block_lay.setSpacing(6)

            head = QFrame()
            head.setObjectName("SidebarSectionHead")
            head_lay = QVBoxLayout(head)
            head_lay.setContentsMargins(8, 0, 8, 0)
            head_lay.setSpacing(6)

            rule = QFrame()
            rule.setObjectName("SidebarSectionRule")
            rule.setFixedHeight(1)
            head_lay.addWidget(rule)

            lbl = QLabel(title.upper())
            lbl.setObjectName("SidebarSection")
            lbl.setProperty("fullText", title.upper())
            head_lay.addWidget(lbl)
            block_lay.addWidget(head)

            group = QFrame()
            group.setObjectName("SidebarNavGroup")
            group_lay = QVBoxLayout(group)
            group_lay.setContentsMargins(4, 4, 4, 4)
            group_lay.setSpacing(2)
            block_lay.addWidget(group)

            self._nav_section_labels.append(block)
            sidebar_lay.addWidget(block)
            return group_lay

        def add_nav(name: str, target_lay: QVBoxLayout | None = None) -> None:
            btn = by_name.get(name)
            if btn is None:
                return
            if target_lay is not None:
                target_lay.addWidget(btn)
            else:
                sidebar_lay.addWidget(btn)

        # Conectar (sozinho acima)
        add_nav("Conectar")

        # Configuração
        cfg = add_section("Configuração")
        add_nav("DPI", cfg)
        add_nav("Instalar", cfg)
        add_nav("Desinstalar", cfg)

        # Manutenção
        man = add_section("Manutenção")
        add_nav("Debug", man)
        add_nav("Otimizar", man)
        add_nav("Restauração", man)

        # Tela
        tela = add_section("Tela")
        add_nav("Gravar", tela)
        add_nav("Print", tela)

        sidebar_lay.addStretch(1)

        # Sobre (sozinho abaixo, acima do rodapé)
        sobre_wrap = QFrame()
        sobre_wrap.setObjectName("SidebarSobreWrap")
        sobre_lay = QVBoxLayout(sobre_wrap)
        sobre_lay.setContentsMargins(0, 6, 0, 4)
        sobre_lay.setSpacing(0)
        rule_sobre = QFrame()
        rule_sobre.setObjectName("SidebarSectionRule")
        rule_sobre.setFixedHeight(1)
        sobre_lay.addWidget(rule_sobre)
        sobre_lay.addSpacing(6)
        add_nav("Sobre", sobre_lay)
        self._nav_section_labels.append(sobre_wrap)
        sidebar_lay.addWidget(sobre_wrap)

        side_footer = QWidget()
        side_footer.setObjectName("PageRoot")
        footer_lay = QVBoxLayout(side_footer)
        footer_lay.setContentsMargins(12, 8, 12, 4)
        footer_lay.setSpacing(6)

        intelite_logo = QLabel()
        intelite_logo.setObjectName("SidebarFooterLogo")
        intelite_logo.setAlignment(Qt.AlignHCenter)
        logo_path = resource_path("Logo para tema escuro.png")
        if logo_path.exists():
            pm = QPixmap(str(logo_path))
            if not pm.isNull():
                intelite_logo.setPixmap(
                    pm.scaled(140, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
        footer_lay.addWidget(intelite_logo)

        side_ver = QLabel(f"v{APP_VERSION}")
        side_ver.setObjectName("BrandSub")
        side_ver.setAlignment(Qt.AlignHCenter)
        footer_lay.addWidget(side_ver)
        sidebar_lay.addWidget(side_footer)

        self.nav_group.idClicked.connect(self._on_nav_changed)
        if self.nav_buttons:
            self.nav_buttons[0].setChecked(True)
            self.pages.setCurrentIndex(0)

        for i in range(min(9, len(self.nav_buttons))):
            act = QAction(self)
            act.setShortcut(f"Alt+{i + 1}")
            act.triggered.connect(lambda _checked=False, idx=i: self._nav_jump(idx))
            self.addAction(act)

        main = QFrame()
        main.setObjectName("MainArea")
        main_lay = QVBoxLayout(main)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        self.page_header = PageHeader(self._page_titles[0][0], self._page_titles[0][1])
        self.lbl_page_title = self.page_header.lbl_title
        self.lbl_page_sub = self.page_header.lbl_sub
        main_lay.addWidget(self.page_header, 0)
        main_lay.addWidget(self.pages, 1)

        root.addWidget(sidebar, 0)
        root.addWidget(main, 1)

        self._set_wifi_state("disconnected")

        self.setCentralWidget(central)
        self.menuBar().setVisible(False)
        self._apply_sidebar_collapsed(self.width() < 700)

    def _on_nav_changed(self, idx: int) -> None:
        self.pages.setCurrentIndex(idx)
        if 0 <= idx < len(self._page_titles):
            title, sub = self._page_titles[idx]
            self.page_header.set_texts(title, sub, animate=True)
        if 0 <= idx < len(self._nav_animators):
            self._nav_animators[idx].play()

    def _add_nav_page(self, text: str, fa_icon: str, fallback: QStyle.StandardPixmap, page: QWidget) -> None:
        idx = self.pages.count()
        self.pages.addWidget(page)

        btn = QToolButton()
        btn.setObjectName("NavButton")
        btn.setProperty("nav", True)
        btn.setProperty("fullText", text)
        btn.setCheckable(True)
        btn.setAutoRaise(True)
        btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        btn.setText(text)
        btn.setIconSize(QSize(18, 18))
        btn.setFocusPolicy(Qt.StrongFocus)
        btn.setAccessibleName(text)
        btn.setToolTip(text)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn.setCursor(Qt.PointingHandCursor)

        animator = NavIconAnimator(
            btn,
            fa_name=fa_icon,
            fallback=fallback,
            color=COLOR_ICON,
            size=QSize(18, 18),
            icon_factory=_nav_icon,
            parent=self,
        )
        self.nav_group.addButton(btn, idx)
        self.nav_buttons.append(btn)
        self._nav_animators.append(animator)

    def _nav_jump(self, idx: int) -> None:
        if not hasattr(self, "nav_buttons") or not hasattr(self, "pages"):
            return
        if idx < 0 or idx >= len(self.nav_buttons):
            return
        self.nav_buttons[idx].setChecked(True)
        self._on_nav_changed(idx)
        self.nav_buttons[idx].setFocus()

    def _apply_sidebar_collapsed(self, collapsed: bool) -> None:
        if self.sidebar is None:
            return
        if collapsed == self.sidebar_collapsed and self.sidebar.width() in (64, 196):
            return
        self.sidebar_collapsed = collapsed

        if collapsed:
            self.sidebar.setFixedWidth(64)
            for btn in self.nav_buttons:
                btn.setText("")
                btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
                btn.setMinimumHeight(40)
            for w in getattr(self, "_nav_section_labels", []):
                # Mantém o bloco, mas esconde só títulos/regras
                for child in w.findChildren(QLabel, "SidebarSection"):
                    child.setVisible(False)
                for child in w.findChildren(QFrame, "SidebarSectionRule"):
                    child.setVisible(False)
                for child in w.findChildren(QFrame, "SidebarSectionHead"):
                    child.setVisible(False)
        else:
            self.sidebar.setFixedWidth(196)
            for btn in self.nav_buttons:
                full = btn.property("fullText")
                btn.setText(str(full) if full else btn.toolTip())
                btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
                btn.setMinimumHeight(38)
            for w in getattr(self, "_nav_section_labels", []):
                for child in w.findChildren(QLabel, "SidebarSection"):
                    child.setVisible(True)
                for child in w.findChildren(QFrame, "SidebarSectionRule"):
                    child.setVisible(True)
                for child in w.findChildren(QFrame, "SidebarSectionHead"):
                    child.setVisible(True)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_sidebar_collapsed(self.width() < 700)

    def _make_page(self) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        w.setObjectName("PageRoot")
        w.setAttribute(Qt.WA_StyledBackground, True)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        return w, lay

    def _make_card(self) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("ContentCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(12)
        return card, lay

    def _wrap_card(self, card: QFrame) -> QWidget:
        wrap = QWidget()
        wrap.setObjectName("PageRoot")
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(18, 8, 18, 8)
        lay.setSpacing(0)
        lay.addWidget(card, 1)
        return wrap

    def _make_action_bar(self, *buttons: QPushButton) -> QFrame:
        bar = QFrame()
        bar.setObjectName("ActionBar")
        bar.setFixedHeight(56)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 8, 18, 8)
        lay.setSpacing(8)
        lay.addStretch(1)
        for btn in buttons:
            btn.setMinimumWidth(112)
            lay.addWidget(btn, 0)
        return bar

    def _field_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("FieldLabel")
        lbl.setMinimumWidth(72)
        lbl.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        return lbl

    def _outlined(self, label: str, editor: QWidget) -> OutlinedField:
        return OutlinedField(label, editor)

    def _page_tip(self, text: str) -> QLabel:
        tip = QLabel(text)
        tip.setObjectName("PageTip")
        tip.setWordWrap(True)
        tip.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        return tip

    def _info_panel(self, text: str) -> QFrame:
        """Bloco de apoio padronizado (avisos, lista de recursos, etc.)."""
        panel = QFrame()
        panel.setObjectName("InfoPanel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(0)
        body = QLabel(text)
        body.setObjectName("InfoPanelText")
        body.setWordWrap(True)
        lay.addWidget(body)
        return panel

    def _status_box(
        self,
        placeholder: str,
        *,
        tall: bool = False,
        compact: bool = False,
        fill: bool = False,
    ) -> QTextEdit:
        box = QTextEdit()
        box.setReadOnly(True)
        box.setPlaceholderText(placeholder)
        if fill:
            box.setMinimumHeight(140)
            box.setMaximumHeight(16777215)
            box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        elif compact:
            box.setMinimumHeight(52)
            box.setMaximumHeight(72)
        elif tall:
            box.setMinimumHeight(120)
            box.setMaximumHeight(200)
        else:
            box.setMinimumHeight(72)
            box.setMaximumHeight(110)
        return box

    def _section_title(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("SectionTitle")
        return lbl

    def _add_progress_row(self, progress: QProgressBar, pct: QLabel) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(10)
        progress.setObjectName("InstallProgress")
        progress.setTextVisible(False)
        progress.setFixedHeight(12)
        progress.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        pct.setObjectName("InstallProgressPct")
        pct.setMinimumWidth(36)
        pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(progress, 1)
        row.addWidget(pct, 0)
        return row

    def _tab_connect(self) -> QWidget:
        w, lay = self._make_page()
        card, card_lay = self._make_card()
        card_lay.addWidget(self._section_title("Conexão de dispositivos"))
        card_lay.addWidget(self._page_tip("Conecte por USB ou Wi‑Fi e selecione o dispositivo ativo."))

        fields = QVBoxLayout()
        fields.setSpacing(16)
        fields.setContentsMargins(0, 4, 0, 0)

        self.cmb_device = QComboBox()
        self.cmb_device.setMinimumContentsLength(28)
        self.cmb_device.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_device.setPlaceholderText("Selecione um dispositivo")
        self.cmb_device.activated.connect(self._on_device_chosen)

        self.btn_refresh = QPushButton("Atualizar")
        self.btn_refresh.setObjectName("SecondaryButton")
        self.btn_refresh.clicked.connect(self.refresh_devices)

        self.txt_wifi = QLineEdit()
        self.txt_wifi.setPlaceholderText("IP:PORTA (ex: 192.168.0.10:5555)")
        self.txt_wifi.setClearButtonEnabled(True)
        self.txt_wifi.textChanged.connect(self._on_wifi_text_changed)

        self.btn_wifi_toggle = QPushButton()
        self.btn_wifi_toggle.clicked.connect(self.toggle_wifi)

        self.btn_wifi_pair = QPushButton("Parear…")
        self.btn_wifi_pair.setObjectName("SecondaryButton")
        self.btn_wifi_pair.setToolTip(
            "Depuração sem fio (Android 11+): pareia com o código de 6 dígitos "
            "mostrado em «Parear dispositivo com código»."
        )
        self.btn_wifi_pair.clicked.connect(self.pair_wifi)

        fields.addWidget(self._outlined("Dispositivo", self.cmb_device))
        fields.addWidget(self._outlined("Wi‑Fi", self.txt_wifi))
        card_lay.addLayout(fields)

        self.txt_connect_out = self._status_box("Status da conexão…")
        card_lay.addWidget(self.txt_connect_out)
        card_lay.addStretch(1)

        lay.addWidget(self._wrap_card(card), 1)
        lay.addWidget(self._make_action_bar(self.btn_refresh, self.btn_wifi_pair, self.btn_wifi_toggle))
        return w

    def _tab_dpi(self) -> QWidget:
        w, lay = self._make_page()
        card, card_lay = self._make_card()
        card_lay.addWidget(self._section_title("Densidade da tela"))
        card_lay.addWidget(self._page_tip("Ajuste o DPI do dispositivo. Valor sugerido: 160."))

        row = QHBoxLayout()
        row.setSpacing(10)
        self.spn_dpi = QSpinBox()
        self.spn_dpi.setRange(72, 640)
        self.spn_dpi.setValue(160)
        self.spn_dpi.setSuffix(" dpi")
        self.spn_dpi.setMinimumWidth(140)
        self.spn_dpi.setMinimumHeight(28)
        self.spn_dpi.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.spn_dpi.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        step_col = QVBoxLayout()
        step_col.setContentsMargins(0, 8, 0, 0)
        step_col.setSpacing(4)
        self.btn_dpi_up = QToolButton()
        self.btn_dpi_up.setObjectName("SpinStepButton")
        self.btn_dpi_up.setText("▲")
        self.btn_dpi_up.setCursor(Qt.PointingHandCursor)
        self.btn_dpi_up.clicked.connect(self.spn_dpi.stepUp)
        self.btn_dpi_down = QToolButton()
        self.btn_dpi_down.setObjectName("SpinStepButton")
        self.btn_dpi_down.setText("▼")
        self.btn_dpi_down.setCursor(Qt.PointingHandCursor)
        self.btn_dpi_down.clicked.connect(self.spn_dpi.stepDown)
        step_col.addWidget(self.btn_dpi_up)
        step_col.addWidget(self.btn_dpi_down)

        row.addWidget(self._outlined("DPI", self.spn_dpi), 0)
        row.addLayout(step_col)
        row.addStretch(1)
        card_lay.addLayout(row)

        self.txt_dpi_out = self._status_box("Resultado do ajuste…")
        card_lay.addWidget(self.txt_dpi_out)
        card_lay.addStretch(1)

        self.btn_dpi_reset = QPushButton("Restaurar padrão")
        self.btn_dpi_reset.setObjectName("SecondaryButton")
        self.btn_dpi_reset.clicked.connect(self.reset_dpi)
        self.btn_dpi_apply = QPushButton("Aplicar DPI")
        self.btn_dpi_apply.setObjectName("PrimaryButton")
        self.btn_dpi_apply.clicked.connect(self.apply_dpi)

        lay.addWidget(self._wrap_card(card), 1)
        lay.addWidget(self._make_action_bar(self.btn_dpi_reset, self.btn_dpi_apply))
        return w

    def _tab_record(self) -> QWidget:
        w, lay = self._make_page()
        card, card_lay = self._make_card()
        card_lay.addWidget(self._section_title("Gravação de tela"))
        card_lay.addWidget(
            self._page_tip(
                "Grave a tela do Mini PC conectado. O áudio é o som reproduzido no equipamento (HDMI), não o microfone do Windows."
            )
        )

        self.txt_out_gravar = QLineEdit(str(self.output_dir))
        self.btn_out_gravar = QPushButton("Trocar…")
        self.btn_out_gravar.setObjectName("SecondaryButton")
        self.btn_out_gravar.clicked.connect(self.pick_output_dir)
        self.btn_open_output_gravar = QPushButton("Abrir pasta")
        self.btn_open_output_gravar.setObjectName("SecondaryButton")
        self.btn_open_output_gravar.clicked.connect(self._open_output_dir)
        out_row = QHBoxLayout()
        out_row.setSpacing(10)
        out_row.addWidget(self._outlined("Pasta", self.txt_out_gravar), 1)
        out_row.addWidget(self.btn_out_gravar, 0, Qt.AlignVCenter)
        card_lay.addLayout(out_row)

        audio_row = QHBoxLayout()
        audio_row.setSpacing(10)
        self.chk_record_audio = ToggleSwitch()
        self.chk_record_audio.setChecked(True)
        self.chk_record_audio.setToolTip(
            "Captura o áudio reproduzido no Mini PC (saída HDMI) e inclui no mesmo arquivo MP4. "
            "Android 11+: nativo (scrcpy). Android 10: usa um aplicativo auxiliar (sndcpy) instalado "
            "automaticamente no aparelho."
        )
        self.lbl_record_audio = QLabel("Áudio do Mini PC")
        self.lbl_record_audio.setObjectName("ToggleRowLabel")
        audio_row.addWidget(self.chk_record_audio, 0, Qt.AlignVCenter)
        audio_row.addWidget(self.lbl_record_audio, 0, Qt.AlignVCenter)
        audio_row.addStretch(1)
        card_lay.addLayout(audio_row)

        mic_row = QHBoxLayout()
        mic_row.setSpacing(10)
        self.chk_record_mic = ToggleSwitch()
        self.chk_record_mic.setChecked(False)
        self.chk_record_mic.setToolTip(
            "Grava também o microfone do COMPUTADOR junto com o vídeo (narração, por exemplo). "
            "Se o áudio do Mini PC também estiver ligado, os dois são misturados na mesma faixa do MP4."
        )
        self.lbl_record_mic = QLabel("Microfone do computador")
        self.lbl_record_mic.setObjectName("ToggleRowLabel")
        self.cmb_mic = QComboBox()
        self.cmb_mic.setMinimumContentsLength(28)
        self.cmb_mic.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_mic.setEnabled(False)
        self.btn_refresh_mics = QPushButton("Atualizar")
        self.btn_refresh_mics.setObjectName("SecondaryButton")
        self.btn_refresh_mics.clicked.connect(self.refresh_microphones)
        mic_row.addWidget(self.chk_record_mic, 0, Qt.AlignVCenter)
        mic_row.addWidget(self.lbl_record_mic, 0, Qt.AlignVCenter)
        mic_row.addWidget(self._outlined("Microfone", self.cmb_mic), 1)
        mic_row.addWidget(self.btn_refresh_mics, 0, Qt.AlignVCenter)
        card_lay.addLayout(mic_row)
        self.chk_record_mic.toggled.connect(self._on_mic_toggled)

        self.txt_gravar_out = self._status_box("Status da gravação…", compact=True)
        card_lay.addWidget(self.txt_gravar_out)

        self.preview_panel = PreviewFrame()
        card_lay.addWidget(self.preview_panel, 1)

        timer_bar = QFrame()
        timer_bar.setObjectName("RecordTimerBar")
        self.record_timer_bar = timer_bar
        timer_lay = QHBoxLayout(timer_bar)
        timer_lay.setContentsMargins(14, 10, 14, 10)
        timer_lay.setSpacing(12)
        self.lbl_rec_indicator = QLabel("REC")
        self.lbl_rec_indicator.setObjectName("RecordRecBadge")
        self.lbl_rec_indicator.setAlignment(Qt.AlignCenter)
        self.lbl_record_time = QLabel("00:00")
        self.lbl_record_time.setObjectName("RecordTimerValue")
        self.lbl_record_time.setAlignment(Qt.AlignCenter)
        timer_lay.addWidget(self.lbl_rec_indicator, 0, Qt.AlignVCenter)
        timer_lay.addStretch(1)
        timer_lay.addWidget(self.lbl_record_time, 0, Qt.AlignVCenter)
        timer_lay.addStretch(1)
        # Espaçador simétrico ao badge para centralizar o tempo
        spacer = QLabel("")
        spacer.setFixedWidth(44)
        timer_lay.addWidget(spacer, 0)
        card_lay.addWidget(timer_bar)
        self._set_record_timer_active(False)

        self.btn_record_toggle = QPushButton()
        self.btn_record_toggle.setObjectName("PrimaryButton")
        self.btn_record_toggle.clicked.connect(self.toggle_recording)
        self.btn_record_toggle.setMinimumWidth(180)

        lay.addWidget(self._wrap_card(card), 1)
        lay.addWidget(self._make_action_bar(self.btn_open_output_gravar, self.btn_record_toggle))
        self._set_recording_state("idle")
        return w

    def _tab_print(self) -> QWidget:
        w, lay = self._make_page()
        card, card_lay = self._make_card()
        card_lay.addWidget(self._section_title("Captura de tela"))
        card_lay.addWidget(self._page_tip("Tire um print PNG do dispositivo conectado."))

        self.txt_out_print = QLineEdit(str(self.output_dir))
        self.btn_out_print = QPushButton("Trocar…")
        self.btn_out_print.setObjectName("SecondaryButton")
        self.btn_out_print.clicked.connect(self.pick_output_dir)
        self.btn_open_output_print = QPushButton("Abrir pasta")
        self.btn_open_output_print.setObjectName("SecondaryButton")
        self.btn_open_output_print.clicked.connect(self._open_output_dir)
        out_row = QHBoxLayout()
        out_row.setSpacing(10)
        out_row.addWidget(self._outlined("Pasta", self.txt_out_print), 1)
        out_row.addWidget(self.btn_out_print, 0, Qt.AlignVCenter)
        card_lay.addLayout(out_row)

        self.txt_print_out = self._status_box("Status da captura…")
        card_lay.addWidget(self.txt_print_out)
        card_lay.addStretch(1)

        self.btn_screenshot = QPushButton("Tirar print (PNG)")
        self.btn_screenshot.setObjectName("PrimaryButton")
        self.btn_screenshot.clicked.connect(self.take_screenshot)

        lay.addWidget(self._wrap_card(card), 1)
        lay.addWidget(self._make_action_bar(self.btn_open_output_print, self.btn_screenshot))
        return w

    def _tab_apps(self) -> QWidget:
        w, lay = self._make_page()
        card, card_lay = self._make_card()
        card_lay.addWidget(self._section_title("Instalar aplicativos"))
        card_lay.addWidget(
            self._page_tip("Abra as categorias, ative os APKs desejados e clique em Instalar.")
        )
        card_lay.addWidget(
            self._info_panel(
                "• Qualquer APK marca Autostart automaticamente (autoinício)\n"
                "• Painel marca/desmarca Síntese de Voz automaticamente\n"
                "• Síntese de Voz: instala o APK e configura pt-BR / Voz V\n"
                "• APKs são baixados do repositório de releases (não vêm no instalador)"
            )
        )

        self._apk_rows: dict[str, list[tuple[ApkEntry, ToggleRow]]] = {}
        self._apk_accordion_btns: dict[str, QToolButton] = {}
        self._apk_accordion_bodies: dict[str, QFrame] = {}

        scroll = QScrollArea()
        scroll.setObjectName("ApkScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        scroll_inner = QWidget()
        scroll_inner.setObjectName("ApkScrollInner")
        accordion = QVBoxLayout(scroll_inner)
        accordion.setSpacing(4)
        accordion.setContentsMargins(0, 0, 2, 0)

        for cat in APK_CATEGORIES:
            btn = QToolButton()
            btn.setObjectName("AccordionButton")
            btn.setText(cat)
            btn.setCheckable(True)
            btn.setChecked(True)
            btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            btn.setArrowType(Qt.DownArrow)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setMinimumHeight(28)
            btn.setMaximumHeight(30)
            btn.setFont(self.font())

            body = QFrame()
            body.setObjectName("AccordionBody")
            body.setVisible(True)
            body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            grid = QGridLayout(body)
            grid.setContentsMargins(4, 2, 4, 2)
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(2)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)

            rows: list[tuple[ApkEntry, ToggleRow]] = []
            # Grade 2 colunas (até 3 linhas ≈ 6 apps por categoria)
            for i, entry in enumerate(entries_for(cat)):
                row_w = ToggleRow(entry.label)
                row_w.setMinimumHeight(28)
                row_w.setMaximumHeight(30)
                row_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                row_w.toggle.toggled.connect(
                    lambda checked=False, category=cat, e=entry: self._on_apk_toggled(category, e, checked)
                )
                r, c = divmod(i, 2)
                grid.addWidget(row_w, r, c, Qt.AlignVCenter)
                rows.append((entry, row_w))
            self._apk_rows[cat] = rows
            self._apk_accordion_btns[cat] = btn
            self._apk_accordion_bodies[cat] = body

            btn.toggled.connect(lambda checked, c=cat: self._toggle_apk_category(c, checked))
            accordion.addWidget(btn)
            accordion.addWidget(body)

        accordion.addStretch(1)
        scroll.setWidget(scroll_inner)
        card_lay.addWidget(scroll, 1)

        self.progress_apps = QProgressBar()
        self.progress_apps.setRange(0, 100)
        self.progress_apps.setValue(0)
        self.lbl_apps_pct = QLabel("0%")
        card_lay.addLayout(self._add_progress_row(self.progress_apps, self.lbl_apps_pct))

        self._install_progress_anim = QPropertyAnimation(self.progress_apps, b"value", self)
        self._install_progress_anim.setDuration(380)
        self._install_progress_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._install_progress_anim.valueChanged.connect(self._on_install_progress_frame)

        self.txt_apps_out = self._status_box("Resultado das instalações…", compact=True)
        card_lay.addWidget(self.txt_apps_out)

        self.chk_reboot_after = ToggleRow("Reiniciar o equipamento ao final (valida o Autostart)")
        self.chk_reboot_after.setChecked(True)
        self.chk_reboot_after.setToolTip(
            "Depois de instalar e configurar o Autostart, reinicia o aparelho e confere se o app abre sozinho."
        )
        card_lay.addWidget(self.chk_reboot_after)

        self.btn_open_apks = QPushButton("Abrir pasta Apks")
        self.btn_open_apks.setObjectName("SecondaryButton")
        self.btn_open_apks.clicked.connect(self._open_apks_dir)
        self.btn_refresh_apks = QPushButton("Atualizar lista")
        self.btn_refresh_apks.setObjectName("SecondaryButton")
        self.btn_refresh_apks.clicked.connect(self.refresh_apk_availability)
        self.btn_sync_apks = QPushButton("Baixar APKs")
        self.btn_sync_apks.setObjectName("SecondaryButton")
        self.btn_sync_apks.clicked.connect(self.sync_apks_from_repo)
        self.btn_install = QPushButton("Instalar selecionados")
        self.btn_install.setObjectName("PrimaryButton")
        self.btn_install.clicked.connect(self.install_selected_apks)

        lay.addWidget(self._wrap_card(card), 1)
        lay.addWidget(
            self._make_action_bar(
                self.btn_open_apks, self.btn_refresh_apks, self.btn_sync_apks, self.btn_install
            )
        )
        self.refresh_apk_availability()
        return w

    def _tab_uninstall(self) -> QWidget:
        w, lay = self._make_page()
        card, card_lay = self._make_card()
        card_lay.addWidget(self._section_title("Desinstalar aplicativos"))
        card_lay.addWidget(
            self._page_tip(
                "Lista os apps instalados com o nome que aparece na tela. "
                "Por padrão mostra apenas apps de terceiros."
            )
        )

        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.edt_uninstall_filter = QLineEdit()
        self.edt_uninstall_filter.setPlaceholderText("Filtrar por nome ou pacote…")
        self.edt_uninstall_filter.textChanged.connect(self._filter_uninstall_list)
        tools.addWidget(self.edt_uninstall_filter, 1)
        self.chk_uninstall_system = ToggleRow("Incluir apps do sistema")
        self.chk_uninstall_system.setMaximumWidth(220)
        self.chk_uninstall_system.setChecked(False)
        tools.addWidget(self.chk_uninstall_system, 0)
        card_lay.addLayout(tools)

        scroll = QScrollArea()
        scroll.setObjectName("ApkScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._uninstall_list_host = QWidget()
        self._uninstall_list_host.setObjectName("ApkScrollInner")
        self._uninstall_list_lay = QVBoxLayout(self._uninstall_list_host)
        self._uninstall_list_lay.setContentsMargins(2, 2, 2, 2)
        self._uninstall_list_lay.setSpacing(2)
        self._uninstall_list_lay.addStretch(1)
        scroll.setWidget(self._uninstall_list_host)
        card_lay.addWidget(scroll, 1)

        self.progress_uninstall = QProgressBar()
        self.progress_uninstall.setRange(0, 100)
        self.progress_uninstall.setValue(0)
        self.lbl_uninstall_pct = QLabel("0%")
        card_lay.addLayout(self._add_progress_row(self.progress_uninstall, self.lbl_uninstall_pct))

        self._uninstall_progress_anim = QPropertyAnimation(self.progress_uninstall, b"value", self)
        self._uninstall_progress_anim.setDuration(380)
        self._uninstall_progress_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._uninstall_progress_anim.valueChanged.connect(self._on_uninstall_progress_frame)

        self.txt_uninstall_out = self._status_box(
            "Atualize a lista com o dispositivo conectado…",
            compact=True,
        )
        card_lay.addWidget(self.txt_uninstall_out)

        self.btn_uninstall_refresh = QPushButton("Atualizar lista")
        self.btn_uninstall_refresh.setObjectName("SecondaryButton")
        self.btn_uninstall_refresh.clicked.connect(self.refresh_uninstall_apps)
        self.btn_uninstall_run = QPushButton("Desinstalar selecionados")
        self.btn_uninstall_run.setObjectName("DangerButton")
        self.btn_uninstall_run.clicked.connect(self.uninstall_selected_apps)

        lay.addWidget(self._wrap_card(card), 1)
        lay.addWidget(self._make_action_bar(self.btn_uninstall_refresh, self.btn_uninstall_run))
        return w

    def _tab_optimize(self) -> QWidget:
        w, lay = self._make_page()
        card, card_lay = self._make_card()
        card_lay.addWidget(self._section_title("Otimizar dispositivo"))
        card_lay.addWidget(
            self._page_tip(
                "Executa uma otimização completa no aparelho conectado — "
                "sem apagar fotos ou dados dos apps."
            )
        )
        card_lay.addWidget(
            self._info_panel(
                "• Cache de aplicativos (pm trim-caches)\n"
                "• Temporários e caches em armazenamento compartilhado\n"
                "• Processos em segundo plano (memória RAM)\n"
                "• Dexopt / compilação (com fallback se o firmware falhar)\n"
                "• Varredura de FATAL/ANR no logcat\n"
                "• Relatório com variação real de disco/RAM"
            )
        )

        self.progress_optimize = QProgressBar()
        self.progress_optimize.setObjectName("InstallProgress")
        self.progress_optimize.setRange(0, 0)
        self.progress_optimize.setValue(0)
        self.progress_optimize.setTextVisible(False)
        self.progress_optimize.setFixedHeight(12)
        self.progress_optimize.setVisible(False)
        card_lay.addWidget(self.progress_optimize)

        self.txt_optimize_out = self._status_box(
            "Toque em Otimizar agora com o dispositivo conectado…",
            tall=True,
        )
        card_lay.addWidget(self.txt_optimize_out)
        card_lay.addStretch(1)

        self.btn_optimize = QPushButton("Otimizar agora")
        self.btn_optimize.setObjectName("PrimaryButton")
        self.btn_optimize.clicked.connect(self.start_optimize_device)

        lay.addWidget(self._wrap_card(card), 1)
        lay.addWidget(self._make_action_bar(self.btn_optimize))
        return w

    def _clear_uninstall_list(self) -> None:
        while self._uninstall_list_lay.count():
            item = self._uninstall_list_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._uninstall_rows = []
        self._uninstall_list_lay.addStretch(1)

    def _filter_uninstall_list(self, text: str = "") -> None:
        q = (text or self.edt_uninstall_filter.text() or "").strip().lower()
        for app, row in self._uninstall_rows:
            if not q:
                row.setVisible(True)
                continue
            hay = f"{app.label} {app.package} {app.version_name or ''}".lower()
            row.setVisible(q in hay)

    @Slot()
    def refresh_uninstall_apps(self) -> None:
        serial = self._require_serial()
        if not serial or not self.adb:
            return
        if self._uninstall_busy:
            QMessageBox.information(self, APP_NAME, "Aguarde a operação em andamento.")
            return
        include_system = self.chk_uninstall_system.isChecked()
        self.btn_uninstall_refresh.setEnabled(False)
        self.btn_uninstall_run.setEnabled(False)
        self._append(self.txt_uninstall_out, "Lendo aplicativos instalados (nomes reais)…")

        def fn() -> list[InstalledApp]:
            assert self.adb is not None
            return self.adb.list_installed_apps(serial, include_system=include_system)

        def ok(apps: list[InstalledApp]) -> None:
            self._clear_uninstall_list()
            # remove stretch, add rows, re-add stretch
            while self._uninstall_list_lay.count():
                self._uninstall_list_lay.takeAt(0)
            for app in apps:
                title = app.display
                if app.version_name:
                    title = f"{title}  ·  v{app.version_name}"
                if app.system:
                    title = f"{title}  (sistema)"
                row = ToggleRow(title)
                row.setMinimumHeight(28)
                row.setMaximumHeight(32)
                row.setToolTip(app.package)
                self._uninstall_list_lay.addWidget(row)
                self._uninstall_rows.append((app, row))
            self._uninstall_list_lay.addStretch(1)
            self._filter_uninstall_list()
            self._append(
                self.txt_uninstall_out,
                f"{len(apps)} aplicativo(s) encontrado(s).",
            )
            self.btn_uninstall_refresh.setEnabled(True)
            self.btn_uninstall_run.setEnabled(True)

        def err(msg: str) -> None:
            self._append(self.txt_uninstall_out, f"Falha: {msg}")
            self.btn_uninstall_refresh.setEnabled(True)
            self.btn_uninstall_run.setEnabled(True)
            self._err_dialog(msg)

        self.bg.run(fn, ok, err)

    @Slot()
    def uninstall_selected_apps(self) -> None:
        if self._uninstall_busy:
            QMessageBox.information(self, APP_NAME, "Desinstalação já em andamento.")
            return
        serial = self._require_serial()
        if not serial or not self.adb:
            return
        selected = [app for app, row in self._uninstall_rows if row.isChecked() and row.isVisible()]
        if not selected:
            QMessageBox.warning(self, APP_NAME, "Selecione ao menos um aplicativo.")
            return
        busy = self._device_busy()
        if busy:
            QMessageBox.information(self, APP_NAME, f"Há {busy} em andamento. Aguarde terminar.")
            return
        blocked = [a for a in selected if uninstall_protection(a.package) == "blocked"]
        if blocked:
            QMessageBox.warning(
                self,
                APP_NAME,
                "Estes componentes essenciais do Android não podem ser desinstalados pelo Aibox "
                "e foram removidos da seleção:\n\n" + "\n".join(f"• {a.display} ({a.package})" for a in blocked),
            )
            selected = [a for a in selected if a not in blocked]
            if not selected:
                return
        risky = [a for a in selected if a.system or uninstall_protection(a.package) == "sensitive"]
        listing = "\n".join(f"• {a.display} ({a.package})" for a in selected[:30])
        if len(selected) > 30:
            listing += f"\n… e mais {len(selected) - 30}"
        confirm = QMessageBox.warning(
            self,
            APP_NAME,
            f"Desinstalar {len(selected)} app(s)?\n\n{listing}\n\nEsta ação não pode ser desfeita.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        if risky:
            typed, ok_ = QInputDialog.getText(
                self,
                "Confirmar desinstalação sensível",
                "Entre os selecionados há apps de sistema ou do kiosk (autostart/TTS):\n"
                + "\n".join(f"• {a.display}" for a in risky[:10])
                + "\n\nRemover pode deixar o aparelho inutilizável.\nDigite DESINSTALAR para continuar:",
            )
            if not ok_ or typed.strip().upper() != "DESINSTALAR":
                QMessageBox.information(self, APP_NAME, "Desinstalação cancelada.")
                return
        self._uninstall_busy = True
        self._uninstall_queue = list(selected)
        self._uninstall_total = len(selected)
        self._uninstall_done = 0
        self.btn_uninstall_refresh.setEnabled(False)
        self.btn_uninstall_run.setEnabled(False)
        self._set_uninstall_progress(0, animate=False)
        self._append(self.txt_uninstall_out, f"Fila: {len(selected)} app(s). Iniciando…")
        self._uninstall_next(serial)

    def _on_uninstall_progress_frame(self, value: int) -> None:
        self.lbl_uninstall_pct.setText(f"{int(value)}%")

    def _set_uninstall_progress(self, percent: int, *, animate: bool = True) -> None:
        target = max(0, min(100, int(percent)))
        if self._uninstall_progress_anim is None:
            self.progress_uninstall.setValue(target)
            self.lbl_uninstall_pct.setText(f"{target}%")
            return
        self._uninstall_progress_anim.stop()
        if not animate:
            self.progress_uninstall.setValue(target)
            self.lbl_uninstall_pct.setText(f"{target}%")
            return
        self._uninstall_progress_anim.setStartValue(self.progress_uninstall.value())
        self._uninstall_progress_anim.setEndValue(target)
        self._uninstall_progress_anim.start()

    def _uninstall_progress_percent(self) -> int:
        if self._uninstall_total <= 0:
            return 0
        return int(round(100.0 * self._uninstall_done / self._uninstall_total))

    def _uninstall_next(self, serial: str) -> None:
        try:
            self._uninstall_next_impl(serial)
        except Exception as exc:  # noqa: BLE001
            self._uninstall_fatal(exc)

    def _uninstall_next_impl(self, serial: str) -> None:
        if not self._uninstall_queue:
            self._uninstall_busy = False
            self.btn_uninstall_refresh.setEnabled(True)
            self.btn_uninstall_run.setEnabled(True)
            self._set_uninstall_progress(100, animate=True)
            self._append(self.txt_uninstall_out, "Desinstalação concluída.")
            self.refresh_uninstall_apps()
            return
        app = self._uninstall_queue.pop(0)
        current = self._uninstall_done + 1
        self._append(
            self.txt_uninstall_out,
            f"Desinstalando {app.display} ({app.package})… ({current}/{self._uninstall_total})",
        )

        def fn() -> str:
            assert self.adb is not None
            return self.adb.uninstall_package(serial, app.package)

        def ok(msg: str) -> None:
            self._append(self.txt_uninstall_out, f"OK • {app.display}: {msg}")
            self._uninstall_done += 1
            self._set_uninstall_progress(self._uninstall_progress_percent(), animate=True)
            self._uninstall_next(serial)

        def err(msg: str) -> None:
            self._append(self.txt_uninstall_out, f"Falha • {app.display}: {msg}")
            self._uninstall_done += 1
            self._set_uninstall_progress(self._uninstall_progress_percent(), animate=True)
            self._uninstall_next(serial)

        self.bg.run(
            fn,
            self._guarded(ok, self._uninstall_fatal),
            self._guarded(err, self._uninstall_fatal),
        )

    @Slot()
    def start_optimize_device(self) -> None:
        if self._optimize_busy:
            QMessageBox.information(self, APP_NAME, "Otimização já em andamento.")
            return
        serial = self._require_serial()
        if not serial or not self.adb:
            return
        busy = self._device_busy()
        if busy:
            QMessageBox.information(self, APP_NAME, f"Há {busy} em andamento. Aguarde terminar.")
            return
        box_ = QMessageBox(self)
        box_.setIcon(QMessageBox.Question)
        box_.setWindowTitle(APP_NAME)
        box_.setText(
            "Otimizar o aparelho?\n\n"
            "Serão feitos: limpeza de caches dos apps, compilação (dexopt) e liberação de RAM.\n"
            "Nenhum app, foto ou documento é removido."
        )
        deep = QCheckBox("Limpeza profunda: apagar /data/local/tmp, lixeiras e limpar o logcat")
        box_.setCheckBox(deep)
        box_.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box_.setDefaultButton(QMessageBox.No)
        if box_.exec() != QMessageBox.Yes:
            return
        deep_clean = deep.isChecked()
        self._optimize_busy = True
        self.btn_optimize.setEnabled(False)
        self.progress_optimize.setVisible(True)
        self.progress_optimize.setRange(0, 0)
        self._append(self.txt_optimize_out, f"Otimizando {serial}… isso pode levar alguns minutos.")

        def fn() -> str:
            assert self.adb is not None
            return self.adb.optimize_device(serial, deep_clean=deep_clean)

        def ok(report: str) -> None:
            self._append(self.txt_optimize_out, report)
            self._append(self.txt_optimize_out, "Otimização finalizada.")
            self._optimize_busy = False
            self.btn_optimize.setEnabled(True)
            self.progress_optimize.setRange(0, 100)
            self.progress_optimize.setValue(100)
            QMessageBox.information(
                self,
                APP_NAME,
                "Otimização concluída.\n\nVeja o relatório na tela para detalhes de armazenamento, RAM e erros.",
            )

        def err(msg: str) -> None:
            self._append(self.txt_optimize_out, f"Falha: {msg}")
            self._optimize_busy = False
            self.btn_optimize.setEnabled(True)
            self.progress_optimize.setVisible(False)
            self._err_dialog(msg)

        self.bg.run(fn, ok, err)

    def _tab_factory_reset(self) -> QWidget:
        w, lay = self._make_page()
        card, card_lay = self._make_card()
        card_lay.addWidget(self._section_title("Restauração de fábrica"))
        card_lay.addWidget(
            self._page_tip(
                "Tenta apagar os dados do dispositivo conectado e reiniciá-lo como novo. "
                "Se o wipe direto não for permitido, o Aibox abre a tela «Redefinir» e confirma automaticamente."
            )
        )
        card_lay.addWidget(
            self._info_panel(
                "Atenção: o processo não pode ser desfeito.\n"
                "Olhe a tela do aparelho após solicitar. "
                "O Aibox só desconecta quando detectar que o reset realmente iniciou."
            )
        )

        self.txt_reset_out = self._status_box("Status da restauração…", tall=True)
        card_lay.addWidget(self.txt_reset_out)
        card_lay.addStretch(1)

        self.btn_factory_reset = QPushButton("Restaurar de fábrica")
        self.btn_factory_reset.setObjectName("DangerButton")
        self.btn_factory_reset.clicked.connect(self.start_factory_reset)

        lay.addWidget(self._wrap_card(card), 1)
        lay.addWidget(self._make_action_bar(self.btn_factory_reset))
        return w

    def _on_apk_toggled(self, category: str, entry: ApkEntry, checked: bool) -> None:
        self._update_apk_accordion_label(category)
        if category == "Painel":
            if self._any_apk_checked_in("Painel"):
                self._set_catalog_apk_checked(self._sintese_de_voz_entry(), True)
            else:
                self._set_catalog_apk_checked(self._sintese_de_voz_entry(), False)
        if self._is_autostart_apk(entry):
            # Só chega aqui por clique do usuário (as marcações automáticas bloqueiam o sinal).
            self._autostart_auto = False
        else:
            self._sync_autostart_checkbox()

    def _autostart_checked(self) -> bool:
        entry = self._autostart_entry()
        if entry is None:
            return False
        for e, row in self._apk_rows.get(entry.category) or []:
            if e.filename == entry.filename:
                return row.isChecked()
        return False

    def _any_apk_checked_in(self, category: str) -> bool:
        rows = self._apk_rows.get(category) or []
        return any(row.isChecked() and row.toggle.isEnabled() for _entry, row in rows)

    def _sintese_de_voz_entry(self) -> ApkEntry | None:
        for e in APK_CATALOG:
            if e.filename == "Sintese_de_Voz.apk" or e.post_install == "tts_pt_br_voice_v":
                return e
        return None

    def _autostart_entry(self) -> ApkEntry | None:
        for e in APK_CATALOG:
            if e.filename == "Autostart.apk" or e.label.lower() == "autostart":
                return e
        return None

    def _is_autostart_apk(self, entry: ApkEntry) -> bool:
        return is_autostart_apk(entry)

    def _wants_boot_autostart(self, entry: ApkEntry) -> bool:
        return wants_boot_autostart(entry)

    def _any_non_autostart_apk_checked(self) -> bool:
        for rows in self._apk_rows.values():
            for entry, row in rows:
                if self._is_autostart_apk(entry):
                    continue
                if row.isChecked() and row.toggle.isEnabled():
                    return True
        return False

    def _sync_autostart_checkbox(self) -> None:
        """Marca Autostart na lista sempre que qualquer outro APK estiver ativo."""
        if not getattr(self, "_apk_rows", None):
            return
        want = self._any_non_autostart_apk_checked()
        if want:
            if not self._autostart_checked():
                self._autostart_auto = True
                self._set_catalog_apk_checked(self._autostart_entry(), True, force=True)
            self._expand_apk_category("Outros")
        elif self._autostart_auto:
            # Desmarca só o que o app marcou; uma escolha explícita do usuário é respeitada.
            self._autostart_auto = False
            self._set_catalog_apk_checked(self._autostart_entry(), False, force=True)

    def _expand_apk_category(self, category: str) -> None:
        btn = self._apk_accordion_btns.get(category)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)
        self._toggle_apk_category(category, True)

    def _set_catalog_apk_checked(
        self,
        entry: ApkEntry | None,
        checked: bool,
        *,
        force: bool = False,
    ) -> None:
        if entry is None:
            return
        rows = self._apk_rows.get(entry.category) or []
        for e, row in rows:
            if e.filename != entry.filename:
                continue
            if force and checked:
                path = resolve_apk(entry)
                if path.exists() and path.stat().st_size > 0:
                    row.setEnabled(True)
                    row.toggle.setEnabled(True)
                    row.setText(entry.label)
            if not row.toggle.isEnabled() and not force:
                break
            if row.isChecked() == checked:
                self._update_apk_accordion_label(entry.category)
                break
            row.toggle.blockSignals(True)
            row.setChecked(checked)
            row.toggle.blockSignals(False)
            self._update_apk_accordion_label(entry.category)
            break

    def _expand_install_dependencies(self, entries: list[ApkEntry]) -> list[ApkEntry]:
        return expand_install_dependencies(
            entries,
            tts=self._sintese_de_voz_entry(),
            autostart=self._autostart_entry(),
        )

    def _toggle_apk_category(self, category: str, expanded: bool) -> None:
        body = self._apk_accordion_bodies.get(category)
        btn = self._apk_accordion_btns.get(category)
        if body is not None:
            body.setVisible(expanded)
        if btn is not None:
            btn.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self._update_apk_accordion_label(category)

    def _update_apk_accordion_label(self, category: str) -> None:
        btn = self._apk_accordion_btns.get(category)
        rows = self._apk_rows.get(category)
        if btn is None or rows is None:
            return
        ready = 0
        checked = 0
        for entry, row in rows:
            if row.isEnabled() and row.toggle.isEnabled():
                path = resolve_apk(entry)
                if path.exists() and path.stat().st_size > 0:
                    ready += 1
            if row.isChecked():
                checked += 1
        parts = [category]
        if ready:
            parts.append(f"{ready} pronto(s)")
        else:
            parts.append("sem APK")
        if checked:
            parts.append(f"{checked} ativo(s)")
        btn.setText(" · ".join(parts))

    def _tab_logs(self) -> QWidget:
        w, lay = self._make_page()
        card, card_lay = self._make_card()
        card_lay.addWidget(self._section_title("Debug / Logcat"))
        card_lay.addWidget(self._page_tip("Visualize os logs do dispositivo em tempo real."))

        self.txt_logcat = self._status_box("Logs aparecerão aqui…", fill=True)
        self.txt_logcat.document().setMaximumBlockCount(5000)
        card_lay.addWidget(self.txt_logcat, 1)

        self.btn_logcat_clear = QPushButton("Limpar")
        self.btn_logcat_clear.setObjectName("SecondaryButton")
        self.btn_logcat_clear.clicked.connect(lambda: self.txt_logcat.clear())
        self.btn_logcat_stop = QPushButton("Parar")
        self.btn_logcat_stop.setObjectName("SecondaryButton")
        self.btn_logcat_stop.clicked.connect(self.stop_logcat)
        self.btn_logcat_start = QPushButton("Iniciar")
        self.btn_logcat_start.setObjectName("PrimaryButton")
        self.btn_logcat_start.clicked.connect(self.start_logcat)

        lay.addWidget(self._wrap_card(card), 1)
        lay.addWidget(self._make_action_bar(self.btn_logcat_clear, self.btn_logcat_stop, self.btn_logcat_start))
        return w

    def _tab_about(self) -> QWidget:
        w, lay = self._make_page()
        card, card_lay = self._make_card()
        card_lay.addWidget(self._section_title(APP_NAME))
        card_lay.addWidget(self._page_tip("Intelite — ferramenta de apoio Aibox."))
        card_lay.addWidget(
            self._info_panel(
                "• Conectar dispositivos por USB ou Wi‑Fi\n"
                "• Ajustar DPI\n"
                "• Gravar e capturar a tela\n"
                "• Instalar e desinstalar aplicativos\n"
                "• Otimizar desempenho\n"
                "• Restauração de fábrica\n"
                "• Visualizar logs (Debug)\n\n"
                "Desenvolvido para a Intelite."
            )
        )

        self.lbl_about_version = QLabel(f"Versão instalada: {APP_VERSION}")
        self.lbl_about_version.setObjectName("SectionTitle")
        card_lay.addWidget(self.lbl_about_version)

        self.lbl_update_status = QLabel("Verifique se há uma versão mais recente.")
        self.lbl_update_status.setObjectName("PageTip")
        self.lbl_update_status.setWordWrap(True)
        card_lay.addWidget(self.lbl_update_status)

        self.txt_update_notes = self._status_box("Notas da versão aparecerão aqui…", tall=True)
        card_lay.addWidget(self.txt_update_notes)

        self.progress_update = QProgressBar()
        self.progress_update.setRange(0, 100)
        self.progress_update.setValue(0)
        self.progress_update.setVisible(False)
        self.lbl_update_pct = QLabel("0%")
        self.lbl_update_pct.setVisible(False)
        card_lay.addLayout(self._add_progress_row(self.progress_update, self.lbl_update_pct))
        self.progress_update.setVisible(False)
        self.lbl_update_pct.setVisible(False)

        card_lay.addStretch(1)

        self.btn_check_updates = QPushButton("Buscar atualizações")
        self.btn_check_updates.setObjectName("PrimaryButton")
        self.btn_check_updates.clicked.connect(self.check_for_app_updates)
        lay.addWidget(self._wrap_card(card), 1)
        lay.addWidget(self._make_action_bar(self.btn_check_updates))

        self._update_check: UpdateCheckResult | None = None
        self._update_busy = False
        return w

    def _set_update_progress(self, downloaded: int, total: int) -> None:
        if total > 0:
            pct = max(0, min(100, int(round(100.0 * downloaded / total))))
        else:
            pct = 0
        self.progress_update.setVisible(True)
        self.lbl_update_pct.setVisible(True)
        self.progress_update.setValue(pct)
        self.lbl_update_pct.setText(f"{pct}%")

    @Slot()
    def check_for_app_updates(self) -> None:
        if self._update_busy:
            return
        self._update_busy = True
        self.btn_check_updates.setEnabled(False)
        self.lbl_update_status.setText("Buscando atualizações…")
        self.txt_update_notes.setPlainText("")
        self.progress_update.setVisible(False)
        self.lbl_update_pct.setVisible(False)
        self.progress_update.setValue(0)

        def fn() -> UpdateCheckResult:
            return check_for_updates()

        def ok(result: UpdateCheckResult) -> None:
            self._update_busy = False
            self.btn_check_updates.setEnabled(True)
            self._update_check = result
            if not result.update_available:
                self.lbl_update_status.setText("Você está na versão mais recente.")
                if result.remote.asset.url:
                    notes = (
                        f"Versão atual: {result.current_version}\n"
                        f"Remoto: {result.remote.version}"
                    )
                else:
                    notes = (
                        f"Versão atual: {result.current_version}\n"
                        "Nenhuma versão nova publicada."
                    )
                self.txt_update_notes.setPlainText(notes)
                return

            remote = result.remote
            self.lbl_update_status.setText(
                f"Nova versão disponível: {remote.version}"
                + (f"  •  {remote.published_at}" if remote.published_at else "")
            )
            self.txt_update_notes.setPlainText(format_notes_plain(remote.notes))

            confirm = QMessageBox.question(
                self,
                APP_NAME,
                f"Há uma nova versão ({remote.version}).\n\n"
                "Baixar e instalar agora?\n"
                "O Aibox será fechado para concluir a atualização.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if confirm == QMessageBox.Yes:
                self._start_update_download(result)

        def err(msg: str) -> None:
            self._update_busy = False
            self.btn_check_updates.setEnabled(True)
            self.lbl_update_status.setText("Não foi possível verificar atualizações.")
            self._err_dialog(msg)

        self.bg.run(fn, ok, err)

    def _start_update_download(self, result: UpdateCheckResult) -> None:
        install_dir = install_dir_for_update()
        if install_dir is None or not is_frozen():
            QMessageBox.information(
                self,
                APP_NAME,
                "A instalação automática só está disponível no Aibox empacotado (EXE).\n"
                "Em modo desenvolvimento, baixe o release manualmente.",
            )
            return

        ops = [o for o in self._active_operations() if o != "atualização do app"]
        if ops:
            QMessageBox.information(
                self,
                APP_NAME,
                "Termine antes de atualizar: " + ", ".join(ops) + ".",
            )
            return

        self._update_busy = True
        self.btn_check_updates.setEnabled(False)
        self.lbl_update_status.setText(f"Baixando {result.remote.version}…")
        self.progress_update.setVisible(True)
        self.lbl_update_pct.setVisible(True)
        self.progress_update.setValue(0)
        self.lbl_update_pct.setText("0%")

        progress_holder: dict[str, tuple[int, int]] = {"v": (0, 0)}

        def on_progress(downloaded: int, total: int) -> None:
            progress_holder["v"] = (downloaded, total)

        # Atualiza a UI a partir do thread principal via timer leve
        tick = QTimer(self)
        tick.setInterval(200)

        def _tick() -> None:
            d, t = progress_holder["v"]
            self._set_update_progress(d, t)

        tick.timeout.connect(_tick)
        tick.start()

        def fn() -> str:
            path = stage_update_package(result.remote, progress=on_progress)
            return str(path)

        def ok(package_path: str) -> None:
            tick.stop()
            tick.deleteLater()
            self._set_update_progress(1, 1)
            self.lbl_update_status.setText("Download concluído. Aplicando atualização…")
            try:
                launch_update_helper(
                    pid=os.getpid(),
                    install_dir=install_dir,
                    package=Path(package_path),
                    restart=True,
                    sha256=result.remote.asset.sha256,
                )
            except Exception as e:
                self._update_busy = False
                self.btn_check_updates.setEnabled(True)
                self._err_dialog(f"Falha ao iniciar o instalador: {e}")
                return
            QMessageBox.information(
                self,
                APP_NAME,
                "A atualização será aplicada agora.\nO Aibox será reiniciado.",
            )
            self._closing = True
            self._shutdown_work()
            app = QApplication.instance()
            if app is not None:
                app.quit()
            else:
                self.close()

        def err(msg: str) -> None:
            tick.stop()
            tick.deleteLater()
            self._update_busy = False
            self.btn_check_updates.setEnabled(True)
            self.progress_update.setVisible(False)
            self.lbl_update_pct.setVisible(False)
            self.lbl_update_status.setText("Falha no download da atualização.")
            self._err_dialog(msg)

        self.bg.run(fn, ok, err)

    def _init_adb(self) -> None:
        if self._adb_initing:
            return
        self._adb_initing = True

        def fn() -> str:
            adb = Adb()
            adb.restart_guard = self._adb_restart_unsafe
            ver = adb.version()
            self.adb = adb
            return ver

        def ok(ver: str) -> None:
            self._adb_initing = False
            assert self.adb is not None
            first = (ver or "").splitlines()[0].strip() if ver else "ADB"
            self._append(self.txt_connect_out, f"{first} pronto.")
            self.refresh_devices()

        def err(msg: str) -> None:
            self._adb_initing = False
            self.adb = None
            self._append(self.txt_connect_out, f"ADB indisponível: {msg}")
            self._err_dialog(msg + "\n\nClique em «Atualizar» para tentar iniciar o ADB novamente.")

        self.bg.run(fn, ok, err)

    def _on_device_chosen(self, _index: int = 0) -> None:
        serial = self._selected_serial()
        if not serial:
            return
        d = self._selected_device()
        label = d.label if d else serial
        self._append(self.txt_connect_out, f"Dispositivo selecionado: {label}")

    def _selected_serial(self) -> str | None:
        if self.cmb_device.count() <= 0:
            return None
        if self.cmb_device.currentIndex() < 0:
            self.cmb_device.setCurrentIndex(0)
        data = self.cmb_device.currentData()
        if isinstance(data, str) and data.strip():
            return data.strip()
        txt = (self.cmb_device.currentText() or "").strip()
        if not txt:
            return None
        return txt.split(" • ", 1)[0].strip()

    def _selected_device(self) -> AdbDevice | None:
        serial = self._selected_serial()
        if not serial:
            return None
        for d in self.devices:
            if d.serial == serial:
                return d
        return None

    def _require_serial(self) -> str | None:
        d = self._selected_device()
        if not d:
            QMessageBox.warning(self, APP_NAME, "Selecione um dispositivo primeiro.")
            return None
        if d.state != "device":
            if d.state == "unauthorized":
                QMessageBox.warning(
                    self,
                    APP_NAME,
                    "Dispositivo não autorizado.\n\n"
                    "No celular: desbloqueie a tela e aceite a mensagem de Depuração USB (chave RSA).\n"
                    "Se não aparecer: desative/ative a Depuração USB e use 'Revogar autorizações de depuração USB'.\n"
                    "Depois clique em Atualizar e tente novamente.",
                )
                return None
            QMessageBox.warning(
                self,
                APP_NAME,
                f"Dispositivo indisponível (estado: {d.state}).\n\n"
                "Tente reconectar o cabo/USB, ativar Depuração USB, ou reconectar via Wi‑Fi e depois clique em Atualizar.",
            )
            return None
        return d.serial

    def _append(self, out: QTextEdit, text: str) -> None:
        out.append(text.rstrip("\n"))

    def _err_dialog(self, msg: str) -> None:
        QMessageBox.critical(self, APP_NAME, msg)

    def _tick_recording_ui(self) -> None:
        try:
            if self.recording_started_at is None:
                self.lbl_record_time.setText("00:00")
                return
            elapsed = max(0.0, time.monotonic() - self.recording_started_at)
            mm = int(elapsed // 60)
            ss = int(elapsed % 60)
            txt = f"{mm:02d}:{ss:02d}"
            self.lbl_record_time.setText(txt)

            if self.recording_state not in ("recording", "stopping"):
                return
            if not self.preview_panel:
                return

            now_ms = int(time.monotonic() * 1000)
            if now_ms - self.preview_last_refresh_ms >= 1000:
                self.preview_last_refresh_ms = now_ms
                self._request_preview_frame()
        except Exception:
            try:
                self.recording_timer.stop()
            except Exception:
                pass

    def _start_recording_ui(self, serial: str) -> None:
        self._preview_token += 1
        self.preview_serial = serial
        self.preview_inflight = False
        self.preview_last_refresh_ms = 0
        self.preview_errors = 0
        self.recording_started_at = time.monotonic()
        self.lbl_record_time.setText("00:00")
        self._set_record_timer_active(True)
        if self.preview_panel is not None:
            self.preview_panel.clear_preview()
            self.preview_panel.set_status("Carregando preview…")
        self.recording_timer.start()
        QTimer.singleShot(200, self._request_preview_frame)

    def _stop_recording_ui(self) -> None:
        self._preview_token += 1
        self.recording_timer.stop()
        self.recording_started_at = None
        self.preview_serial = None
        self.preview_inflight = False
        self.preview_last_refresh_ms = 0
        self.preview_errors = 0
        self.lbl_record_time.setText("00:00")
        self._set_record_timer_active(False)
        if self.preview_panel is not None:
            self.preview_panel.clear_preview()

    def _set_record_timer_active(self, active: bool) -> None:
        bar = getattr(self, "record_timer_bar", None)
        badge = getattr(self, "lbl_rec_indicator", None)
        if bar is not None:
            bar.setProperty("recording", active)
            _repolish(bar)
        if badge is not None:
            badge.setProperty("recording", active)
            _repolish(badge)
            badge.setText("REC" if active else "—")

    def _request_preview_frame(self) -> None:
        if self.preview_inflight:
            return
        if not self.preview_serial or not self.adb:
            return
        if not self.preview_panel:
            return
        if self.recording_state not in ("recording", "stopping"):
            return
        self.preview_inflight = True
        serial = self.preview_serial
        token = self._preview_token

        def fn() -> bytes:
            assert self.adb is not None
            return self.adb.screencap_png_bytes(serial, timeout_s=10)

        def ok(data: bytes) -> None:
            if token != self._preview_token:
                return  # quadro atrasado de uma gravação já encerrada
            self.preview_inflight = False
            if not self.preview_panel:
                return
            if not data:
                self.preview_errors += 1
                self.preview_panel.set_status("Sem dados do preview.")
                return
            pm = QPixmap()
            if not pm.loadFromData(data, "PNG"):
                self.preview_errors += 1
                self.preview_panel.set_status("Falha ao decodificar o preview.")
                return
            self.preview_errors = 0
            self.preview_panel.set_frame(pm)

        def err(msg: str) -> None:
            if token != self._preview_token:
                return
            self.preview_inflight = False
            if self.preview_panel:
                self.preview_panel.set_status(msg)
            self.preview_errors += 1

        self.bg.run(fn, ok, err)

    @Slot()
    def refresh_devices(self) -> None:
        if not self.adb:
            self._init_adb()  # nova tentativa caso o ADB não tenha subido
            return

        def fn() -> tuple[list[AdbDevice], str | None]:
            assert self.adb is not None
            devs = self.adb.list_devices()
            hint = None if devs else self.adb.explain_empty_devices()
            return devs, hint

        def ok(result: tuple[list[AdbDevice], str | None]) -> None:
            devs, hint = result
            self.devices = devs
            current = self._selected_serial()
            self.cmb_device.blockSignals(True)
            self.cmb_device.clear()
            for d in devs:
                self.cmb_device.addItem(d.label, d.serial)
            if current:
                idx = self.cmb_device.findData(current)
                if idx >= 0:
                    self.cmb_device.setCurrentIndex(idx)
                elif self.cmb_device.count() > 0:
                    self.cmb_device.setCurrentIndex(0)
            elif self.cmb_device.count() > 0:
                self.cmb_device.setCurrentIndex(0)
            self.cmb_device.blockSignals(False)
            if not devs:
                msg = hint or "Nenhum dispositivo detectado. Verifique USB debugging ou conexão Wi‑Fi."
                if getattr(self, "_last_device_status", None) != msg:
                    self._append(self.txt_connect_out, msg)
                    self._last_device_status = msg
            else:
                self._last_device_status = None
            if self.wifi_connected_address:
                still = any(d.serial == self.wifi_connected_address and d.state == "device" for d in devs)
                if not still and self.wifi_state == "connected":
                    self.wifi_connected_address = None
                    self._set_wifi_state("disconnected")
                    self._wifi_log("Conexão Wi‑Fi caiu (dispositivo não aparece mais no adb devices).")

        self.bg.run(fn, ok, self._err_dialog)

    @Slot()
    def toggle_wifi(self) -> None:
        if self.wifi_state in ("connecting", "disconnecting"):
            return
        if self.wifi_state == "connected":
            self.disconnect_wifi()
            return
        self.connect_wifi()

    def _normalize_wifi_address(self, text: str) -> tuple[str | None, str | None]:
        raw = (text or "").strip()
        if not raw or raw == ":":
            return None, "Informe IP:PORTA."
        if re.fullmatch(r"[0-9]+", raw):
            return None, "Informe IP:PORTA (não apenas a porta)."
        m = re.fullmatch(r"([0-9]{1,3}(?:\.[0-9]{1,3}){3})(?::([0-9]{1,5}))?", raw)
        if not m:
            return None, "Informe um IPv4 válido (ex: 192.168.0.10:5555)."
        octets = [int(p) for p in m.group(1).split(".")]
        if any(o > 255 for o in octets):
            return None, "Octeto de IP inválido (0–255)."
        ip = ipaddress.IPv4Address(".".join(str(o) for o in octets))
        if ip.is_unspecified or ip.is_loopback or ip.is_multicast or ip == ipaddress.IPv4Address("255.255.255.255"):
            return None, "Esse endereço IP não pode ser usado para conectar a um aparelho."
        port = int(m.group(2)) if m.group(2) else 5555
        if port < 1 or port > 65535:
            return None, "Porta inválida. Use um valor entre 1 e 65535."
        return f"{ip}:{port}", None

    def _wifi_address_is_valid(self, text: str) -> bool:
        addr, err = self._normalize_wifi_address(text)
        return bool(addr) and err is None

    def _wifi_log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self._append(self.txt_connect_out, f"[Wi‑Fi {ts}] {msg}")

    def _wifi_parse_host(self, address: str) -> str:
        host, _port = address.rsplit(":", 1)
        return host.strip()

    def _wifi_local_ipv4s(self, device_ip: str | None = None) -> list[str]:
        ips: list[str] = []

        try:
            target = device_ip or "8.8.8.8"
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect((target, 53))
                ip = s.getsockname()[0]
                if ip and ip != "127.0.0.1" and ip not in ips:
                    ips.append(ip)
            finally:
                s.close()
        except Exception:
            pass

        try:
            cp = run_hidden(["ipconfig"], capture_output=True, text=True, timeout=4)
            out = (cp.stdout or "") + "\n" + (cp.stderr or "")
            for m in re.finditer(r"IPv4[^:]*:\s*([\d.]+)", out, flags=re.IGNORECASE):
                ip = m.group(1).strip()
                if ip and ip != "127.0.0.1" and ip not in ips:
                    ips.append(ip)
            for m in re.finditer(r"Endere[cç]o IPv4[^:]*:\s*([\d.]+)", out, flags=re.IGNORECASE):
                ip = m.group(1).strip()
                if ip and ip != "127.0.0.1" and ip not in ips:
                    ips.append(ip)
        except Exception:
            pass

        return ips

    def _wifi_same_subnet_guess(self, local_ips: list[str], device_ip: str) -> bool | None:
        try:
            a = device_ip.split(".")
            if len(a) != 4:
                return None
            dev_prefix = ".".join(a[:3])
        except Exception:
            return None
        for ip in local_ips:
            parts = ip.split(".")
            if len(parts) == 4 and ".".join(parts[:3]) == dev_prefix:
                return True
        return False if local_ips else None

    def _wifi_ping(self, host: str) -> tuple[bool, str]:
        try:
            cp = run_hidden(
                ["ping", "-n", "1", "-w", "1000", host],
                capture_output=True,
                text=True,
                timeout=4,
            )
        except Exception as e:
            return False, str(e)
        out = (cp.stdout or "") + "\n" + (cp.stderr or "")
        out = out.strip()
        ok = cp.returncode == 0 and ("ttl=" in out.lower() or "tempo=" in out.lower() or "time=" in out.lower())
        m = re.search(r"(tempo|time)[=<]\s*(\d+)\s*ms", out, flags=re.IGNORECASE)
        if ok and m:
            return True, f"ok ({m.group(2)} ms)"
        return ok, (out.splitlines()[-1].strip() if out else "falha")

    def _wifi_connect_hint(self, err_text: str, address: str) -> str | None:
        low = (err_text or "").lower()
        if "timeout executando" in low or "timeout" in low:
            return "Possível bloqueio de rede/firewall, IP incorreto, ou porta errada (pareamento vs depuração)."
        if "failed to authenticate" in low or "authentication" in low:
            return (
                "Parece que falta pareamento da Depuração sem fio. Use o botão Parear com o código e a porta de pareamento."
            )
        if "no route to host" in low or "network is unreachable" in low:
            return "Sem rota para o aparelho. Confirme se PC e celular estão na mesma rede e sem VPN/isolamento de AP."
        if "connection refused" in low:
            return "A porta recusou conexão. Normalmente é porta errada (use a porta exibida em ‘Endereço IP e porta’)."
        if "unable to connect" in low or "failed to connect" in low or "cannot connect" in low:
            if address.endswith(":5555"):
                return "Se estiver usando Depuração sem fio, use a porta exibida no aparelho (não 5555). Se for via USB, rode adb tcpip 5555 primeiro."
            return "Confirme se a porta é a de depuração (não a de pareamento) e se a Depuração sem fio está ativa."
        if "unknown host" in low:
            return "Host inválido. Use um IP (ex: 192.168.0.10) e a porta correta."
        return None

    @Slot()
    def pair_wifi(self) -> None:
        """Pareamento da Depuração sem fio (IP:PORTA de pareamento + código de 6 dígitos)."""
        if not self.adb:
            self._init_adb()
            QMessageBox.information(self, APP_NAME, "O ADB ainda não está pronto. Tente de novo em instantes.")
            return
        if "pair" in self._quick_busy:
            return
        text, ok = QInputDialog.getText(
            self,
            "Parear dispositivo",
            "No aparelho: Depuração sem fio → «Parear dispositivo com código».\n"
            "Digite o IP:PORTA exibido ali (a porta de pareamento é diferente da de conexão):",
            text=self.txt_wifi.text().strip(),
        )
        if not ok:
            return
        raw = text.strip()
        if ":" not in raw:
            QMessageBox.warning(self, APP_NAME, "Informe IP:PORTA do pareamento (ex.: 192.168.0.10:37123).")
            return
        address, err_txt = self._normalize_wifi_address(raw)
        if err_txt or not address:
            QMessageBox.warning(self, APP_NAME, err_txt or "Endereço inválido.")
            return
        code, ok = QInputDialog.getText(
            self, "Parear dispositivo", "Código de pareamento (6 dígitos):"
        )
        code = code.strip()
        if not ok:
            return
        if not re.fullmatch(r"[0-9]{6}", code):
            QMessageBox.warning(self, APP_NAME, "O código de pareamento tem 6 dígitos.")
            return

        def fn() -> str:
            assert self.adb is not None
            return self.adb.pair(address, code)

        def done(msg: str) -> None:
            self._wifi_log(f"Pareamento: {msg or 'concluído'}")
            self._wifi_log(
                "Agora informe o IP:PORTA de CONEXÃO (tela principal da Depuração sem fio) e clique em Conectar."
            )

        self._quick_action("pair", self.btn_wifi_pair, fn, done)

    @Slot()
    def connect_wifi(self) -> None:
        if not self.adb or self.wifi_state in ("connecting", "disconnecting"):
            return
        address, err_txt = self._normalize_wifi_address(self.txt_wifi.text())
        if err_txt or not address:
            QMessageBox.warning(self, APP_NAME, err_txt or "Informe IP:PORTA.")
            return
        if self.txt_wifi.text().strip() != address:
            self.txt_wifi.setText(address)
        self._set_wifi_state("connecting")
        attempt_id = secrets.token_hex(6)
        self.wifi_attempt_id = attempt_id
        QTimer.singleShot(45000, lambda: self._wifi_connect_timeout(attempt_id))
        self.wifi_last_error = None

        def fn() -> dict:
            assert self.adb is not None
            host = self._wifi_parse_host(address)
            local_ips = self._wifi_local_ipv4s(device_ip=host)
            same_subnet = self._wifi_same_subnet_guess(local_ips, host)
            ping_ok, ping_info = self._wifi_ping(host)
            try:
                msg = self.adb.connect(address)
                return {
                    "success": True,
                    "address": address,
                    "host": host,
                    "local_ips": local_ips,
                    "same_subnet": same_subnet,
                    "ping_ok": ping_ok,
                    "ping_info": ping_info,
                    "msg": msg,
                }
            except Exception as e:
                return {
                    "success": False,
                    "address": address,
                    "host": host,
                    "local_ips": local_ips,
                    "same_subnet": same_subnet,
                    "ping_ok": ping_ok,
                    "ping_info": ping_info,
                    "error": str(e),
                }

        def ok(res: dict) -> None:
            if self.wifi_attempt_id != attempt_id:
                return
            self._wifi_log(f"Endereço informado: {res.get('address')}")
            local_ips = res.get("local_ips") or []
            if local_ips:
                self._wifi_log("IPs locais: " + ", ".join(local_ips))
            same_subnet = res.get("same_subnet")
            if same_subnet is True:
                self._wifi_log("Rede: mesmo /24 (estimado).")
            elif same_subnet is False:
                self._wifi_log("Rede: parece diferente (/24). Verifique se PC e aparelho estão na mesma rede.")
            ping_ok = bool(res.get("ping_ok"))
            self._wifi_log(f"Ping {res.get('host')}: {'ok' if ping_ok else 'falhou'} • {res.get('ping_info')}")

            if not res.get("success"):
                err_txt = str(res.get("error") or "").strip() or "Falha ao conectar."
                self.wifi_last_error = err_txt
                self._set_wifi_state("disconnected")
                hint = self._wifi_connect_hint(err_txt, address)
                self._wifi_log(f"Falha: {err_txt}")
                if hint:
                    self._wifi_log(hint)
                    self._err_dialog((err_txt + "\n\n" + hint).strip())
                else:
                    self._err_dialog(err_txt)
                return

            msg = str(res.get("msg") or "").strip()
            low = msg.lower()
            if "unable to connect" in low or "failed to connect" in low or "cannot connect" in low:
                self.wifi_last_error = msg
                self._set_wifi_state("disconnected")
                hint = self._wifi_connect_hint(msg, address)
                self._wifi_log(f"Falha: {msg}")
                if hint:
                    self._wifi_log(hint)
                    self._err_dialog((msg + "\n\n" + hint).strip())
                else:
                    self._err_dialog(msg)
                return

            self.wifi_connected_address = address
            self.wifi_reconnect_failures = 0
            self._set_wifi_state("connected")
            self._wifi_log(msg or "Conectado.")
            self.refresh_devices()

        def err(msg: str) -> None:
            if self.wifi_attempt_id != attempt_id:
                return
            self.wifi_last_error = msg
            self._set_wifi_state("disconnected")
            self._err_dialog(msg)

        self.bg.run(fn, ok, err)

    def _wifi_connect_timeout(self, attempt_id: str) -> None:
        if self.wifi_attempt_id != attempt_id:
            return
        if self.wifi_state == "connecting":
            self._set_wifi_state("disconnected")
            self.wifi_last_error = "Timeout conectando via Wi‑Fi."
            self.wifi_attempt_id = None  # descarta o resultado se chegar depois do aviso
            self._err_dialog("Timeout conectando via Wi‑Fi. Verifique IP:PORTA e a Depuração sem fio no aparelho.")

    def _wifi_monitor_tick(self) -> None:
        if self.wifi_state != "connected" or not self.adb or not self.wifi_connected_address:
            return
        if self.wifi_reconnect_inflight or self._reboot_busy:
            return
        address = self.wifi_connected_address
        self.wifi_reconnect_inflight = True
        attempt_id = secrets.token_hex(6)
        self.wifi_attempt_id = attempt_id

        def fn() -> dict:
            assert self.adb is not None
            devs = self.adb.list_devices()
            state = None
            for d in devs:
                if d.serial == address:
                    state = d.state
                    break
            if state == "device":
                return {"present": True, "state": state}
            if self._reset_recent():
                # Reset em andamento: o aparelho vai sumir; não adianta (nem deve) tentar reconectar.
                return {"present": False, "state": state, "skip_reconnect": True}
            try:
                msg = self.adb.connect(address)
                return {"present": False, "state": state, "reconnect_msg": msg}
            except Exception as e:
                return {"present": False, "state": state, "reconnect_error": str(e)}

        def ok(res: dict) -> None:
            if self.wifi_attempt_id != attempt_id:
                self.wifi_reconnect_inflight = False
                return
            self.wifi_reconnect_inflight = False
            if res.get("present"):
                if self.wifi_reconnect_failures:
                    self._wifi_log("Conexão Wi‑Fi estabilizou novamente.")
                self.wifi_reconnect_failures = 0
                return

            if res.get("skip_reconnect"):
                self._wifi_log("Monitor: o aparelho saiu da rede após a restauração de fábrica (esperado).")
                self._notify_reset_in_progress(address)
                return

            state = res.get("state")
            if state:
                self._wifi_log(f"Monitor: dispositivo em estado {state}. Tentando reconectar…")
            else:
                self._wifi_log("Monitor: dispositivo não encontrado. Tentando reconectar…")

            if "reconnect_msg" in res and res.get("reconnect_msg"):
                self._wifi_log(str(res.get("reconnect_msg")).strip())
                self.refresh_devices()
                self.wifi_reconnect_failures = 0
                return

            self.wifi_reconnect_failures += 1
            err_txt = str(res.get("reconnect_error") or "").strip() or "Falha ao reconectar."
            self.wifi_last_error = err_txt
            self._wifi_log(f"Reconexão falhou ({self.wifi_reconnect_failures}): {err_txt}")
            if self.wifi_reconnect_failures >= 3:
                hint = self._wifi_connect_hint(err_txt, address)
                self.wifi_connected_address = None
                self._set_wifi_state("disconnected")
                self._err_dialog(
                    f"O Aibox perdeu a conexão com {address} e não conseguiu reconectar após 3 tentativas.\n\n"
                    "Verifique se o aparelho está ligado, na mesma rede e com a depuração ativa."
                    + (f"\n\nDica: {hint}" if hint else "")
                    + f"\n\nDetalhe técnico: {err_txt}"
                )

        def err(msg: str) -> None:
            if self.wifi_attempt_id != attempt_id:
                self.wifi_reconnect_inflight = False
                return
            self.wifi_reconnect_inflight = False
            self.wifi_last_error = msg
            self._wifi_log(f"Monitor: erro: {msg}")

        self.bg.run(fn, ok, err)

    @Slot()
    def disconnect_wifi(self) -> None:
        if not self.adb or self.wifi_state in ("connecting", "disconnecting"):
            return
        address = self.wifi_connected_address
        if not address:
            address, _err = self._normalize_wifi_address(self.txt_wifi.text())
        if not address:
            QMessageBox.warning(self, APP_NAME, "Informe IP:PORTA.")
            return
        self._set_wifi_state("disconnecting")
        self.wifi_attempt_id = secrets.token_hex(6)

        def fn() -> str:
            assert self.adb is not None
            return self.adb.disconnect(address)

        def ok(msg: str) -> None:
            self.wifi_connected_address = None
            self._set_wifi_state("disconnected")
            self._append(self.txt_connect_out, msg)
            self.refresh_devices()

        def err(msg: str) -> None:
            self._set_wifi_state("connected" if self.wifi_connected_address else "disconnected")
            self._err_dialog(msg)

        self.bg.run(fn, ok, err)

    def _set_wifi_state(self, state: str) -> None:
        self.wifi_state = state
        btn = self.btn_wifi_toggle
        address_ok = self._wifi_address_is_valid(self.txt_wifi.text())

        if state == "connected":
            if not self.wifi_monitor_timer.isActive():
                self.wifi_monitor_timer.start()
        else:
            if self.wifi_monitor_timer.isActive():
                self.wifi_monitor_timer.stop()
            self.wifi_reconnect_inflight = False

        if state == "disconnected":
            btn.setEnabled(address_ok)
            btn.setText("Conectar Wi‑Fi")
            btn.setObjectName("PrimaryButton")
            btn.setIcon(_nav_icon("fa5s.wifi", QStyle.SP_DialogApplyButton, QSize(18, 18), COLOR_ICON_ON_PRIMARY, self.style()))
            _repolish(btn)
            return
        if state == "connecting":
            btn.setEnabled(False)
            btn.setText("Conectando…")
            btn.setObjectName("PrimaryButton")
            btn.setIcon(_nav_icon("fa5s.wifi", QStyle.SP_DialogApplyButton, QSize(18, 18), COLOR_ICON_ON_PRIMARY, self.style()))
            _repolish(btn)
            return
        if state == "connected":
            btn.setEnabled(True)
            btn.setText("Desconectar Wi‑Fi")
            btn.setObjectName("DangerButton")
            btn.setIcon(
                _nav_icon("fa5s.wifi-slash", QStyle.SP_DialogCancelButton, QSize(18, 18), COLOR_ICON_ON_PRIMARY, self.style())
            )
            _repolish(btn)
            return
        if state == "disconnecting":
            btn.setEnabled(False)
            btn.setText("Desconectando…")
            btn.setObjectName("DangerButton")
            btn.setIcon(
                _nav_icon("fa5s.wifi-slash", QStyle.SP_DialogCancelButton, QSize(18, 18), COLOR_ICON_ON_PRIMARY, self.style())
            )
            _repolish(btn)
            return

        btn.setEnabled(address_ok)
        btn.setText("Conectar Wi‑Fi")
        btn.setObjectName("PrimaryButton")
        btn.setIcon(_nav_icon("fa5s.wifi", QStyle.SP_DialogApplyButton, QSize(18, 18), COLOR_ICON_ON_PRIMARY, self.style()))
        _repolish(btn)

    def _on_wifi_text_changed(self, _text: str) -> None:
        if self.wifi_state in ("disconnected", "connected"):
            self._set_wifi_state(self.wifi_state)

    @Slot()
    def pick_output_dir(self) -> None:
        p = QFileDialog.getExistingDirectory(self, "Pasta de capturas", str(self.output_dir))
        if not p:
            return
        target = Path(p)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, f"Não foi possível usar a pasta:\n{target}\n\n{exc}")
            return
        self.output_dir = target
        self._sync_output_fields()

    def _sync_output_fields(self) -> None:
        path = str(self.output_dir)
        for attr in ("txt_out_gravar", "txt_out_print"):
            w = getattr(self, attr, None)
            if w is not None:
                w.setText(path)

    def _ensure_output_dir(self, source: str | None = None) -> Path | None:
        """Pasta de saída do campo da aba de origem; None (com aviso) se não puder ser criada."""
        raw = ""
        attrs = ((source,) if source else ()) + ("txt_out_gravar", "txt_out_print")
        for attr in attrs:
            w = getattr(self, attr, None)
            if w is not None and (w.text() or "").strip():
                raw = (w.text() or "").strip()
                break
        target = Path(raw) if raw else self.output_dir
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(
                self,
                APP_NAME,
                f"Não foi possível usar a pasta de saída:\n{target}\n\n{exc}",
            )
            return None
        self.output_dir = target
        self._sync_output_fields()
        return target

    @Slot()
    def _open_output_dir(self) -> None:
        out = self._ensure_output_dir()
        if out is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(out)))

    @Slot()
    def take_screenshot(self) -> None:
        serial = self._require_serial()
        if not serial or not self.adb:
            return
        out_dir = self._ensure_output_dir("txt_out_print")
        if out_dir is None:
            return
        out = out_dir / f"screenshot_{serial.replace(':', '_')}_{time.strftime('%Y%m%d_%H%M%S')}.png"

        def fn() -> str:
            assert self.adb is not None
            p = self.adb.screenshot_png(serial, out)
            return str(p)

        def ok(p: str) -> None:
            self._append(self.txt_print_out, f"Print salvo: {p}")

        self._quick_action("print", self.btn_screenshot, fn, ok)

    def _quick_action(self, key: str, btn, fn, ok) -> None:
        """Roda uma ação curta em background com o botão desabilitado (sem duplo clique)."""
        if key in self._quick_busy:
            return
        self._quick_busy.add(key)
        btn.setEnabled(False)

        def done() -> None:
            self._quick_busy.discard(key)
            btn.setEnabled(True)

        def _ok(value) -> None:
            done()
            ok(value)

        def _err(msg: str) -> None:
            done()
            self._err_dialog(msg)

        self.bg.run(fn, _ok, _err)

    @Slot()
    def apply_dpi(self) -> None:
        serial = self._require_serial()
        if not serial or not self.adb:
            return
        dpi = int(self.spn_dpi.value())
        if dpi < 120 or dpi > 320:
            ans = QMessageBox.question(
                self,
                APP_NAME,
                f"DPI {dpi} está fora da faixa usual (120–320) e pode deixar a tela do aparelho "
                "ilegível ou grande demais.\n\nAplicar mesmo assim? (Use «Restaurar» para voltar ao padrão.)",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return

        def fn() -> str:
            assert self.adb is not None
            return self.adb.set_density(serial, dpi)

        def ok(msg: str) -> None:
            self._append(self.txt_dpi_out, msg)

        self._quick_action("dpi", self.btn_dpi_apply, fn, ok)

    @Slot()
    def reset_dpi(self) -> None:
        serial = self._require_serial()
        if not serial or not self.adb:
            return

        def fn() -> str:
            assert self.adb is not None
            return self.adb.reset_density(serial)

        def ok(msg: str) -> None:
            self._append(self.txt_dpi_out, msg)

        self._quick_action("dpi_reset", self.btn_dpi_reset, fn, ok)

    @Slot()
    def toggle_recording(self) -> None:
        state = self.recording_state
        if state == "idle":
            self.start_recording()
            return
        if state == "recording":
            self.stop_recording()
            return

    def _set_recording_state(self, state: str) -> None:
        self.recording_state = state
        btn = self.btn_record_toggle
        busy = state in ("starting", "recording", "stopping")
        if hasattr(self, "chk_record_audio"):
            self.chk_record_audio.setEnabled(not busy)
            self.chk_record_mic.setEnabled(not busy)
            self.cmb_mic.setEnabled(not busy and self.chk_record_mic.isChecked() and bool(self._mics))
            self.btn_refresh_mics.setEnabled(not busy)
        if state == "idle":
            btn.setEnabled(True)
            btn.setText("Iniciar gravação")
            btn.setObjectName("PrimaryButton")
            btn.setIcon(_nav_icon("fa5s.play", QStyle.SP_MediaPlay, QSize(18, 18), COLOR_ICON_ON_PRIMARY, self.style()))
            _repolish(btn)
            return
        if state == "starting":
            btn.setEnabled(False)
            btn.setText("Iniciando…")
            btn.setObjectName("PrimaryButton")
            btn.setIcon(_nav_icon("fa5s.play", QStyle.SP_MediaPlay, QSize(18, 18), COLOR_ICON_ON_PRIMARY, self.style()))
            _repolish(btn)
            return
        if state == "recording":
            btn.setEnabled(True)
            btn.setText("Parar gravação")
            btn.setObjectName("DangerButton")
            btn.setIcon(_nav_icon("fa5s.stop", QStyle.SP_MediaStop, QSize(18, 18), COLOR_ICON_ON_PRIMARY, self.style()))
            _repolish(btn)
            return
        if state == "stopping":
            btn.setEnabled(False)
            btn.setText("Parando…")
            btn.setObjectName("DangerButton")
            btn.setIcon(_nav_icon("fa5s.stop", QStyle.SP_MediaStop, QSize(18, 18), COLOR_ICON_ON_PRIMARY, self.style()))
            _repolish(btn)
            return

        btn.setEnabled(True)
        btn.setText("Iniciar gravação")
        btn.setObjectName("PrimaryButton")
        btn.setIcon(_nav_icon("fa5s.play", QStyle.SP_MediaPlay, QSize(18, 18), COLOR_ICON_ON_PRIMARY, self.style()))
        _repolish(btn)

    @Slot()
    def refresh_microphones(self) -> None:
        """Lista os microfones do Windows (DirectShow) em segundo plano."""

        def ok(mics: list) -> None:
            self._mics = list(mics)
            current = self.cmb_mic.currentData()
            self.cmb_mic.blockSignals(True)
            self.cmb_mic.clear()
            for m in self._mics:
                self.cmb_mic.addItem(m.label, m.dshow_name)
            if not self._mics:
                self.cmb_mic.addItem("Nenhum microfone detectado")
            else:
                idx = self.cmb_mic.findData(current) if current else -1
                self.cmb_mic.setCurrentIndex(idx if idx >= 0 else 0)
            self.cmb_mic.blockSignals(False)
            self.cmb_mic.setEnabled(
                bool(self._mics) and self.chk_record_mic.isChecked() and self.recording_state == "idle"
            )

        self.bg.run(list_microphones, ok, lambda _m: ok([]))

    def _selected_mic(self) -> tuple[str, str] | None:
        """(rótulo, nome DirectShow) do microfone escolhido, ou None."""
        if not self._mics or self.cmb_mic.currentIndex() < 0:
            return None
        data = self.cmb_mic.currentData()
        if not data:
            return None
        return self.cmb_mic.currentText().strip(), str(data)

    def _on_mic_toggled(self, checked: bool) -> None:
        self.cmb_mic.setEnabled(bool(checked) and bool(self._mics) and self.recording_state == "idle")
        if checked and not self._mics:
            self.refresh_microphones()

    @Slot()
    def _on_audio_source_ready(self) -> None:
        """O vídeo começa quando TODAS as fontes de áudio ligadas estiverem capturando."""
        self._audio_ready += 1
        if self._audio_ready >= self._audio_expected:
            self._begin_video_recording()

    def _stop_audio_recording(self) -> None:
        for thr in (self.audio_thread, self.mic_thread):
            if thr is not None:
                thr.stop()

    @Slot()
    def start_recording(self) -> None:
        if self.record_thread is not None or self.recording_state != "idle":
            return
        serial = self._require_serial()
        if not serial or not self.adb:
            return
        out_dir = self._ensure_output_dir("txt_out_gravar")
        if out_dir is None:
            return

        want_device_audio = bool(self.chk_record_audio.isChecked())
        want_mic = bool(self.chk_record_mic.isChecked())
        mic = self._selected_mic() if want_mic else None
        if want_mic and mic is None:
            QMessageBox.warning(
                self,
                APP_NAME,
                "O microfone do computador está ligado, mas nenhum microfone foi encontrado.\n\n"
                "Conecte/ative um microfone e clique em «Atualizar», ou desligue essa opção.",
            )
            return
        want_audio = want_device_audio or want_mic
        self._set_recording_state("starting")
        try:
            ts = time.strftime("%Y%m%d_%H%M%S")
            base = out_dir / f"record_{serial.replace(':', '_')}_{ts}"
            out = Path(str(base) + ".mp4")
            audio_out = Path(str(base) + ".wav") if want_device_audio else None
            mic_out = Path(str(base) + ".mic.wav") if mic is not None else None
            self._audio_delay_s = 0.0
            record_thread = ScreenRecordThread(
                adb_path=self.adb.adb_path,
                serial=serial,
                out_path=out,
                bit_rate=4000000,
                size=None,
                time_limit_s=180,
                started_at=time.monotonic(),
            )
            record_thread.status.connect(lambda msg: self._append(self.txt_gravar_out, msg))
            record_thread.finished.connect(self._on_recording_finished)
            record_thread.failed.connect(self._on_recording_failed)

            audio_thread = None
            if audio_out is not None:
                audio_thread = DeviceAudioRecordThread(self.adb.adb_path, serial, audio_out)
                audio_thread.status.connect(lambda msg: self._append(self.txt_gravar_out, msg))
                audio_thread.finished.connect(self._on_audio_finished)
                audio_thread.failed.connect(self._on_audio_failed)
                audio_thread.ready.connect(self._on_audio_source_ready)

            mic_thread = None
            if mic is not None and mic_out is not None:
                mic_thread = MicRecordThread(mic[1], mic_out, display_name=mic[0])
                mic_thread.status.connect(lambda msg: self._append(self.txt_gravar_out, msg))
                mic_thread.finished.connect(self._on_mic_finished)
                mic_thread.failed.connect(self._on_mic_failed)
                mic_thread.ready.connect(self._on_audio_source_ready)

            self.record_thread = record_thread
            self.audio_thread = audio_thread
            self.mic_thread = mic_thread
            self._audio_out_path = audio_out
            self._mic_out_path = mic_out
            self._audio_expected = int(audio_thread is not None) + int(mic_thread is not None)
            self._audio_ready = 0
            self._video_pending = True
            self._pending_serial = serial
            self._pending_want_audio = want_audio
            if audio_thread is not None or mic_thread is not None:
                # O áudio demora a preparar (instala o app auxiliar/scrcpy): o vídeo só começa quando
                # todas as fontes estiverem entrando, para não perder o som do início. Prazo de segurança.
                self._append(
                    self.txt_gravar_out,
                    "Preparando a captura de áudio… o vídeo começa em seguida.",
                )
                if audio_thread is not None:
                    audio_thread.start()
                if mic_thread is not None:
                    mic_thread.start()
                QTimer.singleShot(35000, self._begin_video_recording)
            else:
                self._begin_video_recording()
        except Exception as e:
            self._video_pending = False
            self.record_thread = None
            for thr in (self.audio_thread, self.mic_thread):
                if thr is not None:
                    thr.stop()
            self.audio_thread = None
            self.mic_thread = None
            self._audio_out_path = None
            self._mic_out_path = None
            self._set_recording_state("idle")
            self._err_dialog(f"Não foi possível iniciar a gravação: {e}")
            return

    @Slot()
    def _begin_video_recording(self) -> None:
        """Inicia o vídeo (uma única vez): chamado quando o áudio está pronto, falhou ou estourou o prazo."""
        if not self._video_pending or self.record_thread is None:
            return
        self._video_pending = False
        serial = self._pending_serial
        now = time.monotonic()
        self._record_started_at = now
        self.record_thread.started_at = now
        try:
            self.record_thread.start()
        except Exception as e:  # noqa: BLE001
            self._stop_audio_recording()
            self._on_recording_failed(f"Não foi possível iniciar a gravação: {e}")
            return
        self._set_recording_state("recording")
        self._start_recording_ui(serial)
        sources = []
        if self.audio_thread is not None:
            sources.append("áudio do Mini PC")
        if self.mic_thread is not None:
            sources.append("microfone do computador")
        if sources:
            self._append(
                self.txt_gravar_out,
                f"Gravando tela + {' + '.join(sources)}… o som entra no mesmo arquivo MP4.",
            )
        else:
            self._append(self.txt_gravar_out, "Gravando… clique em Parar gravação para encerrar.")

    @Slot()
    def stop_recording(self) -> None:
        if self.record_thread is None or self.recording_state in ("idle", "starting", "stopping"):
            return
        self._set_recording_state("stopping")
        # Só sinaliza: cada thread encerra seus próprios processos (a UI não congela).
        self._stop_audio_recording()
        self.record_thread.stop()
        self._append(self.txt_gravar_out, "Encerrando gravação… aguarde a finalização dos arquivos.")

    @Slot()
    def _on_audio_finished(self, path: str) -> None:
        try:
            size = Path(path).stat().st_size
            self._append(self.txt_gravar_out, f"Áudio do Mini PC capturado ({_fmt_bytes(size)}). Será mesclado no MP4.")
        except Exception:
            self._append(self.txt_gravar_out, "Áudio do Mini PC capturado. Será mesclado no MP4.")

    @Slot()
    def _on_audio_failed(self, msg: str) -> None:
        self._append(self.txt_gravar_out, f"Áudio: {msg}")
        # O áudio desistiu antes de o vídeo começar: segue sem ele (e avisa).
        if self._video_pending:
            self._append(self.txt_gravar_out, "Gravando o vídeo sem o áudio do aparelho.")
            self._on_audio_source_ready()

    @Slot()
    def _on_mic_finished(self, path: str) -> None:
        try:
            size = Path(path).stat().st_size
            self._append(self.txt_gravar_out, f"Microfone capturado ({_fmt_bytes(size)}). Será mesclado no MP4.")
        except Exception:
            self._append(self.txt_gravar_out, "Microfone capturado. Será mesclado no MP4.")

    @Slot()
    def _on_mic_failed(self, msg: str) -> None:
        self._append(self.txt_gravar_out, f"Microfone: {msg}")
        if self._video_pending:
            self._append(self.txt_gravar_out, "Gravando sem o microfone.")
            self._on_audio_source_ready()

    def _finish_recording_ok(self, path: str, *, with_audio: bool) -> None:
        self._set_recording_state("idle")
        self._stop_recording_ui()
        try:
            size = Path(path).stat().st_size
            prefix = "Vídeo com áudio salvo" if with_audio else "Vídeo salvo"
            self._append(self.txt_gravar_out, f"{prefix}: {path} • {_fmt_bytes(size)}")
        except Exception:
            self._append(self.txt_gravar_out, f"Vídeo salvo: {path}")

    def _mux_recorded_audio(self, video_path: str, sources: list) -> None:
        """sources: [(thread, arquivo_wav, rótulo)] — junta todas as faixas num só MP4."""
        video = Path(video_path)
        started = self._record_started_at
        self._append(self.txt_gravar_out, "Finalizando áudio e mesclando no vídeo…")

        def fn() -> tuple[str, str, list[str]]:
            notes: list[str] = []
            tracks: list[tuple[Path, float]] = []
            paths: list[Path] = [p for _t, p, _l in sources]
            # Espera fora da thread da UI cada captura fechar o WAV.
            for thr, path, label in sources:
                thr.wait(25000)
                if thr.isRunning():
                    notes.append(f"{label} demorou para finalizar e ficou de fora do MP4.")
                    continue
                try:
                    has_audio = path.exists() and path.stat().st_size > 1024
                except OSError:
                    has_audio = False
                if not has_audio:
                    notes.append(f"{label} não ficou pronto e ficou de fora do MP4.")
                    continue
                delay = 0.0
                if thr.audio_started_at is not None:
                    # ~0,6 s: tempo que o screenrecord leva até o primeiro quadro.
                    delay = thr.audio_started_at - started - 0.6  # negativo: áudio começou antes
                tracks.append((path, delay))
            if not tracks:
                return "noaudio", str(video), notes
            ffmpeg = _resolve_ffmpeg()
            if not ffmpeg:
                raise RuntimeError("FFmpeg não encontrado para incluir o áudio no MP4.")
            tmp = video.with_name(video.stem + ".mux.mp4")
            try:
                ok, err = mux_audios_into_mp4(ffmpeg, video, tracks, tmp)
                if not ok:
                    raise RuntimeError(err or "Falha ao mesclar o áudio no vídeo.")
                tmp.replace(video)
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass
            for p in paths:
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
            return "muxed", str(video), notes

        def ok(result: tuple[str, str, list[str]]) -> None:
            status, info, notes = result
            for n in notes:
                self._append(self.txt_gravar_out, n)
            if status == "muxed":
                self._finish_recording_ok(info, with_audio=True)
                return
            for _t, p, _l in sources:
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
            self._finish_recording_ok(video_path, with_audio=False)

        def err(msg: str) -> None:
            self._set_recording_state("idle")
            self._stop_recording_ui()
            self._append(self.txt_gravar_out, f"Vídeo salvo: {video_path}")
            for _t, p, _l in sources:
                self._append(self.txt_gravar_out, f"Áudio separado (mescla falhou): {p}")
            self._append(self.txt_gravar_out, msg)

        self.bg.run(fn, ok, err)

    def _take_audio_sources(self) -> list:
        """Retira (zera) as capturas de áudio ativas e devolve [(thread, wav, rótulo)]."""
        out = []
        if self.audio_thread is not None and self._audio_out_path is not None:
            out.append((self.audio_thread, self._audio_out_path, "O áudio do Mini PC"))
        if self.mic_thread is not None and self._mic_out_path is not None:
            out.append((self.mic_thread, self._mic_out_path, "O microfone do computador"))
        self.audio_thread = None
        self.mic_thread = None
        self._audio_out_path = None
        self._mic_out_path = None
        return out

    @Slot()
    def _on_recording_finished(self, path: str) -> None:
        thread = self.record_thread
        self.record_thread = None
        self._retire_thread(thread)
        sources = self._take_audio_sources()
        if not sources:
            self._finish_recording_ok(path, with_audio=False)
            return
        for thr, _p, _l in sources:
            thr.stop()
            self._retire_thread(thr)
        self._set_recording_state("stopping")
        self._mux_recorded_audio(path, sources)

    @Slot()
    def _on_recording_failed(self, msg: str) -> None:
        thread = self.record_thread
        self.record_thread = None
        self._retire_thread(thread)
        self._set_recording_state("idle")
        self._stop_recording_ui()
        for audio_thr, audio_path, _label in self._take_audio_sources():
            audio_thr.stop()
            self._retire_thread(audio_thr)

            def cleanup(t=audio_thr, p=audio_path) -> None:
                t.wait(20000)
                p.unlink(missing_ok=True)

            self.bg.run(cleanup, lambda _r: None, lambda _m: None)
        self._err_dialog(msg)

    @Slot()
    def _open_apks_dir(self) -> None:
        ensure_apks_tree()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.apks_dir)))

    @Slot()
    def refresh_apk_availability(self) -> None:
        ensure_apks_tree()
        missing = 0
        available = 0
        for cat, rows in getattr(self, "_apk_rows", {}).items():
            for entry, row in rows:
                path = resolve_apk(entry)
                if path.exists() and path.stat().st_size > 0:
                    available += 1
                    row.setEnabled(True)
                    row.toggle.setEnabled(True)
                    row.setToolTip(str(path))
                    row.setText(entry.label)
                else:
                    missing += 1
                    if row.isChecked():
                        row.setChecked(False)
                    row.setEnabled(True)
                    row.toggle.setEnabled(False)
                    row.setToolTip(f"Arquivo ausente: {path.name}\nColoque o APK em {path.parent}")
                    row.setText(f"{entry.label}  (ausente)")
            self._update_apk_accordion_label(cat)
        self._sync_autostart_checkbox()
        summary = f"APKs prontos: {available} • ausentes: {missing} • pasta: {self.apks_dir}"
        if summary != self._last_apk_avail_msg:  # não repete a mesma linha a cada atualização
            self._last_apk_avail_msg = summary
            self._append(self.txt_apps_out, summary)

    def _set_apk_sync_busy(self, busy: bool) -> None:
        self._apk_sync_busy = busy
        for name in ("btn_sync_apks", "btn_refresh_apks", "btn_install"):
            btn = getattr(self, name, None)
            if btn is not None:
                btn.setEnabled(not busy)

    def _auto_sync_apks(self) -> None:
        if not hasattr(self, "txt_apps_out"):
            return
        missing = 0
        for cat, rows in getattr(self, "_apk_rows", {}).items():
            for entry, _row in rows:
                path = resolve_apk(entry)
                if not path.is_file() or path.stat().st_size <= 0:
                    missing += 1
        if missing <= 0:
            return
        self._append(
            self.txt_apps_out,
            f"{missing} APK(s) ausente(s). Baixando do repositório de releases…",
        )
        self._sync_apks_from_repo(only_missing=True, silent_empty=True)

    @Slot()
    def sync_apks_from_repo(self) -> None:
        self._sync_apks_from_repo(only_missing=False, silent_empty=False)

    def _sync_apks_from_repo(self, *, only_missing: bool, silent_empty: bool) -> None:
        if self._apk_sync_busy or self._install_busy:
            if not silent_empty and hasattr(self, "txt_apps_out"):
                self._append(self.txt_apps_out, "Aguarde: já há download ou instalação de APKs em andamento.")
            return
        if not hasattr(self, "txt_apps_out"):
            return
        self._set_apk_sync_busy(True)
        self.progress_apps.setValue(0)
        self.lbl_apps_pct.setText("0%")

        def fn() -> ApkSyncResult:
            return sync_apks(only_missing=only_missing)

        def ok(result: ApkSyncResult) -> None:
            self._set_apk_sync_busy(False)
            if result.downloaded:
                self._append(
                    self.txt_apps_out,
                    f"APKs baixados: {result.downloaded} • já atualizados: {result.skipped}",
                )
            elif result.skipped and not silent_empty:
                self._append(self.txt_apps_out, "APKs já estão atualizados.")
            if result.failed:
                self._append(self.txt_apps_out, "Falha em: " + " | ".join(result.failed[:6]))
            self.refresh_apk_availability()

        def err(msg: str) -> None:
            self._set_apk_sync_busy(False)
            if silent_empty and "não publicado" in msg.lower():
                self._append(
                    self.txt_apps_out,
                    "Catálogo de APKs ainda não publicado. Use «Baixar APKs» depois.",
                )
                return
            self._append(self.txt_apps_out, msg)

        self.bg.run(fn, ok, err)

    def _checked_apk_entries(self) -> list[ApkEntry]:
        selected: list[ApkEntry] = []
        for cat in APK_CATEGORIES:
            rows = self._apk_rows.get(cat)
            if rows is None:
                continue
            for entry, row in rows:
                if row.isChecked() and row.toggle.isEnabled():
                    selected.append(entry)
        return selected

    @Slot()
    def install_selected_apks(self) -> None:
        if self._install_busy:
            QMessageBox.information(self, APP_NAME, "Já há uma instalação em andamento.")
            return
        serial = self._require_serial()
        if not serial or not self.adb:
            return
        entries = self._expand_install_dependencies(self._checked_apk_entries())
        # Mantém a UI alinhada com as dependências
        if any(e.category == "Painel" for e in entries):
            self._set_catalog_apk_checked(self._sintese_de_voz_entry(), True)
        self._sync_autostart_checkbox()
        if not entries:
            QMessageBox.warning(self, APP_NAME, "Ative ao menos um aplicativo disponível.")
            return
        missing = [e for e in entries if not self._apk_present(e)]
        if missing:
            names = ", ".join(e.label for e in missing)
            tts = self._sintese_de_voz_entry()
            if tts is not None and any(e.category == "Painel" for e in entries):
                if any(e.filename == tts.filename for e in missing):
                    QMessageBox.warning(
                        self,
                        APP_NAME,
                        "Apps de Painel precisam da Síntese de Voz.\n\n"
                        f"APKs ausentes: {names}\n\n"
                        "Clique em «Baixar APKs» para obter os arquivos do repositório.",
                    )
                    return
            autostart = self._autostart_entry()
            if autostart is not None and any(
                e.filename == autostart.filename for e in missing
            ):
                QMessageBox.warning(
                    self,
                    APP_NAME,
                    "A instalação inclui o Autostart (autoinício no Mini PC).\n\n"
                    f"APKs ausentes: {names}\n\n"
                    "Clique em «Baixar APKs» para obter os arquivos do repositório.",
                )
                return
            QMessageBox.warning(
                self,
                APP_NAME,
                f"APKs ausentes: {names}\n\nClique em «Baixar APKs» para baixar do repositório.",
            )
            return

        notes: list[str] = []
        if any(e.category == "Painel" for e in entries) and any(
            e.filename == "Sintese_de_Voz.apk" for e in entries
        ):
            notes.append("Síntese de Voz (Painel)")
        if any(not self._is_autostart_apk(e) for e in entries) and any(
            self._is_autostart_apk(e) for e in entries
        ):
            notes.append("Autostart + props de firmware")
        if notes:
            self._append(
                self.txt_apps_out,
                "Incluído automaticamente: " + ", ".join(notes) + ".",
            )
        self._install_queue = list(entries)
        self._install_busy = True
        self._install_total = len(entries)
        self._install_done = 0
        self._install_boot_packages = []
        self._install_boot_labels = {}
        self._install_configure_boot = any(self._wants_boot_autostart(e) for e in entries)
        self._install_need_tts_config = any(
            e.post_install == "tts_pt_br_voice_v" for e in entries
        )
        self._install_tts_apk_ok = False
        self._install_reboot_serial = serial if self.chk_reboot_after.isChecked() else None
        self._install_reboot_pkgs = []
        self._install_post_pending = bool(
            self._install_need_tts_config or self._install_configure_boot
        )
        self.btn_install.setEnabled(False)
        self._set_install_progress(0, animate=False)
        self._append(self.txt_apps_out, f"Fila: {self._install_total} app(s). Iniciando…")
        if self._install_need_tts_config or self._install_configure_boot:
            self._append(
                self.txt_apps_out,
                "Fases: 1) instalar APKs → 2) Síntese de Voz (se Painel) → "
                "3) autoinício (firmware + Autostart.apk).",
            )
        self._install_next_in_queue(serial)

    def _on_install_progress_frame(self, value: int) -> None:
        self.lbl_apps_pct.setText(f"{int(value)}%")

    def _on_install_phase_progress(self, percent: int, status: str) -> None:
        # Nunca 100% enquanto a pós-instalação estiver em andamento
        cap = 99 if self._install_post_pending else 100
        self._set_install_progress(max(0, min(cap, int(percent))), animate=True)
        if status:
            self._append(self.txt_apps_out, status)

    def _set_install_progress(self, percent: int, *, animate: bool = True) -> None:
        target = max(0, min(100, int(percent)))
        if self._install_post_pending:
            target = min(target, 99)
        if self._install_progress_anim is None:
            self.progress_apps.setValue(target)
            self.lbl_apps_pct.setText(f"{target}%")
            return
        self._install_progress_anim.stop()
        if not animate:
            self.progress_apps.setValue(target)
            self.lbl_apps_pct.setText(f"{target}%")
            return
        self._install_progress_anim.setStartValue(self.progress_apps.value())
        self._install_progress_anim.setEndValue(target)
        self._install_progress_anim.start()

    def _install_progress_percent(self) -> int:
        return install_apk_phase_percent(
            self._install_done,
            self._install_total,
            has_post_install=self._install_post_pending,
        )

    @staticmethod
    def _apk_present(entry) -> bool:
        try:
            p = resolve_apk(entry)
            return p.is_file() and p.stat().st_size > 0
        except OSError:
            return False

    def _guarded(self, cb, on_fatal):
        """Envolve um callback de fila: exceção inesperada nunca deixa a UI travada."""
        def wrapper(*args):
            try:
                cb(*args)
            except Exception as exc:  # noqa: BLE001
                on_fatal(exc)

        return wrapper

    def _install_fatal(self, exc: Exception) -> None:
        self._append(self.txt_apps_out, f"Erro interno na fila de instalação: {exc}")
        self._finish_install_queue(complete=False)

    def _uninstall_fatal(self, exc: Exception) -> None:
        self._append(self.txt_uninstall_out, f"Erro interno na desinstalação: {exc}")
        self._uninstall_queue = []
        self._uninstall_busy = False
        self.btn_uninstall_refresh.setEnabled(True)
        self.btn_uninstall_run.setEnabled(True)

    def _install_next_in_queue(self, serial: str) -> None:
        try:
            self._install_next_in_queue_impl(serial)
        except Exception as exc:  # noqa: BLE001
            self._install_fatal(exc)

    def _install_next_in_queue_impl(self, serial: str) -> None:
        if not self._install_queue:
            need_tts = self._install_need_tts_config
            tts_apk_ok = self._install_tts_apk_ok
            need_boot = self._install_configure_boot
            pkgs = list(dict.fromkeys(self._install_boot_packages)) if need_boot else []
            self._install_reboot_pkgs = list(pkgs)
            self._install_need_tts_config = False
            self._install_configure_boot = False

            if need_tts and not tts_apk_ok:
                # Ainda tenta configurar se o motor já existir no aparelho
                self._append(
                    self.txt_apps_out,
                    "Síntese de Voz: APK não confirmado na fila — "
                    "tentando configurar se o motor já estiver no Mini PC…",
                )

            if need_tts or (need_boot and pkgs):
                self._append(self.txt_apps_out, "Pós-instalação: TTS e/ou autoinício…")
                boot_labels = dict(self._install_boot_labels)
                report = self._install_phase_progress.emit

                def post_fn() -> tuple[str, bool]:
                    assert self.adb is not None
                    parts: list[str] = []
                    complete = True

                    self.adb.set_autostart_apps_enabled(serial, False)
                    try:
                        tts_ok = True
                        if need_tts:
                            report(62, "=== FASE: Síntese de Voz (idioma → país → Voz V) ===")
                            tts: TtsConfigResult = self.adb.configure_tts_pt_br_voice_v(
                                serial,
                                manage_display=False,
                                on_status=lambda m: report(70, m),
                            )
                            parts.append(tts.message)
                            tts_ok = tts.voice_v_confirmed
                            if not tts_ok:
                                complete = False
                                parts.append(
                                    "AutoStart NÃO iniciado: a Voz V não foi confirmada."
                                )
                                report(75, "Voz V não confirmada — AutoStart adiado.")
                            else:
                                report(80, "Voz V confirmada.")

                        if need_boot and pkgs and tts_ok:
                            report(82, "=== FASE: Autoinício (firmware + Autostart) ===")
                            boot: AutostartConfigResult = self.adb.configure_boot_autostart(
                                serial, pkgs, labels=boot_labels, manage_display=False
                            )
                            parts.append(boot.message)
                            if not boot.ok:
                                complete = False
                                report(90, "AutoStart: configuração incompleta.")
                            else:
                                report(95, "AutoStart confirmado.")
                        elif need_boot and pkgs and not tts_ok:
                            complete = False

                        if complete:
                            report(98, "Validação final…")
                    finally:
                        # Idempotente: devolve o Autostart ao estado habilitado mesmo se a Síntese de Voz
                        # falhar (senão o totem não abriria o app após reiniciar).
                        try:
                            self.adb.set_autostart_apps_enabled(serial, True)
                        except Exception:
                            pass
                        self.adb.close_settings_and_tts_ui(
                            serial, stop_tts_engine=False
                        )

                    msg = "\n".join(parts) if parts else "Pós-instalação: nada a configurar."
                    return msg, complete

                def post_ok(result: tuple[str, bool]) -> None:
                    msg, complete = result
                    self._append(self.txt_apps_out, msg)
                    self._finish_install_queue(complete=complete)

                def post_err(msg: str) -> None:
                    self._append(self.txt_apps_out, f"Pós-instalação: falha — {msg}")
                    self._finish_install_queue(complete=False)

                self.bg.run(
                    post_fn,
                    self._guarded(post_ok, self._install_fatal),
                    self._guarded(post_err, self._install_fatal),
                )
                return

            if need_boot and not pkgs:
                self._append(
                    self.txt_apps_out,
                    "Autoinício: não foi possível identificar os packages dos APKs instalados.",
                )
            self._finish_install_queue(complete=True)
            return

        entry = self._install_queue.pop(0)
        apk = resolve_apk(entry)
        current = self._install_done + 1
        self._append(
            self.txt_apps_out,
            f"Instalando [{entry.category}] {entry.label}… ({current}/{self._install_total})",
        )
        if entry.post_install == "tts_pt_br_voice_v":
            self._append(
                self.txt_apps_out,
                "Após o APK: configuração rápida da Síntese de Voz "
                "(Português → Brasil → Voz V).",
            )

        def fn() -> tuple[str, str | None]:
            assert self.adb is not None

            pkg_name = None
            if self._wants_boot_autostart(entry):
                pkg_name = self.adb.package_name_from_apk(apk)
            if entry.post_install == "tts_pt_br_voice_v":
                msg = self.adb.install_tts_apk(serial, apk)
            else:
                msg = self.adb.install_apk(serial, apk)
            if self._wants_boot_autostart(entry) and not pkg_name:
                pkg_name = self.adb.package_name_from_apk(apk)
            return msg, pkg_name

        def ok(result: tuple[str, str | None]) -> None:
            msg, pkg_name = result
            self._append(self.txt_apps_out, f"OK • {entry.label}: {msg}")
            if entry.post_install == "tts_pt_br_voice_v":
                self._install_tts_apk_ok = True
            if self._wants_boot_autostart(entry) and pkg_name:
                if pkg_name not in self._install_boot_packages:
                    self._install_boot_packages.append(pkg_name)
                    self._install_boot_labels[pkg_name] = entry.label
                    self._append(self.txt_apps_out, f"Autoinício: marcado {entry.label} ({pkg_name})")
            elif self._wants_boot_autostart(entry) and not pkg_name:
                self._append(
                    self.txt_apps_out,
                    f"Autoinício: não leu o package de {entry.filename} — "
                    "confira aapt/APK.",
                )
            self._install_done += 1
            self._set_install_progress(self._install_progress_percent(), animate=True)
            self._install_next_in_queue(serial)

        def err(msg: str) -> None:
            self._append(self.txt_apps_out, f"Falha • {entry.label}: {msg}")
            if entry.post_install == "tts_pt_br_voice_v":
                self._append(
                    self.txt_apps_out,
                    "Síntese de Voz: instalação do APK falhou — "
                    "a configuração de voz pode não completar.",
                )
            self._install_done += 1
            self._set_install_progress(self._install_progress_percent(), animate=True)
            self._install_next_in_queue(serial)

        self.bg.run(
            fn,
            self._guarded(ok, self._install_fatal),
            self._guarded(err, self._install_fatal),
        )

    def _finish_install_queue(self, *, complete: bool = True) -> None:
        reboot_serial = self._install_reboot_serial
        reboot_pkgs = list(self._install_reboot_pkgs)
        self._install_reboot_serial = None
        self._install_reboot_pkgs = []
        if reboot_serial and not complete:
            self._append(
                self.txt_apps_out,
                "O equipamento NÃO foi reiniciado: a instalação ficou incompleta. Resolva a pendência e reinstale.",
            )
        self._install_busy = False
        self._install_configure_boot = False
        self._install_need_tts_config = False
        self._install_tts_apk_ok = False
        self._install_post_pending = False
        self.btn_install.setEnabled(True)
        if complete:
            self._set_install_progress(100, animate=True)
            self._append(self.txt_apps_out, "Instalação concluída.")
        else:
            stuck = min(99, self.progress_apps.value())
            self._set_install_progress(stuck, animate=False)
            self._append(
                self.txt_apps_out,
                "Instalação incompleta: a barra não chega a 100% enquanto "
                "a Voz V ou o AutoStart não forem confirmados.",
            )
        self.refresh_apk_availability()
        if complete and reboot_serial and reboot_pkgs and self.adb is not None:
            self._start_reboot_validation(reboot_serial, reboot_pkgs)

    def _start_reboot_validation(self, serial: str, pkgs: list[str]) -> None:
        """Reinicia o aparelho e confere o Autostart em segundo plano (bloqueia outras ações)."""
        adb = self.adb
        if adb is None:
            return
        address = self.wifi_connected_address if ":" in serial else None
        self._reboot_busy = True
        self.btn_install.setEnabled(False)
        self._append(self.txt_apps_out, "Último passo: reiniciar o equipamento e validar o Autostart.")
        report = self._reboot_status.emit

        def fn():
            return wait_and_validate(
                adb, serial, address, pkgs, report, should_stop=lambda: self._closing
            )

        def done(result) -> None:
            self._reboot_busy = False
            self.btn_install.setEnabled(True)
            prefix = "✔ " if result.ok else "⚠ "
            self._append(self.txt_apps_out, prefix + result.message)
            self.refresh_apk_availability()

        def fail(msg: str) -> None:
            self._reboot_busy = False
            self.btn_install.setEnabled(True)
            self._append(self.txt_apps_out, f"⚠ Falha ao reiniciar/validar: {msg}")

        self.bg.run(fn, done, fail)

    @Slot()
    def install_apk(self) -> None:
        """Compatível com testes/smoke: sem seleção, só valida dispositivo."""
        self.install_selected_apks()

    def _cleanup_after_update(self) -> None:
        if not is_frozen():
            return
        try:
            from .updater import cleanup_old_backups, cleanup_update_leftovers, install_dir_for_update

            install_dir = install_dir_for_update()
            if install_dir is None:
                return

            def fn() -> None:
                cleanup_update_leftovers(install_dir)
                cleanup_old_backups(install_dir, keep=0)

            self.bg.run(fn, lambda _r: None, lambda _m: None)
        except Exception:
            pass

    def _retire_thread(self, thr) -> None:
        """Mantém a referência de uma QThread até ela terminar de verdade (sem wait na UI)."""
        if thr is None:
            return
        if thr not in self._retired_threads:
            self._retired_threads.append(thr)
        if not self._thread_reaper.isActive():
            self._thread_reaper.start()

    def _reap_threads(self) -> None:
        self._retired_threads = [t for t in self._retired_threads if t.isRunning()]
        if not self._retired_threads:
            self._thread_reaper.stop()

    def _active_operations(self) -> list[str]:
        ops: list[str] = []
        if self.recording_state != "idle" or self.record_thread is not None:
            ops.append("gravação de tela")
        if self._install_busy:
            ops.append("instalação de APKs")
        if self._reboot_busy:
            ops.append("reinício do equipamento")
        if self._uninstall_busy:
            ops.append("desinstalação")
        if self._optimize_busy:
            ops.append("otimização")
        if self._factory_reset_busy:
            ops.append("restauração de fábrica")
        if getattr(self, "_update_busy", False):
            ops.append("atualização do app")
        return ops

    def _shutdown_work(self) -> None:
        """Encerra timers, logcat e gravação (síncrono; usado ao fechar o app)."""
        for t in (self.wifi_monitor_timer, self.recording_timer):
            try:
                t.stop()
            except Exception:
                pass
        try:
            self.stop_logcat()
        except Exception:
            pass
        threads = [t for t in (self.record_thread, self.audio_thread, self.mic_thread) if t is not None]
        for t in threads:
            try:
                t.stop()
            except Exception:
                pass
        for t in threads + list(self._retired_threads):
            try:
                if not t.wait(35000):
                    t.terminate()
                    t.wait(2000)
            except Exception:
                pass
        self.record_thread = None
        self.audio_thread = None
        self.mic_thread = None
        self._retired_threads.clear()
        try:
            self.bg.wait_idle(3000)
        except Exception:
            pass

    def _device_busy(self) -> str | None:
        """Descrição da operação em andamento que impede ações destrutivas, ou None."""
        if self.recording_state != "idle" or self.record_thread is not None:
            return "uma gravação de tela"
        if self._install_busy:
            return "uma instalação de APK"
        if self._reboot_busy:
            return "o reinício do equipamento"
        if self._uninstall_busy:
            return "uma desinstalação"
        if self._optimize_busy:
            return "uma otimização"
        if self._factory_reset_busy:
            return "uma restauração de fábrica"
        return None

    def _adb_restart_unsafe(self) -> bool:
        """True enquanto reiniciar o servidor ADB derrubaria uma operação em curso."""
        return self._device_busy() is not None or self.logcat_thread is not None

    @Slot()
    def start_factory_reset(self) -> None:
        serial = self._require_serial()
        if not serial or not self.adb:
            return
        busy = self._device_busy()
        if busy:
            QMessageBox.warning(
                self,
                APP_NAME,
                f"Há {busy} em andamento. Aguarde terminar antes de restaurar o aparelho.",
            )
            return
        dev = self._selected_device()
        dev_label = dev.label if dev else serial

        confirm = QMessageBox.warning(
            self,
            APP_NAME,
            f"Atenção: a restauração de fábrica apaga TODOS os dados do dispositivo\n«{dev_label}» ({serial}) "
            "e não pode ser desfeita.\n\nDeseja continuar?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        typed, ok = QInputDialog.getText(
            self,
            "Confirmar restauração",
            f"Digite RESET para apagar «{dev_label}» ({serial}):",
        )
        if not ok or typed.strip().upper() != "RESET":
            QMessageBox.information(self, APP_NAME, "Restauração cancelada.")
            return

        self.btn_factory_reset.setEnabled(False)
        self._factory_reset_busy = True
        self._reset_requested_at = time.monotonic()
        self._reset_notified = False
        self._append(self.txt_reset_out, f"Iniciando restauração de fábrica em {serial}…")
        self._append(
            self.txt_reset_out,
            "Abrindo a tela de redefinição e confirmando automaticamente…",
        )

        def fn() -> tuple[str, bool]:
            assert self.adb is not None
            return self.adb.factory_reset(serial)

        def ok_cb(result: tuple[str, bool]) -> None:
            self._factory_reset_busy = False
            msg, should_disconnect = result
            self._append(self.txt_reset_out, msg)
            if should_disconnect:
                self._notify_reset_in_progress(serial)
            else:
                self._append(
                    self.txt_reset_out,
                    "A confirmação foi enviada. Se o aparelho começar a reiniciar (a tela apaga e a "
                    "conexão cai), é a restauração em andamento — o Aibox avisa quando ele desconectar. "
                    "Se nada acontecer, confirme «Redefinir» na tela do aparelho.",
                )
            self.btn_factory_reset.setEnabled(True)

        def err_cb(msg: str) -> None:
            self._factory_reset_busy = False
            self._reset_requested_at = None
            self.btn_factory_reset.setEnabled(True)
            # factory_reset() já confirma a queda real do aparelho; erro aqui = falha de verdade.
            self._append(self.txt_reset_out, f"Falha: {msg}")
            self._err_dialog(msg)

        self.bg.run(fn, ok_cb, err_cb)

    _RESET_GRACE_S = 900.0
    _RESET_FRIENDLY_TEXT = (
        "A restauração de fábrica foi iniciada e o aparelho desconectou.\n\n"
        "Isso é esperado: ele está apagando os dados e reiniciando, e também perde a configuração "
        "de Wi‑Fi e a Depuração USB/sem fio.\n\n"
        "Quando terminar, configure o Wi‑Fi e ative a depuração no aparelho e depois reconecte em «Conectar»."
    )

    def _reset_recent(self) -> bool:
        """True durante/logo depois de um reset pedido: a queda de conexão é esperada, não um erro."""
        at = self._reset_requested_at
        return at is not None and (time.monotonic() - at) < self._RESET_GRACE_S

    def _notify_reset_in_progress(self, serial: str) -> None:
        """Mensagem amigável (uma vez) quando o aparelho some depois de um reset."""
        if self._reset_notified:
            return
        self._reset_notified = True
        self._reset_requested_at = None
        self._after_factory_reset(serial)
        QMessageBox.information(self, APP_NAME, self._RESET_FRIENDLY_TEXT)

    def _after_factory_reset(self, serial: str) -> None:
        """Desconecta o dispositivo do Aibox após o reset."""
        try:
            if self.wifi_connected_address and (
                self.wifi_connected_address == serial or serial in (self.wifi_connected_address or "")
            ):
                try:
                    if self.adb:
                        self.adb.disconnect(self.wifi_connected_address)
                except Exception:
                    pass
                self.wifi_connected_address = None
                self._set_wifi_state("disconnected")
            elif self.wifi_state == "connected":
                self.wifi_connected_address = None
                self._set_wifi_state("disconnected")
        except Exception:
            pass

        self.devices = []
        if hasattr(self, "cmb_device"):
            self.cmb_device.blockSignals(True)
            self.cmb_device.clear()
            self.cmb_device.blockSignals(False)

        self._append(self.txt_reset_out, "Dispositivo desconectado do Aibox. Atualize a lista em Conectar quando ele voltar.")
        # Atualiza a lista sem diálogo de erro (o device some durante o wipe)
        QTimer.singleShot(1500, self._refresh_devices_after_reset)

    def _refresh_devices_after_reset(self) -> None:
        if not self.adb:
            return

        def fn() -> list[AdbDevice]:
            assert self.adb is not None
            return self.adb.list_devices()

        def ok(devs: list[AdbDevice]) -> None:
            self.devices = devs
            self.cmb_device.blockSignals(True)
            self.cmb_device.clear()
            for d in devs:
                self.cmb_device.addItem(d.label, d.serial)
            if self.cmb_device.count() > 0:
                self.cmb_device.setCurrentIndex(0)
            self.cmb_device.blockSignals(False)

        def err(_msg: str) -> None:
            self._append(
                self.txt_reset_out,
                "Lista de dispositivos ainda indisponível (normal durante a reinicialização).",
            )

        self.bg.run(fn, ok, err)

    @Slot()
    def start_logcat(self) -> None:
        if self.logcat_thread is not None:
            return
        serial = self._require_serial()
        if not serial or not self.adb:
            return

        try:
            popen = self.adb.start_logcat(serial, extra_args=["-v", "time"])
        except AdbError as e:
            self._err_dialog(str(e))
            return

        thr = LogcatThread(popen)
        self.logcat_thread = thr
        thr.line.connect(lambda ln: self._append(self.txt_logcat, ln))
        thr.failed.connect(self._err_dialog)
        thr.stopped.connect(lambda t=thr: self._on_logcat_stopped(t))
        thr.start()

    def _on_logcat_stopped(self, thr) -> None:
        # Só zera se ainda for a thread atual (um "stopped" atrasado não pode soltar a nova).
        if self.logcat_thread is thr:
            self.logcat_thread = None

    @Slot()
    def stop_logcat(self) -> None:
        thr = self.logcat_thread
        if thr is None:
            return
        self.logcat_thread = None
        thr.stop()
        self._retire_thread(thr)

    def closeEvent(self, event) -> None:
        if not self._closing:
            ops = self._active_operations()
            if ops:
                ans = QMessageBox.question(
                    self,
                    APP_NAME,
                    "Há operação em andamento: " + ", ".join(ops) + ".\n\n"
                    "Sair agora interrompe o processo (uma gravação em curso é finalizada e salva).\n"
                    "Deseja sair mesmo assim?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if ans != QMessageBox.Yes:
                    event.ignore()
                    return
            self._closing = True
        self._shutdown_work()
        super().closeEvent(event)
