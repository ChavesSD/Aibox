"""Sincroniza APKs do repositório de releases (ReleasesAibox)."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .apks_catalog import APK_CATALOG, APK_CATEGORIES, ApkEntry, apks_root, ensure_apks_tree
from .github_fetch import (
    MAX_APK_BYTES,
    RemoteFetchError,
    github_api_file as _github_api_file,
    is_allowed_url,
    http_get as _shared_http_get,
    relative_from_url as _relative_from_url,
    releases_mirror_urls,
    rewrite_url_to_working_host as _rewrite_url_to_working_host,
)
from .theme import DEFAULT_APKS_MANIFEST_URL
from .updater import ReleaseAsset, UpdateError, download_asset, sha256_file, verify_sha256

ProgressCb = Callable[[int, int], None]
ItemCb = Callable[[str], None]


class ApksManifestNotFound(UpdateError):
    pass


_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_APK_FILENAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\.apk")


@dataclass(frozen=True)
class RemoteApk:
    category: str
    filename: str
    label: str
    sha256: str
    size: int
    url: str

    @property
    def relative_path(self) -> Path:
        return Path(self.category) / self.filename

    @property
    def key(self) -> str:
        return f"{self.category}/{self.filename}"


@dataclass(frozen=True)
class ApksManifest:
    version: str
    updated_at: str
    apks: tuple[RemoteApk, ...]


@dataclass(frozen=True)
class ApkSyncItem:
    remote: RemoteApk
    local: Path
    status: str  # current | missing | outdated


@dataclass(frozen=True)
class ApkSyncResult:
    downloaded: int
    skipped: int
    failed: list[str]


def apks_manifest_url() -> str:
    return (os.environ.get("AIBOX_APKS_MANIFEST_URL") or DEFAULT_APKS_MANIFEST_URL).strip()


def parse_repo_asset_name(name: str) -> tuple[str, str] | None:
    """Nome no GitHub (Totem-Upzz.apk) → (categoria, arquivo do catálogo)."""
    name = (name or "").strip()
    if not name.lower().endswith(".apk") or "-" not in name:
        return None
    category, filename = name.split("-", 1)
    if category not in APK_CATEGORIES or not filename:
        return None
    return category, filename


def _connection_error(reason: object) -> UpdateError:
    text = str(reason or "")
    if "11001" in text or "getaddrinfo" in text.lower():
        return UpdateError(
            "Sem conexão ao buscar APKs: o Windows não resolveu o endereço do GitHub "
            "(DNS). Verifique internet, DNS e se raw.githubusercontent.com não está bloqueado."
        )
    return UpdateError(f"Sem conexão ao buscar APKs: {reason}")


def _http_get(url: str, *, timeout_s: float, accept: str) -> tuple[bytes, str]:
    try:
        return _shared_http_get(url, timeout_s=timeout_s, accept=accept)
    except RemoteFetchError as e:
        if e.only_missing:
            raise ApksManifestNotFound("Catálogo de APKs ainda não publicado.") from e
        if e.rate_limited:
            raise UpdateError(
                "O GitHub limitou as requisições ao baixar APKs. Espere um minuto e tente de novo."
            ) from e
        code = e.http_codes[-1] if e.http_codes else None
        if code and code not in (403, 404, 410, 429, 502, 503):
            raise UpdateError(f"Falha ao buscar catálogo de APKs (HTTP {code}).") from e
        raise _connection_error(e.last_reason or e) from e


def parse_apks_manifest(data: dict) -> ApksManifest:
    try:
        raw_list = data.get("apks")
        if not isinstance(raw_list, list) or not raw_list:
            raise UpdateError("Manifesto de APKs sem lista 'apks'.")
        apks: list[RemoteApk] = []
        rejected = 0
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            apk = RemoteApk(
                category=str(item["category"]).strip(),
                filename=str(item["filename"]).strip(),
                label=str(item.get("label") or item["filename"]).strip(),
                sha256=str(item["sha256"]).lower().strip(),
                size=int(item.get("size") or 0),
                url=str(item["url"]).strip(),
            )
            # Entrada fora do padrão (path traversal, sem checksum, host estranho) é descartada.
            if (
                apk.category not in APK_CATEGORIES
                or not _APK_FILENAME_RE.fullmatch(apk.filename)
                or not _SHA256_RE.fullmatch(apk.sha256)
                or not is_allowed_url(apk.url)
                or apk.size < 0
                or apk.size > MAX_APK_BYTES
            ):
                rejected += 1
                continue
            apks.append(apk)
        if not apks:
            raise UpdateError(
                "Manifesto de APKs vazio ou sem entradas válidas"
                + (f" ({rejected} rejeitada(s): categoria, nome, checksum ou URL inválidos)." if rejected else ".")
            )
        return ApksManifest(
            version=str(data.get("version") or "0.0.0").lstrip("v"),
            updated_at=str(data.get("updated_at") or ""),
            apks=tuple(apks),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise UpdateError(f"Manifesto de APKs inválido: {e}") from e


def fetch_apks_manifest(url: str | None = None, *, timeout_s: float = 20.0) -> ApksManifest:
    target = url or apks_manifest_url()
    try:
        raw, working = _http_get(target, timeout_s=timeout_s, accept="application/json")
    except TimeoutError as e:
        raise UpdateError("Timeout ao buscar catálogo de APKs.") from e
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise UpdateError("Catálogo de APKs não é JSON válido.") from e
    if not isinstance(payload, dict):
        raise UpdateError("Catálogo de APKs deve ser um objeto JSON.")
    manifest = parse_apks_manifest(payload)
    rewritten = tuple(
        RemoteApk(
            category=apk.category,
            filename=apk.filename,
            label=apk.label,
            sha256=apk.sha256,
            size=apk.size,
            url=_rewrite_url_to_working_host(apk.url, working)
            if not working.startswith("github-api:")
            else apk.url,
        )
        for apk in manifest.apks
    )
    return ApksManifest(
        version=manifest.version,
        updated_at=manifest.updated_at,
        apks=rewritten,
    )


def _local_sha(path: Path) -> str | None:
    if not path.is_file() or path.stat().st_size <= 0:
        return None
    try:
        return sha256_file(path)
    except OSError:
        return None


def plan_apk_sync(manifest: ApksManifest, *, root: Path | None = None) -> list[ApkSyncItem]:
    dest_root = root or ensure_apks_tree()
    catalog_keys = {f"{e.category}/{e.filename}" for e in APK_CATALOG}
    items: list[ApkSyncItem] = []
    for remote in manifest.apks:
        if catalog_keys and remote.key not in catalog_keys:
            # ainda baixa APKs novos publicados no manifesto
            pass
        local = dest_root / remote.relative_path
        try:
            if dest_root.resolve() not in local.resolve().parents:
                continue  # nunca planeja gravar fora da pasta de APKs
        except OSError:
            continue
        digest = _local_sha(local)
        if digest is None:
            status = "missing"
        elif not remote.sha256:
            status = "current"
        elif digest.lower() == remote.sha256.lower():
            status = "current"
        else:
            status = "outdated"
        items.append(ApkSyncItem(remote=remote, local=local, status=status))
    return items


def sync_apks(
    *,
    root: Path | None = None,
    manifest: ApksManifest | None = None,
    progress: ProgressCb | None = None,
    on_item: ItemCb | None = None,
    only_missing: bool = False,
) -> ApkSyncResult:
    """Baixa APKs ausentes/desatualizados para a pasta local."""
    dest_root = root or ensure_apks_tree()
    remote_man = manifest or fetch_apks_manifest()
    plan = plan_apk_sync(remote_man, root=dest_root)
    wanted = [i for i in plan if i.status != "current"]
    if only_missing:
        wanted = [i for i in wanted if i.status == "missing"]

    downloaded = 0
    skipped = sum(1 for i in plan if i.status == "current")
    failed: list[str] = []

    for item in wanted:
        label = f"{item.remote.category}/{item.remote.filename}"
        if not _SHA256_RE.fullmatch((item.remote.sha256 or "").lower()):
            failed.append(f"{label}: sem checksum válido no catálogo — não instalado por segurança")
            continue
        if on_item:
            on_item(f"Baixando {item.remote.label} ({label})…")
        item.local.parent.mkdir(parents=True, exist_ok=True)
        last_error: Exception | None = None
        # download_asset valida o sha256 no .part ANTES de trocar o APK existente;
        # em qualquer falha o arquivo bom que já estava no disco é preservado.
        for mirror in releases_mirror_urls(item.remote.url) or [item.remote.url]:
            asset = ReleaseAsset(
                name=item.remote.filename,
                url=mirror,
                sha256=item.remote.sha256,
                size=item.remote.size,
            )
            try:
                download_asset(
                    asset,
                    item.local,
                    progress=progress,
                    timeout_s=300.0,
                    expected_sha256=item.remote.sha256,
                    max_bytes=MAX_APK_BYTES,
                    deadline_s=1800.0,
                )
                downloaded += 1
                last_error = None
                break
            except Exception as e:
                last_error = e
        if last_error is not None:
            relative = _relative_from_url(item.remote.url)
            if not relative and item.remote.filename:
                relative = f"apk_assets/{item.remote.category}-{item.remote.filename}"
            if relative:
                part = item.local.with_suffix(item.local.suffix + ".part")
                try:
                    data = _github_api_file(relative, timeout_s=300.0)
                    part.write_bytes(data)
                    verify_sha256(part, item.remote.sha256)
                    part.replace(item.local)
                    downloaded += 1
                    last_error = None
                except Exception as e:
                    last_error = e
                finally:
                    try:
                        part.unlink(missing_ok=True)
                    except OSError:
                        pass
        if last_error is not None:
            failed.append(f"{label}: {last_error}")

    return ApkSyncResult(downloaded=downloaded, skipped=skipped, failed=failed)


def catalog_entry_for(remote: RemoteApk) -> ApkEntry | None:
    for e in APK_CATALOG:
        if e.category == remote.category and e.filename == remote.filename:
            return e
    return None
