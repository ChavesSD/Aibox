"""Gera o recurso de versão do Windows (Properties → Detalhes) para o PyInstaller."""
from __future__ import annotations

from pathlib import Path


def version_tuple(version: str) -> tuple[int, int, int, int]:
    parts: list[int] = []
    for piece in version.strip().lstrip("v").replace("-", ".").split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    while len(parts) < 4:
        parts.append(0)
    return (parts[0], parts[1], parts[2], parts[3])


def write_version_file(
    dest: Path,
    *,
    version: str,
    filename: str,
    product: str = "Aibox",
    company: str = "Intelite",
    description: str = "Aibox",
) -> Path:
    major, minor, patch, build = version_tuple(version)
    dotted = f"{major}.{minor}.{patch}.{build}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, {build}),
    prodvers=({major}, {minor}, {patch}, {build}),
    mask=0x3F,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '041604B0',
        [
          StringStruct('CompanyName', {company!r}),
          StringStruct('FileDescription', {description!r}),
          StringStruct('FileVersion', {dotted!r}),
          StringStruct('InternalName', {product!r}),
          StringStruct('LegalCopyright', 'Copyright (C) {company}'),
          StringStruct('OriginalFilename', {filename!r}),
          StringStruct('ProductName', {product!r}),
          StringStruct('ProductVersion', {dotted!r}),
        ],
      ),
    ]),
    VarFileInfo([VarStruct('Translation', [1046, 1200])]),
  ],
)
""",
        encoding="utf-8",
    )
    return dest
