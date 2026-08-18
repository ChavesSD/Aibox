"""
Helper de atualização do Aibox.

Invocado como:
  Aibox.exe --update-helper --pid N --install-dir DIR --package ZIP [--restart]

O EXE do helper ainda está em DIR, então NÃO troca a pasta em uso.
Extrai o ZIP para uma pasta irmã e dispara um .cmd no TEMP que, depois que
este processo sair, troca as pastas e relança o app.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

_CREATE_NO_WINDOW = 0x08000000
_DETACHED = 0x00000008 | 0x00000200 | _CREATE_NO_WINDOW


def _log(package: Path, message: str) -> None:
    path = package.parent / "update.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S") + " " + message + "\n")
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=_CREATE_NO_WINDOW,
            )
            text = (out.stdout or "") + (out.stderr or "")
            return str(pid) in text and "INFO:" not in text.upper()
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _wait_pid(pid: int, timeout_s: float = 90.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not _pid_alive(pid):
            time.sleep(0.8)
            return
        time.sleep(0.4)
    raise RuntimeError(f"Timeout aguardando o processo {pid} encerrar.")


def _stop_locking_helpers() -> None:
    """Libera adb.exe que costuma manter a pasta de instalação travada no Windows."""
    if sys.platform != "win32":
        return
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "adb.exe", "/T"],
            capture_output=True,
            timeout=8,
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception:
        pass


def _payload_from_extract(staging: Path) -> Path:
    children = [p for p in staging.iterdir()]
    if len(children) == 1 and children[0].is_dir() and (children[0] / "Aibox.exe").exists():
        return children[0]
    if (staging / "Aibox.exe").exists():
        return staging
    found = list(staging.rglob("Aibox.exe"))
    if not found:
        raise RuntimeError("Pacote inválido: Aibox.exe não encontrado no ZIP.")
    return found[0].parent


def stage_new_onedir(package: Path, install_dir: Path) -> Path:
    """Extrai o ZIP para uma pasta irmã. Não mexe na instalação em uso."""
    stamp = int(time.time())
    staging = install_dir.parent / f"{install_dir.name}.extract-{stamp}"
    new_dir = install_dir.parent / f"{install_dir.name}.new-{stamp}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    if new_dir.exists():
        shutil.rmtree(new_dir, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(package, "r") as zf:
        zf.extractall(staging)

    payload = _payload_from_extract(staging)
    if payload.resolve() == staging.resolve():
        staging.rename(new_dir)
    else:
        shutil.move(str(payload), str(new_dir))
        shutil.rmtree(staging, ignore_errors=True)

    if not (new_dir / "Aibox.exe").is_file():
        raise RuntimeError("Pacote extraído sem Aibox.exe.")
    return new_dir


def apply_onedir_swap(install_dir: Path, new_dir: Path) -> None:
    """Substitui a pasta de instalação pela extraída (uso em testes / script)."""
    backup = install_dir.parent / f"{install_dir.name}.bak-{int(time.time())}"
    if install_dir.exists():
        install_dir.rename(backup)
    try:
        new_dir.rename(install_dir)
    except Exception:
        if backup.exists() and not install_dir.exists():
            backup.rename(install_dir)
        raise
    try:
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
    except OSError:
        pass


def _extract_onedir(package: Path, install_dir: Path) -> None:
    """Aplica na hora (testes). Em produção o swap é feito pelo .cmd após o helper sair."""
    new_dir = stage_new_onedir(package, install_dir)
    apply_onedir_swap(install_dir, new_dir)


def _vbs_quote(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _write_swap_script(
    *,
    install_dir: Path,
    new_dir: Path,
    helper_pid: int,
    restart: bool,
    script_dir: Path,
) -> Path:
    """Gera um .vbs (wscript, sem console).

    O .cmd antigo fazia `tasklist | find "PID"`: o find.exe abre uma janela
    a cada segundo e, se o helper ainda não saiu, parece que «nunca fecha».
    """
    script_dir.mkdir(parents=True, exist_ok=True)
    script = script_dir / f"aibox-swap-{helper_pid}.vbs"
    install_q = _vbs_quote(str(install_dir))
    new_q = _vbs_quote(str(new_dir))
    backup_q = _vbs_quote(str(install_dir.parent / f"{install_dir.name}.bak-{helper_pid}"))
    exe_q = _vbs_quote(str(install_dir / "Aibox.exe"))
    log_q = _vbs_quote(str(script_dir / "aibox-swap.log"))
    pid = int(helper_pid)
    restart_block = (
        f"sh.Run {exe_q}, 1, False"
        if restart
        else "' no restart"
    )
    script.write_text(
        "\r\n".join(
            [
                "Option Explicit",
                "Dim sh, fso, svc, col, n, i, logPath, ts",
                "Set sh = CreateObject(\"WScript.Shell\")",
                "Set fso = CreateObject(\"Scripting.FileSystemObject\")",
                f"logPath = {log_q}",
                "Sub LogLine(msg)",
                "  On Error Resume Next",
                "  Set ts = fso.OpenTextFile(logPath, 8, True)",
                "  ts.WriteLine Now & \" \" & msg",
                "  ts.Close",
                "  On Error GoTo 0",
                "End Sub",
                "Function PidAlive(pid)",
                "  PidAlive = False",
                "  On Error Resume Next",
                "  Set svc = GetObject(\"winmgmts:\\\\.\\root\\cimv2\")",
                '  Set col = svc.ExecQuery("SELECT ProcessId FROM Win32_Process WHERE ProcessId=" & pid)',
                "  If Err.Number = 0 Then PidAlive = (col.Count > 0)",
                "  On Error GoTo 0",
                "End Function",
                'LogLine "swap-start"',
                "i = 0",
                f"Do While PidAlive({pid})",
                "  i = i + 1",
                "  If i > 225 Then Exit Do",
                "  WScript.Sleep 400",
                "Loop",
                "WScript.Sleep 800",
                'sh.Run "taskkill /F /IM adb.exe /T", 0, True',
                "WScript.Sleep 1500",
                "n = 0",
                "Do",
                "  n = n + 1",
                f"  If fso.FolderExists({install_q}) Then",
                "    On Error Resume Next",
                f"    fso.MoveFolder {install_q}, {backup_q}",
                "    On Error GoTo 0",
                "  End If",
                f"  If Not fso.FolderExists({install_q}) Then Exit Do",
                "  If n >= 25 Then",
                '    LogLine "FAIL-rename"',
                "    WScript.Quit 1",
                "  End If",
                "  WScript.Sleep 1000",
                "Loop",
                "On Error Resume Next",
                f"fso.MoveFolder {new_q}, {install_q}",
                "On Error GoTo 0",
                f"If Not fso.FileExists({exe_q}) Then",
                f"  If fso.FolderExists({backup_q}) Then fso.MoveFolder {backup_q}, {install_q}",
                '  LogLine "FAIL-move"',
                "  WScript.Quit 1",
                "End If",
                restart_block,
                f"If fso.FolderExists({backup_q}) Then",
                "  On Error Resume Next",
                f"  fso.DeleteFolder {backup_q}, True",
                "  On Error GoTo 0",
                "End If",
                'LogLine "swap-ok"',
                "On Error Resume Next",
                "fso.DeleteFile WScript.ScriptFullName, True",
                "WScript.Quit 0",
                "",
            ]
        ),
        encoding="ascii",
        errors="replace",
    )
    return script


def _spawn_swap_script(script: Path) -> None:
    if sys.platform == "win32":
        windir = os.environ.get("SystemRoot") or r"C:\Windows"
        wscript = str(Path(windir) / "System32" / "wscript.exe")
        subprocess.Popen(
            [wscript, "//B", "//Nologo", str(script)],
            cwd=str(script.parent),
            close_fds=True,
            creationflags=_DETACHED,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    subprocess.Popen(
        ["/bin/sh", str(script)],
        cwd=str(script.parent),
        close_fds=True,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


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
        _log(package, f"pacote ausente: {package}")
        print(f"Pacote não encontrado: {package}", file=sys.stderr)
        return 2

    try:
        _log(package, f"aguardando pid {args.pid}")
        _wait_pid(args.pid)
        _stop_locking_helpers()
        _log(package, "extraindo pacote")
        new_dir = stage_new_onedir(package, install_dir)
        script = _write_swap_script(
            install_dir=install_dir,
            new_dir=new_dir,
            helper_pid=os.getpid(),
            restart=bool(args.restart),
            script_dir=package.parent,
        )
        _log(package, f"swap script {script}")
        _spawn_swap_script(script)
    except Exception as e:
        _log(package, f"falha: {e}")
        print(f"Falha na atualização: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
