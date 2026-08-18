from __future__ import annotations

APP_NAME = "Aibox"
APP_DIR_NAME = "Aibox"
APP_VERSION = "1.0.5"

# Manifestos no repositório de releases (fonte + instalador + APKs).
# Sobrescreva com AIBOX_UPDATE_MANIFEST_URL / AIBOX_APKS_MANIFEST_URL.
DEFAULT_UPDATE_MANIFEST_URL = (
    "https://raw.githubusercontent.com/ChavesSD/ReleasesAibox/main/latest.json"
)
DEFAULT_APKS_MANIFEST_URL = (
    "https://raw.githubusercontent.com/ChavesSD/ReleasesAibox/main/apks.json"
)

# Cores extraídas do padrão visual CCleaner
COLOR_BG = "#1a1d26"
COLOR_SIDEBAR = "#1a1d26"
COLOR_SURFACE = "#252934"
COLOR_SURFACE_2 = "#2c313c"
COLOR_BORDER = "#3a4050"
COLOR_BORDER_SOFT = "#2f3542"
COLOR_PRIMARY = "#3d7eff"
COLOR_PRIMARY_HOVER = "#5b91ff"
COLOR_PRIMARY_PRESSED = "#2f66d6"
COLOR_ACCENT = "#3d7eff"
COLOR_TEXT = "#ffffff"
COLOR_SUBTLE = "#a0a4b0"
COLOR_TITLE = "#ffffff"
COLOR_SECTION = "#ffffff"
COLOR_DANGER = "#e11d48"
COLOR_DANGER_HOVER = "#fb2d5a"
COLOR_DANGER_PRESSED = "#be123c"
COLOR_DISABLED = "#6b7385"
COLOR_ICON = "#c5cad6"
COLOR_ICON_ON_PRIMARY = "#ffffff"
COLOR_NAV_ACTIVE = "#2a3140"
COLOR_FOOTER = "#1f232d"
COLOR_OUTLINE = "#d8dde8"


def aibox_stylesheet() -> str:
    return f"""
QWidget {{
  background: {COLOR_BG};
  color: {COLOR_TEXT};
  font-family: "Segoe UI";
  font-size: 13px;
}}

QMainWindow {{
  background: {COLOR_BG};
}}

QLabel {{
  background: transparent;
  color: {COLOR_TEXT};
}}

QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {{
  background: {COLOR_SURFACE_2};
  border: 1px solid {COLOR_BORDER};
  border-radius: 8px;
  padding: 8px 10px;
  selection-background-color: {COLOR_PRIMARY};
  selection-color: {COLOR_TITLE};
  color: {COLOR_TEXT};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
  border: 1px solid {COLOR_PRIMARY};
}}

QSpinBox {{
  background: {COLOR_SURFACE_2};
  border: 1px solid {COLOR_BORDER};
  border-radius: 8px;
  padding: 8px 10px;
  selection-background-color: {COLOR_PRIMARY};
  selection-color: {COLOR_TITLE};
  color: {COLOR_TEXT};
  min-height: 22px;
}}
QSpinBox:focus {{
  border: 1px solid {COLOR_PRIMARY};
}}
QSpinBox::up-button, QSpinBox::down-button {{
  width: 0px;
  height: 0px;
  border: 0px;
}}

QToolButton#SpinStepButton {{
  background: {COLOR_SURFACE_2};
  border: 1px solid {COLOR_BORDER};
  border-radius: 6px;
  color: {COLOR_TITLE};
  font-size: 11px;
  font-weight: 700;
  padding: 0px;
  min-width: 28px;
  max-width: 28px;
  min-height: 18px;
  max-height: 18px;
}}
QToolButton#SpinStepButton:hover {{
  background: {COLOR_NAV_ACTIVE};
  border-color: {COLOR_PRIMARY};
}}
QToolButton#SpinStepButton:pressed {{
  background: {COLOR_PRIMARY};
}}

QComboBox::drop-down {{
  border: 0px;
  width: 28px;
}}
QComboBox QAbstractItemView {{
  background: {COLOR_SURFACE};
  border: 1px solid {COLOR_BORDER};
  selection-background-color: {COLOR_PRIMARY};
  selection-color: {COLOR_TITLE};
  outline: 0;
}}

QListWidget {{
  background: {COLOR_SURFACE_2};
  border: 1px solid {COLOR_BORDER};
  border-radius: 8px;
  padding: 4px;
  outline: 0;
}}
QListWidget::item {{
  padding: 6px 8px;
  border-radius: 6px;
  color: {COLOR_TEXT};
  font-size: 12px;
  min-height: 22px;
}}
QListWidget::item:selected {{
  background: {COLOR_NAV_ACTIVE};
  color: {COLOR_TITLE};
}}
QListWidget::item:hover {{
  background: rgba(255,255,255,0.05);
}}
QListWidget::indicator {{
  width: 16px;
  height: 16px;
}}
QListWidget::indicator:unchecked {{
  border: 1px solid {COLOR_BORDER};
  border-radius: 3px;
  background: transparent;
}}
QListWidget::indicator:checked {{
  border: 1px solid {COLOR_PRIMARY};
  border-radius: 3px;
  background: {COLOR_PRIMARY};
}}

QLabel#ToggleRowLabel {{
  background: transparent;
  color: {COLOR_TEXT};
  font-size: 13px;
  font-weight: 600;
}}
QLabel#ToggleRowLabel:disabled {{
  color: {COLOR_DISABLED};
}}
QWidget#ToggleRow {{
  background: transparent;
  min-height: 28px;
  max-height: 30px;
}}
QScrollArea#ApkScroll {{
  background: transparent;
  border: 0px;
}}
QWidget#ApkScrollInner {{
  background: transparent;
}}

QToolButton#AccordionButton {{
  background: {COLOR_SURFACE_2};
  border: 1px solid {COLOR_BORDER};
  border-radius: 8px;
  padding: 3px 10px;
  color: {COLOR_TEXT};
  font-size: 13px;
  font-weight: 600;
  text-align: left;
  min-height: 14px;
}}
QToolButton#AccordionButton:hover {{
  background: #323845;
  border-color: {COLOR_PRIMARY};
}}
QToolButton#AccordionButton:checked {{
  background: {COLOR_NAV_ACTIVE};
  border-color: {COLOR_PRIMARY};
  color: {COLOR_TITLE};
}}

QFrame#AccordionBody {{
  background: transparent;
  border: 0px;
  margin: 0px;
  padding: 0px;
}}

/* Botão padrão = secundário CCleaner (pill + contorno claro) */
QPushButton {{
  background: transparent;
  border: 1px solid {COLOR_OUTLINE};
  border-radius: 18px;
  padding: 8px 16px;
  color: {COLOR_TITLE};
  font-weight: 600;
  min-height: 18px;
}}
QPushButton:hover {{
  background: rgba(255,255,255,0.06);
  border-color: #ffffff;
}}
QPushButton:pressed {{
  background: rgba(255,255,255,0.10);
}}
QPushButton:disabled {{
  color: {COLOR_DISABLED};
  border-color: {COLOR_BORDER};
  background: transparent;
}}

QPushButton#PrimaryButton {{
  background: {COLOR_PRIMARY};
  border: 1px solid {COLOR_PRIMARY};
  color: {COLOR_TITLE};
  font-weight: 700;
  padding: 8px 18px;
  border-radius: 18px;
}}
QPushButton#PrimaryButton:hover {{
  background: {COLOR_PRIMARY_HOVER};
  border-color: {COLOR_PRIMARY_HOVER};
}}
QPushButton#PrimaryButton:pressed {{
  background: {COLOR_PRIMARY_PRESSED};
  border-color: {COLOR_PRIMARY_PRESSED};
}}

QPushButton#SecondaryButton {{
  background: transparent;
  border: 1px solid {COLOR_OUTLINE};
  color: {COLOR_TITLE};
  border-radius: 18px;
  padding: 8px 16px;
  font-weight: 600;
}}
QPushButton#SecondaryButton:hover {{
  background: rgba(255,255,255,0.06);
  border-color: #ffffff;
}}

QPushButton#DangerButton {{
  background: {COLOR_DANGER};
  border: 1px solid {COLOR_DANGER};
  color: #ffffff;
  font-weight: 700;
  border-radius: 18px;
  padding: 8px 18px;
}}
QPushButton#DangerButton:hover {{
  background: {COLOR_DANGER_HOVER};
  border-color: {COLOR_DANGER_HOVER};
}}
QPushButton#DangerButton:pressed {{
  background: {COLOR_DANGER_PRESSED};
}}

QFrame#Sidebar {{
  background: {COLOR_SIDEBAR};
  border: 0px;
  border-right: 1px solid #12151c;
  border-radius: 0px;
}}

QLabel#BrandTitle {{
  background: transparent;
  color: {COLOR_TITLE};
  font-size: 14px;
  font-weight: 700;
}}
QLabel#BrandSub {{
  background: transparent;
  color: {COLOR_SUBTLE};
  font-size: 11px;
  font-weight: 500;
}}
QLabel#SidebarFooterLogo {{
  background: transparent;
  border: 0px;
}}
QFrame#SidebarSectionBlock {{
  background: transparent;
  border: 0px;
}}
QFrame#SidebarSectionHead {{
  background: transparent;
  border: 0px;
}}
QFrame#SidebarSectionRule {{
  background: {COLOR_BORDER};
  border: 0px;
  max-height: 1px;
}}
QLabel#SidebarSection {{
  background: transparent;
  color: {COLOR_TEXT};
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1.1px;
  padding: 0px 2px 2px 2px;
}}
QFrame#SidebarNavGroup {{
  background: {COLOR_SURFACE};
  border: 1px solid {COLOR_BORDER_SOFT};
  border-radius: 10px;
}}
QFrame#SidebarSobreWrap {{
  background: transparent;
  border: 0px;
}}
QLabel#PageTitle {{
  background: transparent;
  color: {COLOR_TITLE};
  font-size: 22px;
  font-weight: 600;
  letter-spacing: 0.6px;
  padding: 0px;
}}
QLabel#PageSubtitle {{
  background: transparent;
  color: {COLOR_SUBTLE};
  font-size: 12px;
  font-weight: 400;
  letter-spacing: 0.25px;
  padding: 0px;
  max-width: 520px;
}}
QWidget#PageHeader {{
  background: transparent;
  border: 0px;
  border-bottom: 1px solid {COLOR_BORDER_SOFT};
}}
QWidget#PageHeaderCopy {{
  background: transparent;
}}
QFrame#PageAccent {{
  background: {COLOR_PRIMARY};
  border: 0px;
  border-radius: 2px;
}}
QLabel#SectionTitle {{
  background: transparent;
  color: {COLOR_TITLE};
  font-size: 14px;
  font-weight: 700;
}}
QLabel#PageTip {{
  background: transparent;
  color: {COLOR_SUBTLE};
  font-size: 12px;
  font-weight: 500;
  padding: 0px 0px 2px 0px;
}}
QLabel#FieldLabel {{
  background: transparent;
  color: {COLOR_SUBTLE};
  font-size: 12px;
  font-weight: 600;
}}
QLabel#Title {{
  background: transparent;
  font-size: 16px;
  font-weight: 700;
  color: {COLOR_TITLE};
}}
QFrame#OutlinedField {{
  background: transparent;
  border: 1px solid {COLOR_BORDER};
  border-radius: 8px;
}}
QFrame#OutlinedField[focused="true"] {{
  border: 1px solid {COLOR_PRIMARY};
}}
QLabel#OutlinedFieldLabel {{
  background: {COLOR_BG};
  color: {COLOR_SUBTLE};
  font-size: 11px;
  font-weight: 600;
  padding: 0px 6px;
  border: 0px;
}}
QLabel#OutlinedFieldLabel[focused="true"] {{
  color: {COLOR_PRIMARY};
}}
QWidget#OutlinedFieldHost {{
  background: transparent;
}}
QLineEdit#OutlinedInner, QComboBox#OutlinedInner, QSpinBox#OutlinedInner, QTextEdit#OutlinedInner {{
  background: transparent;
  border: 0px;
  padding: 2px 0px;
  color: {COLOR_TEXT};
  selection-background-color: {COLOR_PRIMARY};
  selection-color: {COLOR_TITLE};
}}
QComboBox#OutlinedInner::drop-down {{
  border: 0px;
  width: 24px;
}}
QSpinBox#OutlinedInner::up-button, QSpinBox#OutlinedInner::down-button {{
  width: 0px;
  height: 0px;
  border: 0px;
}}

QLabel#Subtle {{
  background: transparent;
  color: {COLOR_SUBTLE};
}}

QLabel#PreviewFrame {{
  background: {COLOR_SURFACE_2};
  border: 1px solid {COLOR_BORDER};
  border-radius: 10px;
  color: {COLOR_SUBTLE};
}}

QLabel#RecordTimerValue {{
  background: transparent;
  color: {COLOR_TITLE};
  font-family: "Bahnschrift", "Segoe UI";
  font-size: 18px;
  font-weight: 600;
  letter-spacing: 1px;
  padding: 0px 8px;
}}
QFrame#RecordTimerBar {{
  background: {COLOR_SURFACE};
  border: 1px solid {COLOR_BORDER};
  border-radius: 12px;
  min-height: 44px;
}}
QFrame#RecordTimerBar[recording="true"] {{
  background: #241a1e;
  border: 1px solid #5c2430;
}}
QLabel#RecordRecBadge {{
  background: {COLOR_SURFACE_2};
  color: {COLOR_SUBTLE};
  border: 1px solid {COLOR_BORDER};
  border-radius: 8px;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 1px;
  min-width: 44px;
  max-width: 44px;
  padding: 6px 0px;
}}
QLabel#RecordRecBadge[recording="true"] {{
  background: {COLOR_DANGER};
  border-color: {COLOR_DANGER};
  color: #ffffff;
}}

QToolButton#NavButton {{
  background: transparent;
  border: 0px;
  border-radius: 0px;
  padding: 8px 14px;
  min-height: 36px;
  font-size: 13px;
  font-weight: 600;
  color: {COLOR_SUBTLE};
  text-align: left;
}}
QToolButton#NavButton:hover {{
  background: rgba(255,255,255,0.04);
  color: {COLOR_TEXT};
}}
QToolButton#NavButton:checked {{
  background: {COLOR_NAV_ACTIVE};
  color: {COLOR_TITLE};
}}

QFrame#MainArea {{
  background: {COLOR_BG};
}}

QWidget#PageRoot {{
  background: transparent;
}}

QStackedWidget#Pages {{
  background: transparent;
}}

QFrame#ContentCard {{
  background: transparent;
  border: 0px;
  border-radius: 0px;
}}

QFrame#InfoPanel {{
  background: {COLOR_SURFACE_2};
  border: 1px solid {COLOR_BORDER_SOFT};
  border-radius: 10px;
}}
QLabel#InfoPanelText {{
  background: transparent;
  color: {COLOR_SUBTLE};
  font-size: 12px;
  font-weight: 500;
  line-height: 1.35;
}}

QFrame#ActionBar {{
  background: {COLOR_FOOTER};
  border: 0px;
  border-top: 1px solid {COLOR_BORDER_SOFT};
}}

QProgressBar#InstallProgress {{
  background: {COLOR_SURFACE_2};
  border: 1px solid {COLOR_BORDER};
  border-radius: 7px;
  text-align: center;
  color: {COLOR_TITLE};
  min-height: 12px;
  max-height: 14px;
}}
QProgressBar#InstallProgress::chunk {{
  background: {COLOR_PRIMARY};
  border-radius: 6px;
  margin: 1px;
}}
QLabel#InstallProgressPct {{
  background: transparent;
  color: {COLOR_SUBTLE};
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.3px;
}}

QPushButton#UpgradeButton {{
  background: #ffc107;
  border: 1px solid #ffc107;
  color: #1a1d26;
  font-weight: 700;
  border-radius: 18px;
  padding: 8px 18px;
}}
QPushButton#UpgradeButton:hover {{
  background: #ffcd38;
  border-color: #ffcd38;
}}

QGroupBox {{
  border: 0px;
  background: transparent;
  margin-top: 0px;
  padding: 0px;
}}
QGroupBox::title {{
  subcontrol-origin: margin;
  left: 0px;
  padding: 0px;
  color: {COLOR_TITLE};
  background: transparent;
  font-weight: 700;
}}

QScrollBar:vertical {{
  border: 0px;
  background: transparent;
  width: 10px;
}}
QScrollBar::handle:vertical {{
  background: #3a4050;
  border-radius: 5px;
  min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
  background: {COLOR_PRIMARY};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
  height: 0px;
}}

QScrollBar:horizontal {{
  border: 0px;
  background: transparent;
  height: 10px;
}}
QScrollBar::handle:horizontal {{
  background: #3a4050;
  border-radius: 5px;
  min-width: 28px;
}}

QMessageBox {{
  background: {COLOR_SURFACE};
}}
QMessageBox QLabel {{
  background: transparent;
  color: {COLOR_TEXT};
}}
"""
