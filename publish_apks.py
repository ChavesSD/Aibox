"""Gera apks.json e publica os APKs no repositório ReleasesAibox."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from aibox.apks_catalog import APK_CATALOG, APK_CATEGORIES  # noqa: E402

RELEASES_REPO = "ChavesSD/ReleasesAibox"
APKS_TAG_PREFIX = "apks-"
RAW_ASSETS_BASE = f"https://raw.githubusercontent.com/{RELEASES_REPO}/main/apk_assets"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _apks_source() -> Path:
    env = (os.environ.get("AIBOX_APKS_SOURCE") or "").strip()
    if env:
        return Path(env)
    home = Path.home() / "Aibox" / "Apks"
    if home.is_dir():
        return home
    return ROOT / "Apks"


def asset_name(category: str, filename: str) -> str:
    return f"{category}-{filename}"


def build_manifest(src_root: Path, version: str, *, github_release: bool = False) -> dict:
    if github_release:
        base_url = f"https://github.com/{RELEASES_REPO}/releases/download/{APKS_TAG_PREFIX}{version}"
    else:
        base_url = RAW_ASSETS_BASE
    apks: list[dict] = []
    missing: list[str] = []
    for entry in APK_CATALOG:
        src = src_root / entry.relative_path
        if not src.is_file():
            missing.append(f"{entry.category}/{entry.filename}")
            continue
        name = asset_name(entry.category, entry.filename)
        apks.append(
            {
                "category": entry.category,
                "filename": entry.filename,
                "label": entry.label,
                "sha256": _sha256(src),
                "size": src.stat().st_size,
                "url": f"{base_url}/{name}",
            }
        )
    if missing:
        raise SystemExit("APKs ausentes:\n  - " + "\n  - ".join(missing))
    if not apks:
        raise SystemExit(f"Nenhum APK em {src_root}")
    return {
        "version": version,
        "updated_at": date.today().isoformat(),
        "apks": apks,
    }


def copy_assets(src_root: Path, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for entry in APK_CATALOG:
        src = src_root / entry.relative_path
        out = dest / asset_name(entry.category, entry.filename)
        shutil.copy2(src, out)
        files.append(out)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publica APKs no ReleasesAibox")
    parser.add_argument("--version", default="1.0.0", help="Versão do catálogo de APKs")
    parser.add_argument("--source", default="", help="Pasta Totem/Painel/Outros")
    parser.add_argument("--out", default="", help="Pasta de saída do apks.json")
    parser.add_argument(
        "--github-release",
        action="store_true",
        help="URLs no formato GitHub Releases (requer --upload)",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Cria/atualiza o GitHub Release com gh",
    )
    args = parser.parse_args(argv)

    src = Path(args.source) if args.source else _apks_source()
    if not src.is_dir():
        print(f"Pasta de APKs não encontrada: {src}", file=sys.stderr)
        return 1

    manifest = build_manifest(src, args.version, github_release=args.github_release)
    out_dir = Path(args.out) if args.out else ROOT / "dist" / "release"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "apks.json"
    json_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OK: {json_path} ({len(manifest['apks'])} APKs)")

    assets_dir = out_dir / "apk_assets"
    files = copy_assets(src, assets_dir)
    for f in files:
        print(f"  {f.name} ({f.stat().st_size // 1024} KB)")

    if not args.upload:
        print()
        print("Copie apks.json e apk_assets/ para o repositório ReleasesAibox (branch main).")
        print("Ou publique um GitHub Release com:")
        print(f"  python publish_apks.py --version {args.version} --github-release --upload")
        return 0

    tag = f"{APKS_TAG_PREFIX}{args.version}"
    title = f"APKs {args.version}"
    notes = (
        f"Catálogo de APKs do Aibox ({len(files)} arquivos).\n"
        "O app baixa estes arquivos após a instalação."
    )
    cmd = [
        "gh",
        "release",
        "create",
        tag,
        "--repo",
        RELEASES_REPO,
        "--title",
        title,
        "--notes",
        notes,
        str(json_path),
        *[str(p) for p in files],
    ]
    print(">>", " ".join(cmd[:8]), "...")
    rc = subprocess.call(cmd)
    if rc != 0:
        # release já existe — só envia os assets
        cmd2 = ["gh", "release", "upload", tag, "--repo", RELEASES_REPO, "--clobber", str(json_path)]
        cmd2 += [str(p) for p in files]
        print("Release já existia, enviando assets…")
        rc = subprocess.call(cmd2)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
