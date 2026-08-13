# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — Aibox (onedir, windowed)."""

from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).resolve()
PKG = ROOT / "aibox"

# ADB mínimo necessário no Windows (sem NOTICE gigante / tools extras).
_ADB_FILES = (
    "adb.exe",
    "AdbWinApi.dll",
    "AdbWinUsbApi.dll",
    "libwinpthread-1.dll",
)

datas: list[tuple[str, str]] = []
for name in ("Aibox.png", "Logo para tema escuro.png"):
    src = PKG / name
    if src.exists():
        datas.append((str(src), "aibox"))

pt_src = PKG / "platform-tools"
pt_dst = "aibox/platform-tools"
binaries: list[tuple[str, str]] = []
required_adb = ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll")
missing_adb = [n for n in required_adb if not (pt_src / n).is_file()]
if missing_adb:
    raise SystemExit(
        "ADB USB incompleto em aibox/platform-tools: "
        + ", ".join(missing_adb)
        + ". Rode python build_exe.py (baixa o platform-tools automaticamente)."
    )
for name in _ADB_FILES:
    f = pt_src / name
    if f.is_file():
        binaries.append((str(f), pt_dst))
for f in sorted(pt_src.glob("*.dll")):
    pair = (str(f), pt_dst)
    if pair not in binaries:
        binaries.append(pair)

datas += collect_data_files("qtawesome")

hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "qtawesome",
    "aibox.procutil",
    "aibox.usbwin",
    "aibox.apk_sync",
]

a = Analysis(
    [str(ROOT / "run_aibox.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "PIL",
        "pytest",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

icon = PKG / "Aibox.ico"
if not icon.exists():
    icon = PKG / "Aibox.png"
exe_kwargs = {
    "name": "Aibox",
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
    [],
    exclude_binaries=True,
    **exe_kwargs,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Aibox",
)
