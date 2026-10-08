# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — Aibox (onedir, windowed)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).resolve()
PKG = ROOT / "aibox"
sys.path.insert(0, str(ROOT))
from aibox.theme import APP_VERSION
from installer.versioninfo import write_version_file

# ADB mínimo necessário no Windows (sem NOTICE gigante / tools extras).
_ADB_FILES = (
    "adb.exe",
    "AdbWinApi.dll",
    "AdbWinUsbApi.dll",
    "libwinpthread-1.dll",
)

datas: list[tuple[str, str]] = []
for name in ("Aibox.ico", "Aibox.png", "Logo para tema escuro.png"):
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

scrcpy_src = PKG / "scrcpy"
scrcpy_dst = "aibox/scrcpy"
if not (scrcpy_src / "scrcpy.exe").is_file():
    raise SystemExit(
        "scrcpy ausente em aibox/scrcpy. Rode python build_exe.py "
        "(baixa o scrcpy automaticamente para gravar áudio do Mini PC)."
    )
for f in sorted(scrcpy_src.iterdir()):
    if not f.is_file():
        continue
    if f.name in ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll"):
        continue
    binaries.append((str(f), scrcpy_dst))

ffmpeg_src = PKG / "ffmpeg"
if not (ffmpeg_src / "ffmpeg.exe").is_file():
    raise SystemExit(
        "ffmpeg ausente em aibox/ffmpeg. Rode python build_exe.py "
        "(baixa o ffmpeg automaticamente: gravação com áudio e validação de MP4 dependem dele)."
    )
binaries.append((str(ffmpeg_src / "ffmpeg.exe"), "aibox/ffmpeg"))
for _name in ("LICENSE.txt", "README.txt", "SOURCE.txt"):
    _f = ffmpeg_src / _name
    if _f.is_file():
        datas.append((str(_f), "aibox/ffmpeg"))

datas += collect_data_files("qtawesome")

hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "qtawesome",
    "aibox.procutil",
    "aibox.usbwin",
    "aibox.apk_sync",
    "aibox.device_audio",
    "aibox.ziputil",
    "aibox.github_fetch",
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

version_file = write_version_file(
    ROOT / "build" / "version" / "Aibox_version.txt",
    version=APP_VERSION,
    filename="Aibox.exe",
    description="Aibox — Intelite",
)
exe_kwargs["version"] = str(version_file)
manifest = ROOT / "installer" / "Aibox.exe.manifest"
if manifest.is_file():
    exe_kwargs["manifest"] = str(manifest)

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
