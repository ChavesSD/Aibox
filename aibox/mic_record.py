"""Gravação do microfone do COMPUTADOR (DirectShow via ffmpeg), junto com a gravação de tela."""
from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .procutil import popen_hidden, run_hidden
from .recording import _resolve_ffmpeg


@dataclass(frozen=True)
class MicDevice:
    """Microfone DirectShow: rótulo amigável + nome estável para o FFmpeg."""

    label: str
    dshow_name: str


def _decode_ffmpeg_output(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    for enc in ("utf-8", "utf-16-le", "cp1252", "mbcs"):
        try:
            text = raw.decode(enc)
            # Preferir decodificação que preserve ® corretamente
            if "Â®" not in text or enc == "utf-8":
                return text
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def list_microphones() -> list[MicDevice]:
    """Lista microfones disponíveis no Windows via DirectShow (ffmpeg)."""
    ffmpeg = _resolve_ffmpeg()
    if not ffmpeg:
        return []

    try:
        cp = run_hidden(
            [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True,
            timeout=12,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        return []

    text = _decode_ffmpeg_output(cp.stderr) + "\n" + _decode_ffmpeg_output(cp.stdout)
    # Corrige mojibake clássico UTF-8 lido como cp1252 (ex.: IntelÂ® → Intel®)
    if "Â®" in text or "Ã" in text:
        try:
            text = text.encode("cp1252", errors="ignore").decode("utf-8", errors="ignore") or text
        except Exception:
            pass

    devices: list[MicDevice] = []
    in_audio = False
    pending_label: str | None = None
    expect_alt = False

    for line in text.splitlines():
        low = line.lower()
        if "directshow audio devices" in low or ("audio devices" in low and "directshow" in low):
            in_audio = True
            pending_label = None
            expect_alt = False
            continue
        if "directshow video devices" in low or ("video devices" in low and "directshow" in low):
            in_audio = False
            pending_label = None
            expect_alt = False
            continue

        if expect_alt and pending_label:
            alt_only = re.search(r'"([^"]+)"', line)
            if alt_only:
                alt_name = alt_only.group(1).strip()
                open_name = alt_name or pending_label
                if not any(d.label == pending_label for d in devices):
                    devices.append(MicDevice(label=pending_label, dshow_name=open_name))
                pending_label = None
                expect_alt = False
                continue

        alt = re.search(r'Alternative name\s*"([^"]+)"', line, re.IGNORECASE)
        if alt and pending_label:
            alt_name = alt.group(1).strip()
            open_name = alt_name if alt_name else pending_label
            if not any(d.label == pending_label for d in devices):
                devices.append(MicDevice(label=pending_label, dshow_name=open_name))
            pending_label = None
            expect_alt = False
            continue

        if "alternative name" in low and pending_label:
            # Nome na próxima linha (alguns builds do FFmpeg quebram a linha)
            expect_alt = True
            continue

        m = re.search(r'"([^"]+)"\s*\((audio|none)\)', line, re.IGNORECASE)
        if not m:
            if "(audio)" in low:
                m2 = re.search(r'"([^"]+)"', line)
                if m2 and in_audio:
                    if pending_label and not any(d.label == pending_label for d in devices):
                        devices.append(MicDevice(label=pending_label, dshow_name=pending_label))
                    pending_label = m2.group(1).strip()
                    expect_alt = False
            continue

        kind = m.group(2).lower()
        name = m.group(1).strip()
        if not name:
            continue
        if kind == "audio" or in_audio:
            if pending_label and not any(d.label == pending_label for d in devices):
                devices.append(MicDevice(label=pending_label, dshow_name=pending_label))
            pending_label = name
            expect_alt = False

    if pending_label and not any(d.label == pending_label for d in devices):
        devices.append(MicDevice(label=pending_label, dshow_name=pending_label))

    return devices


class MicRecordThread(QThread):
    """Grava o microfone do PC em WAV (pcm 44,1 kHz mono) para mesclar no MP4."""

    status = Signal(str)
    finished = Signal(str)
    failed = Signal(str)
    # Emitido quando o ffmpeg já está capturando (ou desistiu): libera o início do vídeo.
    ready = Signal()

    def __init__(
        self,
        device_name: str,
        out_path: Path,
        *,
        display_name: str | None = None,
        input_args: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.device_name = str(device_name).strip()
        self.display_name = (display_name or self.device_name).strip()
        self.out_path = Path(out_path)
        # `input_args` existe para testes (fonte lavfi); em produção é DirectShow.
        self._input_args = list(input_args) if input_args else ["-f", "dshow", "-i", f"audio={self.device_name}"]
        self._stop = False
        self._ready_sent = False
        self._popen: subprocess.Popen | None = None
        # Instante (time.monotonic) em que a captura começou de fato; None = desconhecido.
        self.audio_started_at: float | None = None

    def stop(self) -> None:
        """Pede a parada (não bloqueia a UI); a própria thread finaliza o ffmpeg com «q»."""
        self._stop = True

    def _emit_ready(self) -> None:
        if not self._ready_sent:
            self._ready_sent = True
            self.ready.emit()

    def run(self) -> None:
        try:
            self._run_impl()
        finally:
            self._emit_ready()  # sucesso ou falha: nunca deixa o vídeo esperando

    def _run_impl(self) -> None:
        ffmpeg = _resolve_ffmpeg()
        if not ffmpeg:
            self.failed.emit("FFmpeg não encontrado. Necessário para gravar áudio do microfone.")
            return
        if not self._input_args or (not self.device_name and "dshow" in self._input_args):
            self.failed.emit("Selecione um microfone.")
            return

        try:
            self.out_path.parent.mkdir(parents=True, exist_ok=True)
            if self.out_path.exists():
                self.out_path.unlink(missing_ok=True)
        except Exception as e:
            self.failed.emit(f"Não foi possível preparar o arquivo de áudio: {e}")
            return

        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-nostats",
            "-loglevel",
            "info",
            *self._input_args,
            "-ac",
            "1",
            "-ar",
            "44100",
            "-c:a",
            "pcm_s16le",
            str(self.out_path),
        ]
        self.status.emit(f"Microfone do PC: preparando «{self.display_name}»…")
        try:
            self._popen = popen_hidden(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except Exception as e:
            self.failed.emit(f"Falha ao iniciar a captura do microfone: {e}")
            return

        lines: list[str] = []

        def _drain() -> None:
            stream = self._popen.stderr if self._popen is not None else None
            if stream is None:
                return
            for line in stream:
                lines.append(line.rstrip())
                # «Output #0» aparece logo antes de o ffmpeg começar a gravar.
                if self.audio_started_at is None and line.lstrip().startswith("Output #0"):
                    self.audio_started_at = time.monotonic()
                    self.status.emit(f"Microfone do PC: gravando de «{self.display_name}»…")
                    self._emit_ready()

        drain = threading.Thread(target=_drain, daemon=True)
        drain.start()

        started = time.monotonic()
        while True:
            if self._stop:
                break
            if self._popen.poll() is not None:
                break
            if not self._ready_sent and time.monotonic() - started > 5.0:
                self._emit_ready()  # a linha pode não aparecer: não trava o vídeo
            time.sleep(0.05)

        if self._popen.poll() is None:
            try:
                if self._popen.stdin is not None:
                    self._popen.stdin.write("q\n")  # encerra o ffmpeg finalizando o WAV
                    self._popen.stdin.flush()
            except Exception:
                pass
            try:
                self._popen.wait(timeout=6)
            except Exception:
                try:
                    self._popen.terminate()
                    self._popen.wait(timeout=4)
                except Exception:
                    try:
                        self._popen.kill()
                    except Exception:
                        pass
        drain.join(timeout=3)

        rc = int(self._popen.returncode if self._popen.returncode is not None else 1)
        if self.out_path.exists() and self.out_path.stat().st_size > 1024:
            self.finished.emit(str(self.out_path))
            return

        detail = "\n".join(lines[-4:]).strip()
        self.failed.emit(
            "Não foi possível gravar o microfone do PC"
            + (f": {detail}" if detail else f" (código {rc}).")
            + "\nConfira se o microfone está habilitado em Configurações do Windows → Privacidade → Microfone."
        )
