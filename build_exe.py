# Gera dist/Aibox/Aibox.exe + Aibox-Setup.exe (instala em C:\Aibox com APKs).
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "Aibox.spec"
SETUP_SPEC = ROOT / "installer" / "Aibox-Setup.spec"
DIST_APP = ROOT / "dist" / "Aibox"
DIST_RELEASE = ROOT / "dist" / "release"
PAYLOAD_DIR = ROOT / "build" / "installer_payload"
PAYLOAD_ZIP = PAYLOAD_DIR / "aibox_payload.zip"


PLATFORM_TOOLS_URL = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
_ADB_FILES = (
    "adb.exe",
    "AdbWinApi.dll",
    "AdbWinUsbApi.dll",
    "libwinpthread-1.dll",
)


def _ensure_platform_tools() -> Path:
    """Garante adb.exe + DLLs USB em aibox/platform-tools (necessário no .exe)."""
    dest = ROOT / "aibox" / "platform-tools"
    dest.mkdir(parents=True, exist_ok=True)
    adb = dest / "adb.exe"
    api = dest / "AdbWinApi.dll"
    usb = dest / "AdbWinUsbApi.dll"
    if adb.is_file() and api.is_file() and usb.is_file():
        print(f"ADB empacotado: {adb}")
        return dest

    import tempfile
    import urllib.request

    print("Baixando Android platform-tools (ADB + DLLs USB)…")
    print(f"  {PLATFORM_TOOLS_URL}")
    with tempfile.TemporaryDirectory(prefix="aibox_pt_") as tmp:
        zpath = Path(tmp) / "platform-tools.zip"
        urllib.request.urlretrieve(PLATFORM_TOOLS_URL, zpath)
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(tmp)
        src = Path(tmp) / "platform-tools"
        if not (src / "adb.exe").is_file():
            found = list(Path(tmp).rglob("adb.exe"))
            if not found:
                raise SystemExit("Falha ao extrair adb.exe do platform-tools.")
            src = found[0].parent
        copied = []
        for name in _ADB_FILES:
            f = src / name
            if f.is_file():
                shutil.copy2(f, dest / name)
                copied.append(name)
        for dll in src.glob("*.dll"):
            shutil.copy2(dll, dest / dll.name)
            if dll.name not in copied:
                copied.append(dll.name)
        print("  arquivos:", ", ".join(copied))

    if not adb.is_file() or not usb.is_file():
        raise SystemExit(
            f"platform-tools incompleto em {dest}: precisa de adb.exe e AdbWinUsbApi.dll."
        )
    print(f"ADB empacotado: {adb}")
    return dest


def _copy_adb_into_dist() -> None:
    """Copia ADB para _internal e ao lado do .exe (fallback de descoberta)."""
    src = ROOT / "aibox" / "platform-tools"
    if not (src / "adb.exe").is_file():
        return
    dests = [
        DIST_APP / "_internal" / "aibox" / "platform-tools",
        DIST_APP / "platform-tools",
    ]
    names = list(dict.fromkeys(list(_ADB_FILES) + [p.name for p in src.glob("*.dll")]))
    for dest in dests:
        dest.mkdir(parents=True, exist_ok=True)
        for name in names:
            f = src / name
            if f.is_file():
                shutil.copy2(f, dest / name)
    bundled = DIST_APP / "_internal" / "aibox" / "platform-tools" / "adb.exe"
    if bundled.is_file():
        print(f"ADB no dist: {bundled}")
    else:
        print("AVISO: adb.exe não ficou em dist/_internal/aibox/platform-tools", file=sys.stderr)


def _png_to_ico(png: Path, ico: Path) -> None:
    """Gera .ico (PNG embutido, Windows Vista+) para o instalador e o .exe."""
    import struct

    data = png.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Não é PNG: {png}")
    w, h = struct.unpack(">II", data[16:24])
    bw = 0 if w >= 256 else int(w)
    bh = 0 if h >= 256 else int(h)
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", bw, bh, 0, 0, 1, 32, len(data), 6 + 16)
    ico.write_bytes(header + entry + data)


def _ensure_ico() -> Path | None:
    png = ROOT / "aibox" / "Aibox.png"
    ico = ROOT / "aibox" / "Aibox.ico"
    if not png.is_file():
        return ico if ico.is_file() else None
    try:
        if not ico.is_file() or ico.stat().st_mtime < png.stat().st_mtime:
            _png_to_ico(png, ico)
            print(f"Ícone: {ico}")
        return ico if ico.is_file() else None
    except Exception as e:
        print(f"AVISO: não foi possível gerar Aibox.ico ({e})", file=sys.stderr)
        return ico if ico.is_file() else None


def _app_version() -> str:
    ns: dict = {}
    theme = (ROOT / "aibox" / "theme.py").read_text(encoding="utf-8")
    for line in theme.splitlines():
        if line.startswith("APP_VERSION"):
            exec(line, ns, ns)
            break
    return str(ns.get("APP_VERSION", "0.0.0"))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _load_notes(version: str) -> dict:
    notes_path = ROOT / "release_notes" / f"{version}.json"
    if notes_path.exists():
        data = json.loads(notes_path.read_text(encoding="utf-8"))
        return data.get("notes") or {"added": [], "fixed": [], "removed": []}
    return {"added": [], "fixed": [], "removed": []}


def _write_empty_apks_tree() -> None:
    """Cria C:\\Aibox\\Apks vazia no dist — os APKs vêm do repositório de releases."""
    from aibox.apks_catalog import APK_CATEGORIES

    dst_root = DIST_APP / "Apks"
    if dst_root.exists():
        shutil.rmtree(dst_root, ignore_errors=True)
    dst_root.mkdir(parents=True, exist_ok=True)
    for cat in APK_CATEGORIES:
        (dst_root / cat).mkdir(parents=True, exist_ok=True)
    (dst_root / "LEIA-ME.txt").write_text(
        "Os APKs não vêm no instalador.\n"
        "Abra o Aibox e clique em «Baixar APKs» para obter Totem, Painel e Outros.\n",
        encoding="utf-8",
    )
    print("Pasta Apks vazia no dist (download posterior).")


def _zip_onedir(version: str) -> Path:
    DIST_RELEASE.mkdir(parents=True, exist_ok=True)
    zip_path = DIST_RELEASE / f"Aibox-windows-x64-{version}.zip"
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in DIST_APP.rglob("*"):
            if path.is_file():
                arc = Path("Aibox") / path.relative_to(DIST_APP)
                zf.write(path, arcname=str(arc))
    return zip_path


def _build_payload_zip() -> Path:
    """ZIP embutido no Setup.exe (pasta Aibox/)."""
    PAYLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if PAYLOAD_ZIP.exists():
        PAYLOAD_ZIP.unlink()
    with zipfile.ZipFile(PAYLOAD_ZIP, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in DIST_APP.rglob("*"):
            if path.is_file():
                arc = Path("Aibox") / path.relative_to(DIST_APP)
                zf.write(path, arcname=str(arc).replace("\\", "/"))
    print(f"Payload do instalador: {PAYLOAD_ZIP} ({PAYLOAD_ZIP.stat().st_size // (1024*1024)} MB)")
    return PAYLOAD_ZIP


def _write_latest_json(version: str, zip_path: Path, *, download_url: str | None) -> Path:
    digest = _sha256(zip_path)
    size = zip_path.stat().st_size
    url = download_url or (
        f"https://github.com/ChavesSD/ReleasesAibox/releases/download/v{version}/{zip_path.name}"
    )
    notes_file = ROOT / "release_notes" / f"{version}.json"
    published = ""
    min_version = "1.0.0"
    if notes_file.exists():
        meta = json.loads(notes_file.read_text(encoding="utf-8"))
        published = str(meta.get("published_at") or "")
        min_version = str(meta.get("min_version") or min_version)

    payload = {
        "version": version,
        "min_version": min_version,
        "channel": "stable",
        "published_at": published,
        "notes": _load_notes(version),
        "asset": {
            "name": zip_path.name,
            "url": url,
            "sha256": digest,
            "size": size,
        },
        "installer": {
            "name": "Aibox-Setup.exe",
            "url": f"https://github.com/ChavesSD/ReleasesAibox/releases/download/v{version}/Aibox-Setup.exe",
            "install_dir": r"C:\Aibox",
            "includes_apks": False,
        },
        "apks_manifest": "https://raw.githubusercontent.com/ChavesSD/ReleasesAibox/main/apks.json",
    }
    latest = DIST_RELEASE / "latest.json"
    latest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return latest


def _run_pyinstaller(spec: Path) -> int:
    # Re-libera locks imediatamente antes do COLLECT (adb pode ter voltado)
    if spec.resolve() == SPEC.resolve():
        _kill_locking_processes()
        if DIST_APP.exists():
            _unlock_binaries(DIST_APP)
            _safe_rmtree(DIST_APP)
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(spec),
    ]
    print(">>", " ".join(cmd))
    return subprocess.call(cmd, cwd=str(ROOT))


def _kill_locking_processes() -> None:
    """Encerra Aibox/adb que travam dist\\Aibox no Windows (PermissionError)."""
    import time

    # adb kill-server em todas as cópias conhecidas (projeto, dist, C:\\Aibox)
    adb_candidates = [
        ROOT / "aibox" / "platform-tools" / "adb.exe",
        DIST_APP / "_internal" / "aibox" / "platform-tools" / "adb.exe",
        DIST_APP / "aibox" / "platform-tools" / "adb.exe",
        Path(r"C:\Aibox") / "_internal" / "aibox" / "platform-tools" / "adb.exe",
        Path(r"C:\Aibox") / "aibox" / "platform-tools" / "adb.exe",
        Path(r"C:\Aibox") / "platform-tools" / "adb.exe",
    ]
    which = shutil.which("adb")
    if which:
        adb_candidates.append(Path(which))
    for p in adb_candidates:
        if p.is_file():
            subprocess.run([str(p), "kill-server"], capture_output=True, timeout=8)
    # taskkill por nome (repetido — adb às vezes respawna)
    for _ in range(3):
        for name in ("Aibox.exe", "adb.exe", "Aibox-Setup.exe"):
            subprocess.run(
                ["taskkill", "/F", "/IM", name, "/T"],
                capture_output=True,
                timeout=10,
            )
        time.sleep(0.35)


def _unlock_binaries(path: Path) -> None:
    """Renomeia binários travados para o rmtree/COLLECT conseguir apagar a pasta."""
    import time

    for pattern in ("adb.exe", "Aibox.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll"):
        for locked in path.rglob(pattern):
            if not locked.is_file():
                continue
            try:
                locked.chmod(0o666)
            except OSError:
                pass
            trash = locked.with_name(locked.name + f".old_{os.getpid()}")
            for _ in range(3):
                try:
                    if trash.exists():
                        trash.unlink(missing_ok=True)
                    locked.rename(trash)
                    trash.unlink(missing_ok=True)
                    break
                except OSError:
                    _kill_locking_processes()
                    time.sleep(0.5)


def _safe_rmtree(path: Path) -> None:
    if not path.exists():
        return
    import time

    for _ in range(5):
        try:
            shutil.rmtree(path)
            return
        except PermissionError:
            _kill_locking_processes()
            _unlock_binaries(path)
            time.sleep(0.7)
    # Evacua a pasta (rename) para o PyInstaller criar dist\\Aibox limpo
    trash = path.with_name(f"{path.name}_old_{os.getpid()}")
    if trash.exists():
        shutil.rmtree(trash, ignore_errors=True)
        _unlock_binaries(trash)
    try:
        path.rename(trash)
        _unlock_binaries(trash)
        shutil.rmtree(trash, ignore_errors=True)
    except OSError as e:
        raise SystemExit(
            f"Não foi possível limpar {path}.\n"
            f"Feche o Aibox e qualquer janela/terminal usando adb.exe, depois rode de novo.\n"
            f"Detalhe: {e}"
        ) from e


def _prepare_build_dirs() -> None:
    print("Liberando arquivos bloqueados (adb/Aibox)…")
    _kill_locking_processes()
    _safe_rmtree(DIST_APP)
    # pasta onefile residual do setup
    _safe_rmtree(ROOT / "dist" / "Aibox-Setup")
    # limpa restos de evacuate anterior
    for stale in (ROOT / "dist").glob("Aibox_old_*"):
        _unlock_binaries(stale)
        shutil.rmtree(stale, ignore_errors=True)


def _try_inno_setup(version: str) -> Path | None:
    """Compila um único Aibox-Setup.exe (Inno) se o ISCC estiver instalado."""
    candidates = [
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe"),
    ]
    iscc = next((p for p in candidates if p.is_file()), None)
    if iscc is None:
        which = shutil.which("ISCC") or shutil.which("iscc")
        iscc = Path(which) if which else None
    if iscc is None or not iscc.is_file():
        return None
    iss = ROOT / "installer" / "Aibox.iss"
    DIST_RELEASE.mkdir(parents=True, exist_ok=True)
    cmd = [str(iscc), f"/DMyAppVersion={version}", str(iss)]
    print(">>", " ".join(cmd))
    rc = subprocess.call(cmd, cwd=str(ROOT))
    if rc != 0:
        return None
    out = DIST_RELEASE / "Aibox-Setup.exe"
    return out if out.exists() else None


def _find_signtool() -> Path | None:
    which = shutil.which("signtool")
    if which:
        return Path(which)
    kits = Path(r"C:\Program Files (x86)\Windows Kits\10\bin")
    if kits.is_dir():
        found = sorted(kits.glob(r"*\x64\signtool.exe"), reverse=True)
        if found:
            return found[0]
    return None


def _try_sign(path: Path) -> None:
    """Assina o .exe se AIBOX_PFX ou AIBOX_CERT_THUMBPRINT estiver definido."""
    if not path.is_file():
        return
    pfx = os.environ.get("AIBOX_PFX", "").strip()
    thumb = os.environ.get("AIBOX_CERT_THUMBPRINT", "").strip()
    if not pfx and not thumb:
        return
    signtool = _find_signtool()
    if signtool is None:
        print("AVISO: signtool não encontrado; EXE não assinado.", file=sys.stderr)
        return
    cmd = [
        str(signtool),
        "sign",
        "/fd",
        "SHA256",
        "/td",
        "SHA256",
        "/tr",
        "http://timestamp.digicert.com",
    ]
    if pfx:
        cmd += ["/f", pfx]
        password = os.environ.get("AIBOX_PFX_PASSWORD", "")
        if password:
            cmd += ["/p", password]
    else:
        cmd += ["/sha1", thumb]
    cmd.append(str(path))
    rc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if rc.returncode != 0:
        err = (rc.stderr or rc.stdout or "").strip()
        print(f"AVISO: falha ao assinar {path.name}: {err}", file=sys.stderr)
        return
    print(f"Assinado: {path}")


def _copy_pyinstaller_setup() -> Path | None:
    setup_src = ROOT / "dist" / "Aibox-Setup.exe"
    if not setup_src.exists():
        alt = ROOT / "dist" / "Aibox-Setup" / "Aibox-Setup.exe"
        if alt.exists():
            setup_src = alt
    if not setup_src.exists():
        return None
    DIST_RELEASE.mkdir(parents=True, exist_ok=True)
    setup_dst = DIST_RELEASE / "Aibox-Setup.exe"
    shutil.copy2(setup_src, setup_dst)
    try:
        from installer.windows_trust import unblock_file

        unblock_file(setup_dst)
    except Exception:
        pass
    return setup_dst


def main() -> int:
    if not SPEC.exists():
        print(f"Spec não encontrado: {SPEC}", file=sys.stderr)
        return 1

    try:
        _ensure_platform_tools()
        _ensure_ico()
        _prepare_build_dirs()
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 5

    # 1) App onedir (usado internamente pelo instalador)
    rc = _run_pyinstaller(SPEC)
    if rc != 0:
        return rc

    out = DIST_APP / "Aibox.exe"
    if not out.exists():
        print("Build terminou sem Aibox.exe em dist/Aibox/", file=sys.stderr)
        return 2

    _copy_adb_into_dist()
    _try_sign(out)

    version = _app_version()
    (DIST_APP / "version.txt").write_text(version + "\n", encoding="utf-8")

    # 2) Pasta Apks vazia — download no app, após instalar
    _write_empty_apks_tree()

    # 3) ZIP de atualização (GitHub / updater) — não é o arquivo para o usuário final
    zip_path = _zip_onedir(version)
    latest = _write_latest_json(version, zip_path, download_url=None)

    # 4) Um único Aibox-Setup.exe para distribuir
    DIST_RELEASE.mkdir(parents=True, exist_ok=True)
    setup_dst = _try_inno_setup(version)
    setup_kind = "Inno Setup" if setup_dst else None
    if setup_dst is None:
        print("Inno Setup não encontrado — gerando instalador gráfico (PyInstaller onefile).")
        _build_payload_zip()
        if not SETUP_SPEC.exists():
            print(f"Spec do Setup não encontrado: {SETUP_SPEC}", file=sys.stderr)
            return 4
        rc = _run_pyinstaller(SETUP_SPEC)
        if rc != 0:
            return rc
        setup_dst = _copy_pyinstaller_setup()
        setup_kind = "PyInstaller"
        if setup_dst is None:
            print("AVISO: Aibox-Setup.exe não foi gerado.", file=sys.stderr)

    if setup_dst:
        _try_sign(setup_dst)

    print()
    print(f"OK: {out}")
    print(f"OK: {zip_path}")
    print(f"OK: {latest}")
    if setup_dst and setup_dst.exists():
        print(f"OK ({setup_kind}): {setup_dst}")
        print()
        print("Distribua apenas este arquivo (um único .exe, sem pastas):")
        print(f"  {setup_dst}")
        print("  -> O usuário executa Aibox-Setup.exe e o app é instalado em C:\\Aibox.")
    print(f"Versão: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
