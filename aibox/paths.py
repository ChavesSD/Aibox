from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def package_dir() -> Path:
    """Pasta do pacote `aibox` (dev ou bundle)."""
    here = Path(__file__).resolve().parent
    if here.exists():
        return here
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled = Path(meipass) / "aibox"
            if bundled.exists():
                return bundled
    return here


def executable_dir() -> Path:
    """Pasta do executável (ou cwd em modo script)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def resource_path(*parts: str | Path) -> Path:
    """Caminho de recurso empacotado ao lado do pacote."""
    return package_dir().joinpath(*parts)


def app_icon_path() -> Path | None:
    """Ícone da janela/tarefa: .ico nativo; PNG só se o .ico não existir."""
    ico = resource_path("Aibox.ico")
    if ico.is_file():
        return ico
    png = resource_path("Aibox.png")
    if png.is_file():
        return png
    return None

