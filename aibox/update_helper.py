"""
Helper de atualização do Aibox.

Invocado como:
  Aibox.exe --update-helper --pid N --install-dir DIR --package ZIP [--restart]

Espera o processo principal sair, substitui a pasta onedir e relança o app.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # OpenProcess + wait would be better; tasklist is available everywhere.
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
            text = (out.stdout or "") + (out.stderr or "")
            return str(pid) in text and "INFO:" not in text.upper()
        except Exception:
            return False
    try:
        os_kill = getattr(__import__("os"), "kill")
        os_kill(pid, 0)
        return True
    except OSError:
        return False


def _wait_pid(pid: int, timeout_s: float = 90.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not _pid_alive(pid):
            # pequena folga para handles liberarem no Windows
            time.sleep(0.8)
            return
        time.sleep(0.4)
    raise RuntimeError(f"Timeout aguardando o processo {pid} encerrar.")


def _extract_onedir(package: Path, install_dir: Path) -> None:
    staging = install_dir.parent / f"{install_dir.name}.new-{int(time.time())}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(package, "r") as zf:
        zf.extractall(staging)

    # ZIP pode conter a pasta "Aibox/" ou arquivos na raiz.
    children = [p for p in staging.iterdir()]
    payload = staging
    if len(children) == 1 and children[0].is_dir() and (children[0] / "Aibox.exe").exists():
        payload = children[0]
    elif not (staging / "Aibox.exe").exists():
        # procura Aibox.exe em subpastas
        found = list(staging.rglob("Aibox.exe"))
        if not found:
            raise RuntimeError("Pacote inválido: Aibox.exe não encontrado no ZIP.")
        payload = found[0].parent

    backup = install_dir.parent / f"{install_dir.name}.bak-{int(time.time())}"
    if install_dir.exists():
        install_dir.rename(backup)

    try:
        shutil.move(str(payload), str(install_dir))
    except Exception:
        # rollback
        if backup.exists() and not install_dir.exists():
            backup.rename(install_dir)
        raise

    # limpa staging
    shutil.rmtree(staging, ignore_errors=True)
    # remove backup antigo se a troca deu certo
    try:
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="AiboxUpdater")
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args(argv)

    install_dir = args.install_dir.resolve()
    package = args.package.resolve()
    if not package.exists():
        print(f"Pacote não encontrado: {package}", file=sys.stderr)
        return 2

    try:
        _wait_pid(args.pid)
        _extract_onedir(package, install_dir)
    except Exception as e:
        print(f"Falha na atualização: {e}", file=sys.stderr)
        return 1

    exe = install_dir / "Aibox.exe"
    if args.restart and exe.exists():
        creationflags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0
        subprocess.Popen(
            [str(exe)],
            cwd=str(install_dir),
            close_fds=True,
            creationflags=creationflags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
