# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller — Aibox-Setup.exe (um único instalador windowed)."""

from __future__ import annotations

from pathlib import Path

SPEC_DIR = Path(SPECPATH).resolve()
ROOT = SPEC_DIR.parent

PAYLOAD = ROOT / "build" / "installer_payload" / "aibox_payload.zip"
ICON_ICO = ROOT / "aibox" / "Aibox.ico"
ICON_PNG = ROOT / "aibox" / "Aibox.png"
SETUP_SCRIPT = SPEC_DIR / "setup_main.py"

datas = []
if PAYLOAD.exists():
    datas.append((str(PAYLOAD), "."))
if ICON_PNG.exists():
    datas.append((str(ICON_PNG), "."))
if ICON_ICO.exists():
    datas.append((str(ICON_ICO), "."))

hiddenimports = ["tkinter", "tkinter.ttk", "_tkinter"]

a = Analysis(
    [str(SETUP_SCRIPT)],
    pathex=[str(ROOT), str(SPEC_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6", "qtawesome", "pytest", "matplotlib", "numpy", "pandas"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

icon = ICON_ICO if ICON_ICO.exists() else ICON_PNG
exe_kwargs = {
    "name": "Aibox-Setup",
    "debug": False,
    "bootloader_ignore_signals": False,
    "strip": False,
    "upx": False,
    "console": False,
    "disable_windowed_traceback": False,
    "argv_emulation": False,
    "target_arch": None,
    "codesign_identity": None,
    "entitlements_file": None,
}
if icon.exists():
    exe_kwargs["icon"] = str(icon)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    exclude_binaries=False,
    **exe_kwargs,
)
