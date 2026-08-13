from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .paths import executable_dir, is_frozen
from .theme import APP_DIR_NAME, APP_VERSION, DEFAULT_UPDATE_MANIFEST_URL


ProgressCb = Callable[[int, int], None]  # (downloaded, total) — total 0 se desconhecido


class UpdateError(RuntimeError):
    pass


class ManifestNotFound(UpdateError):
    """Nenhum manifesto/release publicado (ex.: HTTP 404)."""


@dataclass(frozen=True)
class ReleaseNotes:
    added: list[str] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    sha256: str
    size: int = 0


@dataclass(frozen=True)
class UpdateManifest:
    version: str
    notes: ReleaseNotes
    asset: ReleaseAsset
    min_version: str = "0.0.0"
    channel: str = "stable"
    published_at: str = ""


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    remote: UpdateManifest
    update_available: bool


_SEMVER_RE = re.compile(
    r"^\s*v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?\s*$"
)


def parse_semver(version: str) -> tuple[int, int, int, str]:
    m = _SEMVER_RE.match(version or "")
    if not m:
        raise UpdateError(f"Versão inválida: {version!r}")
    return (
        int(m.group("major")),
        int(m.group("minor")),
        int(m.group("patch")),
        m.group("pre") or "",
    )


def compare_semver(a: str, b: str) -> int:
    """Retorna -1 se a<b, 0 se iguais, 1 se a>b (pre-release < release)."""
    am, an, ap, apre = parse_semver(a)
    bm, bn, bp, bpre = parse_semver(b)
    if (am, an, ap) != (bm, bn, bp):
        return -1 if (am, an, ap) < (bm, bn, bp) else 1
    if apre == bpre:
        return 0
    if not apre:
        return 1
    if not bpre:
        return -1
    return -1 if apre < bpre else 1


def is_newer(remote: str, current: str) -> bool:
    return compare_semver(remote, current) > 0


def manifest_url() -> str:
    return (os.environ.get("AIBOX_UPDATE_MANIFEST_URL") or DEFAULT_UPDATE_MANIFEST_URL).strip()


def updates_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    d = base / APP_DIR_NAME / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def parse_manifest(data: dict) -> UpdateManifest:
    try:
        notes_raw = data.get("notes") or {}
        notes = ReleaseNotes(
            added=list(notes_raw.get("added") or []),
            fixed=list(notes_raw.get("fixed") or []),
            removed=list(notes_raw.get("removed") or []),
        )
        asset_raw = data["asset"]
        asset = ReleaseAsset(
            name=str(asset_raw["name"]),
            url=str(asset_raw["url"]),
            sha256=str(asset_raw["sha256"]).lower().strip(),
            size=int(asset_raw.get("size") or 0),
        )
        return UpdateManifest(
            version=str(data["version"]).lstrip("v"),
            notes=notes,
            asset=asset,
            min_version=str(data.get("min_version") or "0.0.0").lstrip("v"),
            channel=str(data.get("channel") or "stable"),
            published_at=str(data.get("published_at") or ""),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise UpdateError(f"Manifesto inválido: {e}") from e


def fetch_manifest(url: str | None = None, *, timeout_s: float = 20.0) -> UpdateManifest:
    target = url or manifest_url()
    req = urllib.request.Request(
        target,
        headers={"User-Agent": f"Aibox/{APP_VERSION}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            raise ManifestNotFound(
                "Nenhuma atualização publicada no servidor."
            ) from e
        raise UpdateError(f"Falha ao buscar manifesto (HTTP {e.code}).") from e
    except urllib.error.URLError as e:
        raise UpdateError(f"Sem conexão ao buscar atualizações: {e.reason}") from e
    except TimeoutError as e:
        raise UpdateError("Timeout ao buscar atualizações.") from e

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise UpdateError("Manifesto não é JSON válido.") from e
    if not isinstance(data, dict):
        raise UpdateError("Manifesto deve ser um objeto JSON.")
    return parse_manifest(data)


def _synthetic_current_manifest(version: str) -> UpdateManifest:
    return UpdateManifest(
        version=version,
        notes=ReleaseNotes(),
        asset=ReleaseAsset(name="", url="", sha256="", size=0),
        min_version=version,
        channel="stable",
        published_at="",
    )


def check_for_updates(
    *,
    current_version: str | None = None,
    url: str | None = None,
) -> UpdateCheckResult:
    current = (current_version or APP_VERSION).lstrip("v")
    try:
        remote = fetch_manifest(url)
    except ManifestNotFound:
        # Sem release publicado ⇒ não há versão mais nova para o usuário.
        return UpdateCheckResult(
            current_version=current,
            remote=_synthetic_current_manifest(current),
            update_available=False,
        )
    if compare_semver(current, remote.min_version) < 0:
        raise UpdateError(
            f"Esta instalação ({current}) é anterior ao mínimo suportado "
            f"({remote.min_version}). Baixe o Aibox manualmente."
        )
    return UpdateCheckResult(
        current_version=current,
        remote=remote,
        update_available=is_newer(remote.version, current),
    )


def sha256_file(path: Path, *, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def download_asset(
    asset: ReleaseAsset,
    dest: Path,
    *,
    progress: ProgressCb | None = None,
    timeout_s: float = 60.0,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".part")
    if partial.exists():
        partial.unlink()

    req = urllib.request.Request(
        asset.url,
        headers={"User-Agent": f"Aibox/{APP_VERSION}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            total = int(resp.headers.get("Content-Length") or asset.size or 0)
            downloaded = 0
            with partial.open("wb") as out:
                while True:
                    block = resp.read(256 * 1024)
                    if not block:
                        break
                    out.write(block)
                    downloaded += len(block)
                    if progress:
                        progress(downloaded, total)
    except urllib.error.HTTPError as e:
        raise UpdateError(f"Falha no download (HTTP {e.code}).") from e
    except urllib.error.URLError as e:
        raise UpdateError(f"Falha no download: {e.reason}") from e

    partial.replace(dest)
    if progress and asset.size:
        progress(asset.size, asset.size)
    return dest


def verify_sha256(path: Path, expected: str) -> None:
    got = sha256_file(path)
    if got.lower() != expected.lower().strip():
        raise UpdateError(
            f"Checksum inválido.\nEsperado: {expected}\nObtido: {got}"
        )


def stage_update_package(
    manifest: UpdateManifest,
    *,
    progress: ProgressCb | None = None,
) -> Path:
    dest = updates_dir() / manifest.asset.name
    download_asset(manifest.asset, dest, progress=progress)
    verify_sha256(dest, manifest.asset.sha256)
    return dest


def install_dir_for_update() -> Path | None:
    """Pasta onedir do EXE (None se não estiver empacotado)."""
    if not is_frozen():
        return None
    return executable_dir()


def helper_argv(
    *,
    pid: int,
    install_dir: Path,
    package: Path,
    restart: bool = True,
) -> list[str]:
    args = [
        sys.executable,
        "--update-helper",
        "--pid",
        str(pid),
        "--install-dir",
        str(install_dir),
        "--package",
        str(package),
    ]
    if restart:
        args.append("--restart")
    return args


def launch_update_helper(
    *,
    pid: int,
    install_dir: Path,
    package: Path,
    restart: bool = True,
) -> None:
    args = helper_argv(pid=pid, install_dir=install_dir, package=package, restart=restart)
    creationflags = 0
    if sys.platform == "win32":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        creationflags = 0x00000008 | 0x00000200
    subprocess.Popen(
        args,
        cwd=str(install_dir),
        close_fds=True,
        creationflags=creationflags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def format_notes_html(notes: ReleaseNotes) -> str:
    def _ul(items: list[str]) -> str:
        if not items:
            return "<p style='margin:4px 0;color:#8b92a3'>—</p>"
        lis = "".join(f"<li>{_escape(i)}</li>" for i in items)
        return f"<ul style='margin:4px 0 8px 18px;padding:0'>{lis}</ul>"

    parts = [
        "<b>Adicionado</b>",
        _ul(notes.added),
        "<b>Corrigido</b>",
        _ul(notes.fixed),
        "<b>Removido</b>",
        _ul(notes.removed),
    ]
    return "".join(parts)


def format_notes_plain(notes: ReleaseNotes) -> str:
    def _block(title: str, items: list[str]) -> str:
        if not items:
            return f"{title}\n  —"
        lines = "\n".join(f"  • {i}" for i in items)
        return f"{title}\n{lines}"

    return "\n\n".join(
        [
            _block("Adicionado", notes.added),
            _block("Corrigido", notes.fixed),
            _block("Removido", notes.removed),
        ]
    )


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def cleanup_old_backups(install_dir: Path, *, keep: int = 1) -> None:
    parent = install_dir.parent
    name = install_dir.name
    backups = sorted(
        parent.glob(f"{name}.bak-*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in backups[keep:]:
        try:
            if old.is_dir():
                shutil.rmtree(old, ignore_errors=True)
            else:
                old.unlink(missing_ok=True)
        except OSError:
            pass
