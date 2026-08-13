from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .paths import executable_dir, is_frozen
from .theme import APP_DIR_NAME, APP_NAME


@dataclass(frozen=True)
class ApkEntry:
    category: str  # Totem | Painel | Outros
    label: str
    filename: str  # nome esperado do arquivo .apk
    post_install: str | None = None  # ex.: "tts_pt_br_voice_v"

    @property
    def relative_path(self) -> Path:
        return Path(self.category) / self.filename


# Nomes fixos: substita o arquivo mantendo exatamente o filename.
APK_CATALOG: tuple[ApkEntry, ...] = (
    ApkEntry("Totem", "Atendimento Inteligente", "Atendimento_Inteligente.apk"),
    ApkEntry("Totem", "Totem Interativo", "Totem_Interativo.apk"),
    ApkEntry("Totem", "Upzz", "Upzz.apk"),
    ApkEntry("Painel", "Atendimento Inteligente", "Atendimento_Inteligente.apk"),
    ApkEntry("Painel", "Painel Cirurgico", "Painel_Cirurgico.apk"),
    ApkEntry("Painel", "Aiclass", "Aiclass.apk"),
    ApkEntry("Painel", "Ainurse", "Ainurse.apk"),
    ApkEntry("Painel", "Aifit", "Aifit.apk"),
    ApkEntry("Outros", "Sintese de Voz", "Sintese_de_Voz.apk", post_install="tts_pt_br_voice_v"),
    ApkEntry("Outros", "ADB Wi-fi", "ADB_Wifi.apk"),
    ApkEntry("Outros", "Autostart", "Autostart.apk"),
)

APK_CATEGORIES: tuple[str, ...] = ("Totem", "Painel", "Outros")

# Instalação padrão no Windows (Setup.exe).
DEFAULT_INSTALL_DIR = Path(r"C:\Aibox")


def install_root() -> Path:
    """Pasta do Aibox instalado (ao lado do .exe) ou ~/Aibox em desenvolvimento."""
    if is_frozen():
        return executable_dir()
    return Path.home() / APP_DIR_NAME


def apks_root() -> Path:
    """Pasta de APKs: prioriza instalação (C:\\Aibox\\Apks), depois perfil do usuário."""
    candidates: list[Path] = []
    if is_frozen():
        candidates.append(executable_dir() / "Apks")
        candidates.append(DEFAULT_INSTALL_DIR / "Apks")
    candidates.append(Path.home() / APP_DIR_NAME / "Apks")

    # Preferência: primeira pasta que já tem algum .apk do catálogo
    for root in candidates:
        if any((root / e.relative_path).is_file() for e in APK_CATALOG):
            return root

    # Senão, pasta canônica da instalação/dev (será criada)
    return candidates[0]


def ensure_apks_tree() -> Path:
    root = apks_root()
    root.mkdir(parents=True, exist_ok=True)
    for cat in APK_CATEGORIES:
        (root / cat).mkdir(parents=True, exist_ok=True)

    readme = root / "LEIA-ME.txt"
    lines = [
        f"Pasta de APKs do {APP_NAME}",
        "",
        "Os APKs de Totem, Painel e Outros são baixados automaticamente do repositório",
        "de releases após a instalação (não vêm dentro do Setup.exe).",
        "",
        "Pasta local:",
        f"  {root}",
        "",
        "Use «Baixar APKs» no Aibox para atualizar. Não é preciso copiar arquivos à mão.",
        "",
    ]
    for cat in APK_CATEGORIES:
        lines.append(f"[{cat}]")
        for entry in APK_CATALOG:
            if entry.category == cat:
                lines.append(f"  - {entry.label}: {entry.filename}")
        lines.append("")
    readme.write_text("\n".join(lines), encoding="utf-8")
    return root


def entries_for(category: str) -> list[ApkEntry]:
    return [e for e in APK_CATALOG if e.category == category]


def resolve_apk(entry: ApkEntry) -> Path:
    """Resolve o .apk: instalação empacotada primeiro, depois ~/Aibox/Apks."""
    primary = apks_root() / entry.relative_path
    if primary.is_file():
        return primary
    # Fallback explícito (útil se apks_root() escolheu pasta vazia)
    for root in (
        executable_dir() / "Apks",
        DEFAULT_INSTALL_DIR / "Apks",
        Path.home() / APP_DIR_NAME / "Apks",
    ):
        p = root / entry.relative_path
        if p.is_file():
            return p
    return primary
