"""Extração de ZIP com validação (zip-slip, ADS do NTFS, nomes reservados, zip-bomb)."""
from __future__ import annotations

import re
import zipfile
from pathlib import Path, PurePosixPath

MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB descompactado
MAX_FILES = 20000

_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')


class UnsafeZipError(RuntimeError):
    pass


def _check_member(name: str) -> None:
    raw = name.replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise UnsafeZipError(f"Caminho absoluto no ZIP: {name!r}")
    for part in PurePosixPath(raw).parts:
        if part in ("", ".", ".."):
            if part == "..":
                raise UnsafeZipError(f"Caminho com '..' no ZIP: {name!r}")
            continue
        if _BAD_CHARS.search(part):
            raise UnsafeZipError(f"Nome inválido no ZIP (':' ou caractere de controle): {name!r}")
        stem = part.split(".", 1)[0].rstrip(" ").lower()
        if stem in _RESERVED:
            raise UnsafeZipError(f"Nome reservado do Windows no ZIP: {name!r}")
        if part.endswith((" ", ".")):
            raise UnsafeZipError(f"Nome terminado em espaço/ponto no ZIP: {name!r}")


def validate_zip(zf: zipfile.ZipFile, *, max_total: int = MAX_TOTAL_BYTES, max_files: int = MAX_FILES) -> None:
    infos = zf.infolist()
    if len(infos) > max_files:
        raise UnsafeZipError(f"ZIP com arquivos demais ({len(infos)}).")
    total = 0
    for info in infos:
        _check_member(info.filename)
        # bit 0xA000 em external_attr (Unix) = symlink
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise UnsafeZipError(f"Link simbólico no ZIP: {info.filename!r}")
        total += info.file_size
        if total > max_total:
            raise UnsafeZipError("ZIP descompactado excede o limite de tamanho.")


def safe_extract_zip(zf: zipfile.ZipFile, dest: Path, **limits: int) -> None:
    """Valida todos os membros e extrai garantindo que nada saia de `dest`."""
    validate_zip(zf, **limits)
    root = Path(dest).resolve()
    for info in zf.infolist():
        target = (root / info.filename.replace("\\", "/")).resolve()
        if target != root and root not in target.parents:
            raise UnsafeZipError(f"Entrada fora da pasta de destino: {info.filename!r}")
    zf.extractall(root)
