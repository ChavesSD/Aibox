"""Download com espelhos do repositório ReleasesAibox (raw.githubusercontent costuma dar 429)."""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request

from .theme import APP_VERSION

GITHUB_REPO = "ChavesSD/ReleasesAibox"
GITHUB_API_CONTENTS = f"https://api.github.com/repos/{GITHUB_REPO}/contents/"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}"

RELEASES_PREFIXES = (
    f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/",
    f"https://media.githubusercontent.com/media/{GITHUB_REPO}/main/",
    f"https://github.com/{GITHUB_REPO}/raw/refs/heads/main/",
    f"https://cdn.jsdelivr.net/gh/{GITHUB_REPO}@main/",
    f"https://github.com/{GITHUB_REPO}/raw/main/",
)

_RELEASE_DOWNLOAD_RE = re.compile(
    rf"^https://github.com/{re.escape(GITHUB_REPO)}/releases/download/"
    r"(?P<tag>[^/]+)/(?P<name>[^/?#]+)"
)


class RemoteFetchError(Exception):
    def __init__(
        self,
        message: str,
        *,
        http_codes: list[int] | None = None,
        last_reason: object | None = None,
        last_http: urllib.error.HTTPError | None = None,
    ) -> None:
        super().__init__(message)
        self.http_codes = list(http_codes or [])
        self.last_reason = last_reason
        self.last_http = last_http

    @property
    def only_missing(self) -> bool:
        return (
            bool(self.http_codes)
            and all(c in (404, 410) for c in self.http_codes)
            and self.last_reason is None
        )

    @property
    def rate_limited(self) -> bool:
        return 429 in self.http_codes


def ua_headers(accept: str) -> dict[str, str]:
    return {"User-Agent": f"Aibox/{APP_VERSION}", "Accept": accept}


def relative_from_url(url: str) -> str:
    target = (url or "").strip()
    for prefix in RELEASES_PREFIXES:
        if target.startswith(prefix):
            return target[len(prefix) :]
    if target.endswith("apks.json"):
        return "apks.json"
    if target.endswith("latest.json"):
        return "latest.json"
    marker = "/apk_assets/"
    if marker in target:
        return "apk_assets/" + target.split(marker, 1)[1]
    return ""


def releases_mirror_urls(url: str) -> list[str]:
    """URLs alternativas do mesmo arquivo no ReleasesAibox."""
    target = (url or "").strip()
    if not target:
        return []
    ordered: list[str] = []

    def _add(item: str) -> None:
        if item and item not in ordered:
            ordered.append(item)

    _add(target)
    relative = relative_from_url(target)
    if relative:
        for prefix in RELEASES_PREFIXES:
            _add(prefix + relative)
    return ordered


def rewrite_url_to_working_host(url: str, working_url: str) -> str:
    working_prefix = ""
    for prefix in RELEASES_PREFIXES:
        if working_url.startswith(prefix):
            working_prefix = prefix
            break
    if not working_prefix:
        return url
    for prefix in RELEASES_PREFIXES:
        if url.startswith(prefix):
            return working_prefix + url[len(prefix) :]
    return url


def github_api_file(relative: str, *, timeout_s: float) -> bytes:
    """Baixa um arquivo via API do GitHub (funciona quando raw.githubusercontent.com dá 429)."""
    url = GITHUB_API_CONTENTS + relative.lstrip("/")
    req_meta = urllib.request.Request(
        url, headers=ua_headers("application/vnd.github+json"), method="GET"
    )
    with urllib.request.urlopen(req_meta, timeout=timeout_s) as resp:
        meta = json.loads(resp.read().decode("utf-8"))
    if not isinstance(meta, dict):
        raise RemoteFetchError(f"Resposta inválida da API do GitHub para {relative}.")
    encoding = str(meta.get("encoding") or "")
    content = meta.get("content")
    if encoding == "base64" and isinstance(content, str) and content.strip():
        return base64.b64decode("".join(content.split()))
    sha = str(meta.get("sha") or "")
    if sha:
        blob_url = f"{GITHUB_API}/git/blobs/{sha}"
        req_blob = urllib.request.Request(
            blob_url, headers=ua_headers("application/vnd.github.raw"), method="GET"
        )
        try:
            with urllib.request.urlopen(req_blob, timeout=timeout_s) as resp:
                data = resp.read()
                if data:
                    return data
        except Exception:
            pass
        req_blob_json = urllib.request.Request(
            blob_url, headers=ua_headers("application/vnd.github+json"), method="GET"
        )
        try:
            with urllib.request.urlopen(req_blob_json, timeout=timeout_s) as resp:
                blob = json.loads(resp.read().decode("utf-8"))
            if isinstance(blob, dict) and blob.get("encoding") == "base64":
                raw = str(blob.get("content") or "")
                if raw.strip():
                    return base64.b64decode("".join(raw.split()))
        except Exception:
            pass
    download = str(meta.get("download_url") or "").strip()
    if download:
        req_dl = urllib.request.Request(
            download, headers=ua_headers("*/*"), method="GET"
        )
        with urllib.request.urlopen(req_dl, timeout=timeout_s) as resp:
            return resp.read()
    raise RemoteFetchError(f"Não foi possível baixar {relative} via API do GitHub.")


def github_release_asset_url(url: str, *, timeout_s: float) -> str | None:
    """Resolve o browser_download_url de um asset publicado em GitHub Releases."""
    match = _RELEASE_DOWNLOAD_RE.match((url or "").strip())
    if match is None:
        return None
    tag = match.group("tag")
    name = match.group("name")
    api = f"{GITHUB_API}/releases/tags/{tag}"
    req = urllib.request.Request(
        api, headers=ua_headers("application/vnd.github+json"), method="GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    assets = payload.get("assets") if isinstance(payload, dict) else None
    if not isinstance(assets, list):
        return None
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        if str(asset.get("name") or "") != name:
            continue
        download = str(asset.get("browser_download_url") or "").strip()
        if download:
            return download
    return None


def http_get(url: str, *, timeout_s: float, accept: str) -> tuple[bytes, str]:
    http_codes: list[int] = []
    last_http: urllib.error.HTTPError | None = None
    last_reason: object | None = None
    for candidate in releases_mirror_urls(url):
        req = urllib.request.Request(
            candidate,
            headers=ua_headers(accept),
            method="GET",
        )
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                    return resp.read(), candidate
            except urllib.error.HTTPError as e:
                last_http = e
                http_codes.append(e.code)
                if e.code == 429 and attempt == 0:
                    time.sleep(1.5)
                    continue
                if e.code not in (403, 404, 410, 429, 502, 503):
                    raise RemoteFetchError(
                        f"HTTP {e.code}",
                        http_codes=http_codes,
                        last_http=e,
                    ) from e
                break
            except urllib.error.URLError as e:
                last_reason = e.reason
                break
            except TimeoutError:
                last_reason = "timeout"
                break

    relative = relative_from_url(url)
    if relative:
        try:
            return github_api_file(relative, timeout_s=timeout_s), "github-api:" + relative
        except urllib.error.HTTPError as e:
            last_http = e
            http_codes.append(e.code)
        except RemoteFetchError as e:
            http_codes.extend(e.http_codes)
            last_http = last_http or e.last_http
            last_reason = last_reason or e.last_reason or e
        except Exception as e:
            last_reason = last_reason or e

    raise RemoteFetchError(
        str(last_reason or "falha de rede"),
        http_codes=http_codes,
        last_reason=last_reason,
        last_http=last_http,
    )
