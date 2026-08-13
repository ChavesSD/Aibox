"""Execução de processos no Windows sem janela de console."""
from __future__ import annotations

import subprocess
import sys
from typing import Any

# CREATE_NO_WINDOW — evita cmd.exe/adb/ffmpeg piscando no app windowed (PyInstaller).
CREATE_NO_WINDOW = 0x08000000


def hidden_kwargs(**extra: Any) -> dict[str, Any]:
    """Kwargs para subprocess.run/Popen que ocultam o console no Windows."""
    kwargs: dict[str, Any] = dict(extra)
    if sys.platform != "win32":
        return kwargs
    flags = int(kwargs.get("creationflags") or 0) | CREATE_NO_WINDOW
    kwargs["creationflags"] = flags
    if "startupinfo" not in kwargs:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        kwargs["startupinfo"] = si
    return kwargs


def run_hidden(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
    return subprocess.run(*args, **hidden_kwargs(**kwargs))


def popen_hidden(*args: Any, **kwargs: Any) -> subprocess.Popen:
    return subprocess.Popen(*args, **hidden_kwargs(**kwargs))
