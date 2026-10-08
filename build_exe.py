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


# ffmpeg "essentials" (Gyan/codexffmpeg, release fixa). Só ffmpeg.exe + licença entram no app:
# a gravação com áudio e a validação de MP4 dependem dele na máquina do técnico.
FFMPEG_VERSION = "8.1"
FFMPEG_URL = (
    "https://github.com/GyanD/codexffmpeg/releases/download/"
    f"{FFMPEG_VERSION}/ffmpeg-{FFMPEG_VERSION}-essentials_build.zip"
)
FFMPEG_SHA256 = "8748283d821613d930b0e7be685aaa9df4ca6f0ad4d0c42fd02622b3623463c6"
PLATFORM_TOOLS_URL = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
_ADB_FILES = (
    "adb.exe",
    "AdbWinApi.dll",
    "AdbWinUsbApi.dll",
    "libwinpthread-1.dll",
)


def _ensure_ffmpeg() -> Path:
    """Garante aibox/ffmpeg/ffmpeg.exe (+ LICENSE/README) com SHA-256 do zip fixado."""
    dest = ROOT / "aibox" / "ffmpeg"
    exe = dest / "ffmpeg.exe"
    marker = dest / "SOURCE.txt"
    expected_marker = f"{FFMPEG_URL}\nsha256={FFMPEG_SHA256}\n"
    if (
        exe.is_file()
        and (dest / "LICENSE.txt").is_file()
        and marker.is_file()
        and marker.read_text(encoding="utf-8") == expected_marker
    ):
        print(f"ffmpeg empacotado: {exe}")
        return dest

    import tempfile
    import urllib.request

    print(f"Baixando ffmpeg {FFMPEG_VERSION} (essentials)…")
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aibox_ff_") as tmp:
        zpath = Path(tmp) / "ffmpeg.zip"
        h = hashlib.sha256()
        total = 0
        with urllib.request.urlopen(FFMPEG_URL, timeout=60) as resp, zpath.open("wb") as out:
            while True:
                block = resp.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > 250 * 1024 * 1024:
                    raise SystemExit("ffmpeg maior que o esperado; download abortado.")
                h.update(block)
                out.write(block)
        if h.hexdigest().lower() != FFMPEG_SHA256:
            raise SystemExit(f"SHA-256 do ffmpeg não confere ({h.hexdigest()}).")
        wanted = {"/bin/ffmpeg.exe": "ffmpeg.exe", "/LICENSE": "LICENSE.txt", "/README.txt": "README.txt"}
        found: set[str] = set()
        with zipfile.ZipFile(zpath) as zf:
            for info in zf.infolist():
                name = "/" + info.filename.replace("\\", "/")
                for suffix, target in wanted.items():
                    if name.endswith(suffix) and name.count("/") == suffix.count("/") + 1:
                        (dest / target).write_bytes(zf.read(info))
                        found.add(target)
        missing = set(wanted.values()) - found
        if missing:
            raise SystemExit("Faltam arquivos no zip do ffmpeg: " + ", ".join(sorted(missing)))
    cp = subprocess.run([str(exe), "-version"], capture_output=True, text=True, timeout=20)
    if cp.returncode != 0:
        raise SystemExit("ffmpeg.exe baixado não executa.")
    marker.write_text(expected_marker, encoding="utf-8")
    print(f"ffmpeg pronto: {exe}")
    return dest


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
        # Com timeout e limite de tamanho (urlretrieve não tem nenhum dos dois).
        with urllib.request.urlopen(PLATFORM_TOOLS_URL, timeout=60) as resp, zpath.open("wb") as out:
            total = 0
            while True:
                block = resp.read(256 * 1024)
                if not block:
                    break
                total += len(block)
                if total > 200 * 1024 * 1024:
                    raise SystemExit("platform-tools maior que o esperado; download abortado.")
                out.write(block)
        from aibox.ziputil import safe_extract_zip

        with zipfile.ZipFile(zpath) as zf:
            safe_extract_zip(zf, Path(tmp))
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


def _ensure_ico() -> Path | None:
    """Usa aibox/Aibox.ico. Não regrava a partir do PNG (fica desfalcado no Windows)."""
    ico = ROOT / "aibox" / "Aibox.ico"
    if ico.is_file():
        print(f"Ícone: {ico}")
        return ico
    print(
        "AVISO: aibox/Aibox.ico ausente — o .exe ficará sem ícone nativo.",
        file=sys.stderr,
    )
    return None


def _app_version() -> str:
    import re

    theme = (ROOT / "aibox" / "theme.py").read_text(encoding="utf-8")
    m = re.search(r'^APP_VERSION\s*=\s*"([0-9][0-9A-Za-z.\-+]*)"', theme, re.MULTILINE)
    return m.group(1) if m else "0.0.0"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _validate_release_notes(version: str) -> str | None:
    """Mensagem de erro se as notas da versão faltarem/estiverem inconsistentes (None = ok)."""
    if os.environ.get("AIBOX_ALLOW_NO_NOTES") == "1":
        return None
    import re

    path = ROOT / "release_notes" / f"{version}.json"
    if not path.is_file():
        return (
            f"Faltam as notas da versão: crie release_notes/{version}.json "
            "(ou defina AIBOX_ALLOW_NO_NOTES=1 para um build de teste)."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"release_notes/{version}.json inválido: {exc}"
    if str(data.get("version") or "").lstrip("v") != version:
        return f"release_notes/{version}.json declara versão {data.get('version')!r}, esperado {version!r}."
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(data.get("published_at") or "")):
        return f"release_notes/{version}.json precisa de published_at no formato AAAA-MM-DD."
    notes = data.get("notes")
    if not isinstance(notes, dict) or not any(notes.get(k) for k in ("added", "fixed", "removed")):
        return f"release_notes/{version}.json não lista nenhuma mudança (added/fixed/removed)."
    return None


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
        for name in ("Aibox.exe", "adb.exe", "Aibox-Setup.exe", "scrcpy.exe"):
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


def _try_sign(path: Path) -> bool:
    """Assina o .exe se AIBOX_PFX ou AIBOX_CERT_THUMBPRINT estiver definido."""
    if not path.is_file():
        return False
    pfx = os.environ.get("AIBOX_PFX", "").strip()
    thumb = os.environ.get("AIBOX_CERT_THUMBPRINT", "").strip()
    if not pfx and not thumb:
        return False
    signtool = _find_signtool()
    if signtool is None:
        print("AVISO: signtool não encontrado; EXE não assinado.", file=sys.stderr)
        return False
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
            print(
                "AVISO: a senha do PFX vai na linha de comando (visível a outros processos). "
                "Prefira AIBOX_CERT_THUMBPRINT com o certificado instalado no Windows.",
                file=sys.stderr,
            )
            cmd += ["/p", password]
    else:
        cmd += ["/sha1", thumb]
    cmd.append(str(path))
    rc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if rc.returncode != 0:
        err = (rc.stderr or rc.stdout or "").strip()
        print(f"AVISO: falha ao assinar {path.name}: {err}", file=sys.stderr)
        return False
    print(f"Assinado: {path}")
    return True


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

    problem = _validate_release_notes(_app_version())
    if problem:
        print(problem, file=sys.stderr)
        return 6

    try:
        _ensure_platform_tools()
        from aibox.device_audio import ensure_scrcpy

        print(f"scrcpy: {ensure_scrcpy()}")
        _ensure_ffmpeg()
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
    signed = _try_sign(out)
    if os.environ.get("AIBOX_REQUIRE_SIGN") == "1" and not signed:
        print("AIBOX_REQUIRE_SIGN=1, mas Aibox.exe não foi assinado — build abortado.", file=sys.stderr)
        return 7

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
        if not _try_sign(setup_dst) and os.environ.get("AIBOX_REQUIRE_SIGN") == "1":
            print("AIBOX_REQUIRE_SIGN=1, mas o instalador não foi assinado — build abortado.", file=sys.stderr)
            return 7

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
