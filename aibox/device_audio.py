"""Captura o áudio reproduzido no Mini PC (ADB), não o microfone do Windows."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .github_fetch import safe_urlopen
from .paths import executable_dir, is_frozen, package_dir, resource_path
from .procutil import popen_hidden, run_hidden
from .ziputil import safe_extract_zip

SCRCPY_VERSION = "5.0"
SCRCPY_ZIP_URL = (
    f"https://github.com/Genymobile/scrcpy/releases/download/v{SCRCPY_VERSION}/"
    f"scrcpy-win64-v{SCRCPY_VERSION}.zip"
)
SCRCPY_ZIP_SHA256 = "44c10d9e82f20ea67227d14d37bf9fbe3603117c5736df3f514544a02ba20a73"
_SKIP_SCRCPY_NAMES = {"adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll"}

SCRCPY_MAX_ZIP_BYTES = 80 * 1024 * 1024
SCRCPY_DOWNLOAD_TIMEOUT_S = 30.0
SCRCPY_DOWNLOAD_DEADLINE_S = 300.0

_ALSA_PCM_RE = re.compile(
    r"^(\d+)-(\d+):\s*(.+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AlsaCapture:
    card: int
    device: int
    name: str
    score: int


def parse_sdk(text: str) -> int | None:
    raw = (text or "").strip().splitlines()
    if not raw:
        return None
    digits = re.sub(r"[^0-9]", "", raw[0])
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def scrcpy_audio_args_for_sdk(sdk: int) -> tuple[list[str], str]:
    """Flags do scrcpy e aviso (pode ser vazio) conforme o Android do Mini PC."""
    if sdk >= 33:
        return (
            ["--audio-source", "playback", "--audio-dup"],
            "",
        )
    if sdk >= 30:
        return (
            ["--audio-source", "output"],
            "Neste Android o som da TV pode ficar mudo enquanto a gravação estiver ativa.",
        )
    return [], "Áudio interno via ADB exige Android 11+."


def parse_alsa_captures(pcm_text: str) -> list[AlsaCapture]:
    """Interpreta /proc/asound/pcm e pontua dispositivos de captura (não microfone)."""
    out: list[AlsaCapture] = []
    for line in (pcm_text or "").splitlines():
        line = line.strip()
        if not line or "capture" not in line.lower():
            continue
        m = _ALSA_PCM_RE.match(line)
        if not m:
            continue
        card = int(m.group(1))
        device = int(m.group(2))
        name = m.group(3).split(":")[0].strip()
        score = _alsa_capture_score(name)
        out.append(AlsaCapture(card=card, device=device, name=name, score=score))
    return out


def pick_alsa_capture(captures: list[AlsaCapture]) -> AlsaCapture | None:
    usable = [c for c in captures if c.score > 0]
    if not usable:
        return None
    return max(usable, key=lambda c: (c.score, -c.card, -c.device))


def _alsa_capture_score(name: str) -> int:
    low = name.lower()
    if any(bad in low for bad in ("dmic", "headset", "headphone", "mic", "analog")):
        return -100
    score = 0
    if "loopback" in low or "aloop" in low:
        score += 100
    if "hdmi" in low:
        score += 80
    if "ahub" in low or "audiohub" in low:
        score += 70
    if "mix" in low or "dummy" in low:
        score += 55
    if "i2s" in low or "daudio" in low:
        score += 40
    return score


def send_ctrl_c(pid: int) -> bool:
    """Envia Ctrl+C ao console (oculto) de um processo filho no Windows.

    O scrcpy só finaliza o arquivo (cabeçalho WAV) ao receber CTRL_C_EVENT; um app
    sem console precisa se anexar ao console do filho para entregar o sinal.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        k32.FreeConsole()
        try:
            if not k32.AttachConsole(int(pid)):
                return False
            k32.SetConsoleCtrlHandler(None, True)
            ok = bool(k32.GenerateConsoleCtrlEvent(0, 0))
            time.sleep(0.2)
            k32.FreeConsole()
            k32.SetConsoleCtrlHandler(None, False)
            return ok
        finally:
            k32.AttachConsole(-1)  # volta ao console do pai (modo dev), se existir
    except Exception:
        return False


def _device_sdk(adb_path: str, serial: str) -> int | None:
    try:
        cp = run_hidden(
            [adb_path, "-s", serial, "shell", "getprop", "ro.build.version.sdk"],
            capture_output=True,
            text=True,
            timeout=8,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        return None
    return parse_sdk((cp.stdout or "") + "\n" + (cp.stderr or ""))


def _shell_out(adb_path: str, serial: str, cmd: str, timeout_s: int = 8) -> str:
    try:
        cp = run_hidden(
            [adb_path, "-s", serial, "shell", cmd],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        return ""
    return f"{cp.stdout or ''}{cp.stderr or ''}".strip()


def _scrcpy_search_dirs() -> list[Path]:
    dirs: list[Path] = []
    env = "" if is_frozen() else (os.environ.get("AIBOX_SCRCPY_DIR") or "").strip()
    if env:
        dirs.append(Path(env))
    dirs.append(resource_path("scrcpy"))
    dirs.append(package_dir() / "scrcpy")
    exe = executable_dir()
    dirs.append(exe / "scrcpy")
    dirs.append(exe / "_internal" / "aibox" / "scrcpy")
    local = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    dirs.append(local / "Aibox" / "scrcpy")
    return dirs


def _is_scrcpy_exe(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
    except OSError:
        return False
    try:
        cp = run_hidden([str(path), "--version"], capture_output=True, text=True, timeout=8)
    except Exception:
        return False
    # Exige a assinatura de texto do scrcpy: só "returncode 0" aceitaria qualquer .exe.
    return "scrcpy" in ((cp.stdout or "") + (cp.stderr or "")).lower()


def resolve_scrcpy() -> str | None:
    frozen = is_frozen()
    env = "" if frozen else (os.environ.get("AIBOX_SCRCPY_PATH") or os.environ.get("SCRCPY_PATH") or "").strip()
    if env and _is_scrcpy_exe(Path(env)):
        return str(Path(env))
    for folder in _scrcpy_search_dirs():
        candidate = folder / "scrcpy.exe" if os.name == "nt" else folder / "scrcpy"
        if _is_scrcpy_exe(candidate):
            return str(candidate)
    if frozen:
        return None  # empacotado: só o scrcpy distribuído com o app (sem PATH)
    which = shutil.which("scrcpy.exe") if os.name == "nt" else shutil.which("scrcpy")
    if which and _is_scrcpy_exe(Path(which)):
        return which
    return None


def _install_dir() -> Path:
    if is_frozen():
        local = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return local / "Aibox" / "scrcpy"
    return package_dir() / "scrcpy"


def _download_scrcpy_zip(dest: Path) -> str:
    """Baixa o zip oficial com timeout, limite de tamanho e prazo total; devolve o SHA-256."""
    import urllib.request

    req = urllib.request.Request(SCRCPY_ZIP_URL, headers={"User-Agent": "Aibox"}, method="GET")
    h = hashlib.sha256()
    started = time.monotonic()
    total = 0
    with safe_urlopen(req, SCRCPY_DOWNLOAD_TIMEOUT_S) as resp, dest.open("wb") as out:
        while True:
            if time.monotonic() - started > SCRCPY_DOWNLOAD_DEADLINE_S:
                raise RuntimeError("Download do scrcpy excedeu o tempo máximo.")
            block = resp.read(256 * 1024)
            if not block:
                break
            total += len(block)
            if total > SCRCPY_MAX_ZIP_BYTES:
                raise RuntimeError("Download do scrcpy excedeu o tamanho máximo.")
            h.update(block)
            out.write(block)
    return h.hexdigest()


def ensure_scrcpy() -> str:
    """Garante scrcpy.exe (baixa o zip oficial se ainda não estiver no disco)."""
    found = resolve_scrcpy()
    if found:
        return found
    dest = _install_dir()
    dest.mkdir(parents=True, exist_ok=True)
    existing = dest / ("scrcpy.exe" if os.name == "nt" else "scrcpy")
    if _is_scrcpy_exe(existing):
        return str(existing)

    with tempfile.TemporaryDirectory(prefix="aibox_scrcpy_") as tmp:
        zpath = Path(tmp) / "scrcpy.zip"
        digest = _download_scrcpy_zip(zpath)
        if digest.lower() != SCRCPY_ZIP_SHA256.lower():
            zpath.unlink(missing_ok=True)
            raise RuntimeError(
                f"SHA-256 do scrcpy não confere ({digest}; esperado {SCRCPY_ZIP_SHA256})."
            )
        extract_dir = Path(tmp) / "x"
        extract_dir.mkdir()
        with zipfile.ZipFile(zpath) as zf:
            safe_extract_zip(zf, extract_dir)
        src_root = extract_dir
        exe_found = list(src_root.rglob("scrcpy.exe")) + list(src_root.rglob("scrcpy"))
        if not exe_found:
            raise RuntimeError("O zip do scrcpy não contém o executável.")
        src = exe_found[0].parent
        for item in src.iterdir():
            if not item.is_file():
                continue
            if item.name in _SKIP_SCRCPY_NAMES:
                continue
            shutil.copy2(item, dest / item.name)

    if not _is_scrcpy_exe(existing):
        raise RuntimeError(f"Falha ao preparar o scrcpy em {dest}.")
    return str(existing)


class DeviceAudioRecordThread(QThread):
    """Grava o áudio de saída do Mini PC em WAV (scrcpy ou tinycap) para mesclar no MP4."""

    status = Signal(str)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, adb_path: str, serial: str, out_path: Path) -> None:
        super().__init__()
        self.adb_path = str(adb_path)
        self.serial = str(serial)
        self.out_path = Path(out_path)
        self._stop = False
        self._popen: subprocess.Popen | None = None
        self._remote_wav: str | None = None
        # Instante (time.monotonic) em que o áudio começou de fato; None = desconhecido.
        self.audio_started_at: float | None = None

    def stop(self) -> None:
        """Pede a parada (não bloqueia a UI); a própria thread encerra o scrcpy."""
        self._stop = True

    def _signal_stop_process(self) -> None:
        p = self._popen
        if p is None or p.poll() is not None:
            return
        if sys.platform == "win32":
            if not send_ctrl_c(p.pid):
                try:
                    p.terminate()
                except Exception:
                    pass
        else:
            try:
                p.send_signal(signal.SIGINT)
            except Exception:
                pass

    def _interrupt_device_tools(self) -> None:
        for cmd in (
            "kill -INT $(pidof tinycap) 2>/dev/null",
            "pkill -INT tinycap 2>/dev/null",
            "killall -INT tinycap 2>/dev/null",
            "kill -INT $(ps -A -o PID,ARGS 2>/dev/null | grep -i genymobile.scrcpy | awk '{print $1}') 2>/dev/null",
        ):
            try:
                run_hidden(
                    [self.adb_path, "-s", self.serial, "shell", cmd],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    stdin=subprocess.DEVNULL,
                )
            except Exception:
                pass

    def run(self) -> None:
        try:
            self.out_path.parent.mkdir(parents=True, exist_ok=True)
            if self.out_path.exists():
                self.out_path.unlink(missing_ok=True)
        except Exception as e:
            self.failed.emit(f"Não foi possível preparar o arquivo de áudio: {e}")
            return

        sdk = _device_sdk(self.adb_path, self.serial)
        if sdk is not None and sdk >= 30:
            if self._capture_with_scrcpy(sdk):
                return
            if self._stop:
                self.failed.emit("Captura de áudio do Mini PC interrompida.")
                return

        tinycap_err = self._capture_with_tinycap()
        if tinycap_err is None:
            return
        self.failed.emit(
            tinycap_err
            if sdk is None or sdk < 30
            else f"{tinycap_err}\nÁudio interno do Mini PC não pôde ser capturado."
        )

    def _capture_with_scrcpy(self, sdk: int) -> bool:
        """True = já sinalizou sucesso/falha; False = tentar tinycap."""
        extra, warn = scrcpy_audio_args_for_sdk(sdk)
        if not extra:
            self.status.emit(warn or "Android antigo demais para áudio interno via ADB.")
            return False
        self.status.emit("Preparando captura de áudio do Mini PC…")
        try:
            scrcpy = ensure_scrcpy()
        except Exception as e:
            self.status.emit(f"scrcpy indisponível: {e}")
            return False

        if warn:
            self.status.emit(warn)

        env = os.environ.copy()
        env["ADB"] = self.adb_path
        server = Path(scrcpy).with_name("scrcpy-server")
        if server.is_file():
            env["SCRCPY_SERVER_PATH"] = str(server)

        cmd = [
            scrcpy,
            "-s",
            self.serial,
            "--no-window",
            "--no-playback",
            "--no-control",
            "--no-video",
            "--audio-codec",
            "raw",
            "--require-audio",
            "--time-limit",
            "180",
            "--record",
            str(self.out_path),
            *extra,
        ]
        self.status.emit("Áudio: capturando a saída do Mini PC…")
        started = time.monotonic()
        try:
            self._popen = popen_hidden(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=str(Path(scrcpy).parent),
            )
        except Exception as e:
            self.status.emit(f"Falha ao iniciar captura de áudio do Mini PC: {e}")
            return False

        stderr_lines: list[str] = []

        def _drain() -> None:
            stream = self._popen.stderr if self._popen is not None else None
            if stream is None:
                return
            for line in stream:
                stderr_lines.append(line.rstrip())
                if self.audio_started_at is None and "recording started" in line.lower():
                    self.audio_started_at = time.monotonic()

        drain = threading.Thread(target=_drain, daemon=True)
        drain.start()

        while True:
            if self._stop:
                break
            if self._popen.poll() is not None:
                break
            time.sleep(0.08)
        elapsed = time.monotonic() - started

        if self._stop and self._popen.poll() is None:
            self._signal_stop_process()
            try:
                self._popen.wait(timeout=10)
            except Exception:
                self._interrupt_device_tools()
                try:
                    self._popen.terminate()
                except Exception:
                    pass
                try:
                    self._popen.wait(timeout=5)
                except Exception:
                    try:
                        self._popen.kill()
                    except Exception:
                        pass

        drain.join(timeout=3)
        stderr_txt = "\n".join(stderr_lines[-6:]).strip()

        if self.out_path.exists() and self.out_path.stat().st_size > 1024:
            self.finished.emit(str(self.out_path))
            return True
        detail = stderr_txt or "arquivo de áudio vazio"
        msg = f"scrcpy não gravou o áudio do Mini PC ({detail})."
        if elapsed >= 3.0:
            self.failed.emit(msg)
            return True
        self.status.emit(msg)
        return False

    def _capture_with_tinycap(self) -> str | None:
        """None = sucesso; string = erro."""
        which = _shell_out(self.adb_path, self.serial, "command -v tinycap || which tinycap")
        if "tinycap" not in which.lower() and "tinycap" not in _shell_out(
            self.adb_path, self.serial, "ls /system/bin/tinycap /vendor/bin/tinycap 2>/dev/null"
        ):
            return "O Mini PC não tem tinycap para fallback de áudio."

        pcm = _shell_out(self.adb_path, self.serial, "cat /proc/asound/pcm")
        target = pick_alsa_capture(parse_alsa_captures(pcm))
        if target is None:
            return "Não há dispositivo ALSA de captura do som HDMI/mix (só microfone, ignorado)."

        remote = "/data/local/tmp/aibox_audio.wav"
        self._remote_wav = remote
        _shell_out(self.adb_path, self.serial, f"rm -f {remote}", timeout_s=5)
        cmd = (
            f"tinycap {remote} -D {target.card} -d {target.device} "
            f"-c 2 -r 44100 -b 16"
        )
        self.status.emit(
            f"Áudio: capturando «{target.name}» (card {target.card}, device {target.device})…"
        )
        try:
            self._popen = popen_hidden(
                [self.adb_path, "-s", self.serial, "shell", cmd],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except Exception as e:
            return f"Falha ao iniciar tinycap: {e}"

        while True:
            if self._stop:
                self._interrupt_device_tools()
                break
            if self._popen.poll() is not None:
                break
            time.sleep(0.08)

        if self._popen.poll() is None:
            try:
                self._popen.wait(timeout=8)
            except Exception:
                try:
                    self._popen.kill()
                except Exception:
                    pass

        time.sleep(0.4)
        pull = run_hidden(
            [self.adb_path, "-s", self.serial, "pull", remote, str(self.out_path)],
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
        _shell_out(self.adb_path, self.serial, f"rm -f {remote}", timeout_s=5)
        if pull.returncode == 0 and self.out_path.exists() and self.out_path.stat().st_size > 1024:
            self.finished.emit(str(self.out_path))
            return None
        err = (pull.stderr or pull.stdout or "").strip()
        return err or "tinycap não gerou áudio utilizável."
