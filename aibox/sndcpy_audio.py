"""Captura o áudio reproduzido no aparelho Android 10+ com o aplicativo auxiliar sndcpy.

O scrcpy só captura áudio interno a partir do Android 11. No Android 10 (muitos Mini PCs de
totem) usamos o sndcpy (MIT, Romain Vimont): um APK minúsculo que grava a reprodução com
`AudioPlaybackCapture` e a serve em um socket local. O consentimento de captura é liberado
pelo `appops PROJECT_MEDIA`, então não aparece nenhum diálogo na tela do aparelho.

Fluxo: instalar o APK (idempotente) → liberar PROJECT_MEDIA → `adb forward` → iniciar a
activity → ler PCM (48 kHz, estéreo, 16 bits) até pedirem para parar → limpar tudo.
"""
from __future__ import annotations

import array
import hashlib
import math
import socket
import subprocess
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .paths import executable_dir, is_frozen, package_dir, resource_path
from .procutil import run_hidden

SNDCPY_VERSION = "1.1"
SNDCPY_PACKAGE = "com.rom1v.sndcpy"
SNDCPY_ZIP_URL = (
    f"https://github.com/rom1v/sndcpy/releases/download/v{SNDCPY_VERSION}/sndcpy-v{SNDCPY_VERSION}.zip"
)
SNDCPY_ZIP_SHA256 = "b045320ddddbc5a237d6d95213c664f03b2f46ef8bbfcef91c2d95644ed42bbc"
SNDCPY_APK_SHA256 = "4216b69b647aeec7cbe9ab7d991f406eb27ebc7f76e0d1fadd4c291d23eff03c"
SNDCPY_LICENSE_URL = f"https://raw.githubusercontent.com/rom1v/sndcpy/v{SNDCPY_VERSION}/LICENSE"
SNDCPY_LICENSE_SHA256 = "6b6d5219c4732200fdf483c5a9bde69867a1386b6278c176ce3598c32132e66d"

SAMPLE_RATE = 48000
CHANNELS = 2
SAMPLE_WIDTH = 2
MAX_SECONDS = 185  # screenrecord para em 180 s


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def resolve_sndcpy_apk() -> Path | None:
    """APK distribuído com o app, conferido pelo SHA-256 fixado (nunca um arquivo qualquer)."""
    dirs = [resource_path("sndcpy"), package_dir() / "sndcpy"]
    if is_frozen():
        dirs.append(executable_dir() / "_internal" / "aibox" / "sndcpy")
    for d in dirs:
        apk = d / "sndcpy.apk"
        try:
            if apk.is_file() and sha256_file(apk) == SNDCPY_APK_SHA256:
                return apk
        except OSError:
            continue
    return None


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def pcm_to_wav(pcm_path: Path, wav_path: Path) -> int:
    """Converte PCM cru em WAV (cópia em blocos). Devolve o nº de bytes de áudio."""
    total = 0
    with pcm_path.open("rb") as src, wave.open(str(wav_path), "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(SAMPLE_RATE)
        frame = CHANNELS * SAMPLE_WIDTH
        carry = b""
        for block in iter(lambda: src.read(1024 * 1024), b""):
            block = carry + block
            usable = len(block) - (len(block) % frame)
            carry = block[usable:]
            if usable:
                w.writeframes(block[:usable])
                total += usable
    return total


def pcm_peak_dbfs(pcm_path: Path) -> float | None:
    """Pico em dBFS do PCM (None = vazio, -inf = silêncio digital)."""
    peak = 0
    seen = False
    with pcm_path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            block = block[: len(block) - (len(block) % 2)]
            if not block:
                continue
            seen = True
            samples = array.array("h")
            samples.frombytes(block)
            if samples:
                peak = max(peak, max(max(samples), -min(samples)))
    if not seen:
        return None
    if peak == 0:
        return -math.inf
    return 20.0 * math.log10(min(peak, 32767) / 32768.0)


@dataclass
class SndcpyResult:
    ok: bool
    bytes_written: int = 0
    reason: str = ""


class SndcpyCapture:
    """Executa a captura (sem Qt: testável). `should_stop` é consultado a cada ~0,25 s."""

    def __init__(
        self,
        adb_path: str,
        serial: str,
        pcm_path: Path,
        *,
        should_stop: Callable[[], bool],
        status: Callable[[str], None],
        on_first_data: Callable[[], None] | None = None,
        connect_deadline_s: float = 12.0,
    ) -> None:
        self.adb_path = str(adb_path)
        self.serial = str(serial)
        self.pcm_path = Path(pcm_path)
        self.should_stop = should_stop
        self.status = status
        self.on_first_data = on_first_data
        self.connect_deadline_s = connect_deadline_s
        self._port: int | None = None
        self._sock: socket.socket | None = None

    # -- adb helpers -------------------------------------------------------------------------
    def _adb(self, *args: str, timeout_s: int = 15) -> subprocess.CompletedProcess:
        return run_hidden(
            [self.adb_path, "-s", self.serial, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,
        )

    def _prepare_device(self, apk: Path) -> str | None:
        """Instala/libera o app. Devolve uma mensagem de erro ou None."""
        if not self._already_installed():
            cp = self._adb("install", "-t", "-r", "-g", str(apk), timeout_s=90)
            out = f"{cp.stdout or ''}{cp.stderr or ''}"
            if cp.returncode != 0 or "success" not in out.lower():
                return (
                    "não foi possível instalar o aplicativo auxiliar de áudio "
                    f"({out.strip()[:160] or 'sem detalhes'})"
                )
        self._adb("shell", "appops", "set", SNDCPY_PACKAGE, "PROJECT_MEDIA", "allow")
        self._adb("shell", "am", "force-stop", SNDCPY_PACKAGE)  # estado limpo
        self._port = free_port()
        cp = self._adb("forward", f"tcp:{self._port}", "localabstract:sndcpy")
        if cp.returncode != 0:
            return f"adb forward falhou ({(cp.stderr or cp.stdout or '').strip()[:120]})"
        self._adb("shell", "am", "start", f"{SNDCPY_PACKAGE}/.MainActivity", timeout_s=15)
        return None

    def _already_installed(self) -> bool:
        """True se a versão certa já está no aparelho com a permissão de áudio (poupa ~5 s no Wi-Fi)."""
        try:
            cp = self._adb("shell", "dumpsys", "package", SNDCPY_PACKAGE, timeout_s=15)
        except Exception:  # noqa: BLE001
            return False
        text = cp.stdout or ""
        return (
            f"versionName={SNDCPY_VERSION}" in text
            and "android.permission.RECORD_AUDIO: granted=true" in text
        )

    def _cleanup_device(self) -> None:
        try:
            if self._sock is not None:
                self._sock.close()
        except OSError:
            pass
        self._sock = None
        for args in (
            ("shell", "am", "force-stop", SNDCPY_PACKAGE),
            ("forward", "--remove", f"tcp:{self._port}") if self._port else None,
        ):
            if not args:
                continue
            try:
                self._adb(*args, timeout_s=8)
            except Exception:
                pass

    # -- captura -----------------------------------------------------------------------------
    def _connect_and_wait_first_data(self) -> tuple[socket.socket | None, bytes]:
        deadline = time.monotonic() + self.connect_deadline_s
        while time.monotonic() < deadline and not self.should_stop():
            try:
                s = socket.create_connection(("127.0.0.1", self._port), timeout=2.0)
            except OSError:
                time.sleep(0.4)
                continue
            s.settimeout(2.0)
            try:
                first = s.recv(65536)
            except (socket.timeout, OSError):
                first = b""
            if first:
                return s, first
            # O adb aceita a conexão local antes do app abrir o socket: tenta de novo.
            s.close()
            time.sleep(0.5)
        return None, b""

    def run(self) -> SndcpyResult:
        apk = resolve_sndcpy_apk()
        if apk is None:
            return SndcpyResult(False, reason="aplicativo auxiliar de áudio (sndcpy) ausente ou adulterado")
        self.status("Áudio: preparando o aplicativo auxiliar no aparelho…")
        try:
            err = self._prepare_device(apk)
            if err:
                return SndcpyResult(False, reason=err)
            sock, first = self._connect_and_wait_first_data()
            if sock is None:
                if self.should_stop():
                    return SndcpyResult(False, reason="interrompido antes de começar")
                return SndcpyResult(
                    False,
                    reason=(
                        "o aparelho não enviou áudio. Se aparecer um pedido de captura na tela dele, "
                        "aceite «Começar agora» e grave de novo"
                    ),
                )
            self._sock = sock
            self.status("Áudio: capturando o som do aparelho (aplicativo auxiliar)…")
            if self.on_first_data:
                self.on_first_data()
            written = 0
            started = time.monotonic()
            with self.pcm_path.open("wb") as out:
                out.write(first)
                written += len(first)
                sock.settimeout(0.25)
                while not self.should_stop() and time.monotonic() - started < MAX_SECONDS:
                    try:
                        block = sock.recv(65536)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    if not block:
                        break
                    out.write(block)
                    written += len(block)
            return SndcpyResult(True, bytes_written=written)
        except Exception as exc:  # noqa: BLE001
            return SndcpyResult(False, reason=f"erro inesperado: {exc}")
        finally:
            self._cleanup_device()
