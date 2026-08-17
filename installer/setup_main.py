"""Instalador gráfico Aibox → C:\\Aibox (um único .exe, sem console)."""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

from installer.windows_trust import smart_app_control_active, trust_installed_app

INSTALL_DIR = Path(r"C:\Aibox")
APP_EXE_NAME = "Aibox.exe"
PAYLOAD_NAME = "aibox_payload.zip"
CREATE_NO_WINDOW = 0x08000000

try:
    import tkinter as tk
    from tkinter import font as tkfont
except ImportError:  # pragma: no cover
    tk = None  # type: ignore[assignment]
    tkfont = None  # type: ignore[assignment]

BG = "#1a1d26"
SURFACE = "#252934"
PRIMARY = "#3d7eff"
PRIMARY_HOVER = "#5b91ff"
TEXT = "#ffffff"
SUBTLE = "#a0a4b0"


def _resource_path(name: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / name  # type: ignore[attr-defined]
    here = Path(__file__).resolve().parent
    for base in (here, here.parent / "aibox", here.parent / "build" / "installer_payload"):
        p = base / name
        if p.is_file():
            return p
    return here / name


def _hidden_run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    flags = int(kwargs.pop("creationflags", 0)) | CREATE_NO_WINDOW
    return subprocess.run(cmd, creationflags=flags, **kwargs)


def _is_admin() -> bool:
    if os.name != "nt":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _relaunch_as_admin() -> bool:
    """True se o processo elevado foi disparado (este deve encerrar)."""
    if os.name != "nt":
        return False
    exe = sys.executable
    params = subprocess.list2cmdline(sys.argv[1:])
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    return rc > 32


def _message(title: str, text: str, error: bool = False) -> None:
    flags = 0x10 if error else 0x40
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, flags)
    except Exception:
        pass


def _stop_running_app() -> None:
    for name in ("Aibox.exe", "adb.exe"):
        try:
            _hidden_run(
                ["taskkill", "/F", "/IM", name, "/T"],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass
    adb = INSTALL_DIR / "_internal" / "aibox" / "platform-tools" / "adb.exe"
    if adb.is_file():
        try:
            _hidden_run([str(adb), "kill-server"], capture_output=True, timeout=8)
        except Exception:
            pass


def _create_shortcut(target: Path, link_path: Path, workdir: Path) -> None:
    link_path.parent.mkdir(parents=True, exist_ok=True)
    ps = f"""
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut('{str(link_path).replace("'", "''")}')
$sc.TargetPath = '{str(target).replace("'", "''")}'
$sc.WorkingDirectory = '{str(workdir).replace("'", "''")}'
$sc.Description = 'Aibox — Intelite'
$sc.Save()
"""
    _hidden_run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        check=False,
        capture_output=True,
        timeout=20,
    )


def _write_uninstaller(install_dir: Path) -> None:
    bat = install_dir / "Desinstalar-Aibox.bat"
    bat.write_text(
        "\r\n".join(
            [
                "@echo off",
                "echo Removendo Aibox de C:\\Aibox ...",
                "taskkill /F /IM Aibox.exe /T >nul 2>&1",
                "taskkill /F /IM adb.exe /T >nul 2>&1",
                'del /q "%USERPROFILE%\\Desktop\\Aibox.lnk" 2>nul',
                'del /q "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Aibox.lnk" 2>nul',
                "cd /d C:\\",
                "rmdir /s /q C:\\Aibox",
                "echo Concluido.",
                "pause",
                "",
            ]
        ),
        encoding="utf-8",
    )
    try:
        import winreg

        key = winreg.CreateKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Aibox",
        )
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "Aibox")
        winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "Intelite")
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(install_dir))
        winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, str(install_dir / APP_EXE_NAME))
        winreg.SetValueEx(
            key,
            "UninstallString",
            0,
            winreg.REG_SZ,
            f'cmd /c "{bat}"',
        )
        winreg.CloseKey(key)
    except OSError:
        pass


def _payload_path() -> Path:
    return _resource_path(PAYLOAD_NAME)


def perform_install(progress=None) -> Path:
    """Extrai o payload para C:\\Aibox. `progress(frac, status)` é opcional."""

    def report(frac: float, status: str) -> None:
        if progress:
            progress(max(0.0, min(1.0, frac)), status)

    payload = _payload_path()
    if not payload.is_file():
        raise RuntimeError(
            "Pacote de instalação não encontrado.\nRecompile com: python build_exe.py"
        )

    report(0.02, "Encerrando instâncias anteriores…")
    _stop_running_app()

    INSTALL_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="aibox_setup_") as tmp:
        tmp_path = Path(tmp)
        report(0.08, "Extraindo arquivos…")
        with zipfile.ZipFile(payload, "r") as zf:
            infos = zf.infolist()
            total = max(len(infos), 1)
            for i, info in enumerate(infos, start=1):
                zf.extract(info, tmp_path)
                if i == 1 or i == total or i % 25 == 0:
                    report(0.08 + 0.55 * (i / total), f"Extraindo arquivos… ({i}/{total})")

        src = tmp_path / "Aibox"
        if not src.is_dir():
            src = tmp_path
        if not (src / APP_EXE_NAME).is_file():
            found = list(tmp_path.rglob(APP_EXE_NAME))
            if found:
                src = found[0].parent
            else:
                raise RuntimeError("Aibox.exe não encontrado no pacote.")

        report(0.66, "Preparando pasta de destino…")
        for item in list(INSTALL_DIR.iterdir()):
            if item.name.lower() in ("capturas",):
                continue
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                try:
                    item.unlink()
                except OSError:
                    pass

        files = [p for p in src.rglob("*") if p.is_file()]
        total_files = max(len(files), 1)
        report(0.70, "Copiando para C:\\Aibox…")
        for i, item in enumerate(files, start=1):
            rel = item.relative_to(src)
            dest = INSTALL_DIR / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)
            if i == 1 or i == total_files or i % 20 == 0:
                report(0.70 + 0.20 * (i / total_files), f"Copiando arquivos… ({i}/{total_files})")

    exe = INSTALL_DIR / APP_EXE_NAME
    if not exe.is_file():
        raise RuntimeError("Falha ao copiar Aibox.exe.")

    report(0.92, "Criando atalhos…")
    desktop = Path(os.environ.get("USERPROFILE", "")) / "Desktop" / "Aibox.lnk"
    start_menu = (
        Path(os.environ.get("APPDATA", ""))
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Aibox.lnk"
    )
    _create_shortcut(exe, desktop, INSTALL_DIR)
    _create_shortcut(exe, start_menu, INSTALL_DIR)
    _write_uninstaller(INSTALL_DIR)
    report(0.97, "Permitindo o Aibox no Windows…")
    trust_installed_app(INSTALL_DIR)
    report(1.0, "Instalação concluída.")
    return exe


def _set_dark_titlebar(root) -> None:
    try:
        root.update_idletasks()
        hwnd = root.winfo_id()
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


def run_gui() -> int:
    if tk is None or tkfont is None:
        raise RuntimeError("tkinter indisponível")

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()
    root.title("Aibox — Instalação")
    root.configure(bg=BG)
    root.resizable(False, False)
    w, h = 520, 420
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2)}")
    root.minsize(480, 380)
    _set_dark_titlebar(root)

    icon_png = _resource_path("Aibox.png")
    photo = None
    if icon_png.is_file():
        try:
            photo = tk.PhotoImage(file=str(icon_png))
            root.iconphoto(True, photo)
        except Exception:
            photo = None

    title_font = tkfont.Font(family="Segoe UI", size=16, weight="bold")
    body_font = tkfont.Font(family="Segoe UI", size=10)
    small_font = tkfont.Font(family="Segoe UI", size=9)

    pad = tk.Frame(root, bg=BG)
    pad.pack(fill="both", expand=True, padx=28, pady=22)

    btns = tk.Frame(pad, bg=BG)
    btns.pack(side="bottom", fill="x", pady=(12, 0))

    header = tk.Frame(pad, bg=BG)
    header.pack(fill="x")
    if photo is not None:
        try:
            thumb = photo.subsample(max(photo.width() // 40, 1), max(photo.height() // 40, 1))
            tk.Label(header, image=thumb, bg=BG).pack(side="left", padx=(0, 12))
            header._thumb = thumb  # noqa: SLF001 — evita GC
        except Exception:
            pass
    titles = tk.Frame(header, bg=BG)
    titles.pack(side="left", fill="x", expand=True)
    tk.Label(titles, text="Aibox", fg=TEXT, bg=BG, font=title_font, anchor="w").pack(fill="x")
    tk.Label(titles, text="Intelite", fg=SUBTLE, bg=BG, font=small_font, anchor="w").pack(fill="x")

    tk.Label(
        pad,
        text=(
            "Instala o Aibox em C:\\Aibox, com atalhos na Área de Trabalho "
            "e no Menu Iniciar. Os APKs são baixados depois, pelo próprio app."
        ),
        fg=SUBTLE,
        bg=BG,
        font=body_font,
        justify="left",
        anchor="w",
        wraplength=440,
    ).pack(fill="x", pady=(18, 8))

    dest = tk.Frame(pad, bg=SURFACE)
    dest.pack(fill="x", pady=(4, 16))
    tk.Label(dest, text="  Destino", fg=SUBTLE, bg=SURFACE, font=small_font, anchor="w").pack(fill="x", pady=(8, 0))
    tk.Label(dest, text="  C:\\Aibox", fg=TEXT, bg=SURFACE, font=body_font, anchor="w").pack(fill="x", pady=(0, 10))

    status = tk.StringVar(value="Pronto para instalar.")
    tk.Label(pad, textvariable=status, fg=SUBTLE, bg=BG, font=small_font, anchor="w").pack(fill="x")

    bar_bg = tk.Frame(pad, bg=SURFACE, height=10)
    bar_bg.pack(fill="x", pady=(8, 4))
    bar_bg.pack_propagate(False)
    bar_fill = tk.Frame(bar_bg, bg=PRIMARY, width=0, height=10)
    bar_fill.place(x=0, y=0, relheight=1)

    result: dict = {"exe": None, "error": None, "busy": False}

    def set_progress(frac: float, text: str) -> None:
        def _apply() -> None:
            status.set(text)
            bar_fill.configure(width=max(2, int(bar_bg.winfo_width() * frac)))

        root.after(0, _apply)

    def finish_ok() -> None:
        result["busy"] = False
        status.set("Instalação concluída.")
        bar_fill.configure(width=max(2, bar_bg.winfo_width()))
        install_btn.pack_forget()
        open_btn.pack(side="right")
        close_btn.configure(text="Fechar")
        if smart_app_control_active():
            _message(
                "Aibox — Windows",
                "O Controle inteligente de aplicativos deste PC pode bloquear o Aibox "
                "(o instalador ainda não tem certificado de código).\n\n"
                "Se o app não abrir:\n"
                "1. Segurança do Windows → Controle de aplicativos e navegador\n"
                "2. Controle inteligente de aplicativos → Desativado\n"
                "3. Ou, no aviso do Windows, clique em Mais informações → Executar mesmo assim.",
            )

    def finish_err(msg: str) -> None:
        result["busy"] = False
        status.set(msg)
        install_btn.configure(state="normal", bg=PRIMARY)
        _message("Aibox — Instalação", msg, error=True)

    def worker() -> None:
        try:
            exe = perform_install(progress=set_progress)
            result["exe"] = exe
            root.after(0, finish_ok)
        except Exception as e:
            result["error"] = str(e)
            root.after(0, lambda: finish_err(str(e)))

    def start_install() -> None:
        if result["busy"]:
            return
        result["busy"] = True
        install_btn.configure(state="disabled", bg="#2f66d6")
        threading.Thread(target=worker, daemon=True).start()

    def open_app() -> None:
        exe = result.get("exe")
        if exe and Path(exe).is_file():
            try:
                os.startfile(str(exe))  # type: ignore[attr-defined]
            except OSError:
                subprocess.Popen(
                    [str(exe)],
                    cwd=str(INSTALL_DIR),
                    close_fds=True,
                    creationflags=0x00000008 | 0x00000200,
                )
        root.destroy()

    def on_close() -> None:
        if result["busy"]:
            return
        root.destroy()

    def hover_on(_e) -> None:
        if str(install_btn["state"]) == "normal":
            install_btn.configure(bg=PRIMARY_HOVER)

    def hover_off(_e) -> None:
        if str(install_btn["state"]) == "normal":
            install_btn.configure(bg=PRIMARY)

    close_btn = tk.Button(
        btns,
        text="Cancelar",
        command=on_close,
        bg=SURFACE,
        fg=TEXT,
        activebackground="#2c313c",
        activeforeground=TEXT,
        relief="flat",
        font=body_font,
        padx=16,
        pady=8,
        cursor="hand2",
    )
    close_btn.pack(side="left")

    open_btn = tk.Button(
        btns,
        text="Abrir Aibox",
        command=open_app,
        bg=PRIMARY,
        fg=TEXT,
        activebackground=PRIMARY_HOVER,
        activeforeground=TEXT,
        relief="flat",
        font=body_font,
        padx=18,
        pady=8,
        cursor="hand2",
    )

    install_btn = tk.Button(
        btns,
        text="Instalar",
        command=start_install,
        bg=PRIMARY,
        fg=TEXT,
        activebackground=PRIMARY_HOVER,
        activeforeground=TEXT,
        relief="flat",
        font=body_font,
        padx=22,
        pady=8,
        cursor="hand2",
    )
    install_btn.pack(side="right")
    install_btn.bind("<Enter>", hover_on)
    install_btn.bind("<Leave>", hover_off)

    def _fit_window() -> None:
        root.update_idletasks()
        need_w = min(max(w, pad.winfo_reqwidth() + 56), max(480, sw - 40))
        need_h = min(max(h, pad.winfo_reqheight() + 56), max(380, sh - 80))
        root.geometry(
            f"{need_w}x{need_h}+{max(0, (sw - need_w) // 2)}+{max(0, (sh - need_h) // 2)}"
        )

    root.after_idle(_fit_window)
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    return 0 if result.get("exe") or not result.get("error") else 1


def install() -> int:
    if os.name == "nt" and not _is_admin():
        if _relaunch_as_admin():
            return 0
        _message("Aibox — Instalação", "É necessário confirmar o UAC para instalar em C:\\Aibox.", error=True)
        return 1
    try:
        return run_gui()
    except Exception as e:
        try:
            perform_install()
            _message("Aibox — Instalação", f"Instalação concluída em C:\\Aibox.")
            return 0
        except Exception as e2:
            _message("Aibox — Instalação", str(e2) or str(e), error=True)
            return 1


if __name__ == "__main__":
    raise SystemExit(install())
