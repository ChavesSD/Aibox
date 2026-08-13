from __future__ import annotations

import re
import subprocess
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
    status = Signal(str)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, device_name: str, out_path: Path, *, display_name: str | None = None) -> None:
        super().__init__()
        self.device_name = str(device_name).strip()
        self.display_name = (display_name or self.device_name).strip()
        self.out_path = Path(out_path)
        self._stop = False
        self._popen: subprocess.Popen | None = None

    def stop(self) -> None:
        self._stop = True
        p = self._popen
        if p is None:
            return
        try:
            if p.stdin is not None:
                p.stdin.write("q\n")
                p.stdin.flush()
        except Exception:
            pass
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if p.poll() is not None:
                return
            time.sleep(0.05)
        try:
            p.terminate()
        except Exception:
            pass
        try:
            p.wait(timeout=4)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass

    def run(self) -> None:
        ffmpeg = _resolve_ffmpeg()
        if not ffmpeg:
            self.failed.emit("FFmpeg não encontrado. Necessário para gravar áudio do microfone.")
            return
        if not self.device_name:
            self.failed.emit("Selecione um microfone.")
            return

        try:
            self.out_path.parent.mkdir(parents=True, exist_ok=True)
            if self.out_path.exists():
                self.out_path.unlink(missing_ok=True)
        except Exception as e:
            self.failed.emit(f"Não foi possível preparar o arquivo de áudio: {e}")
            return

        # Nome alternativo (@device_…) é ASCII e evita erro de encoding no DirectShow.
        input_spec = f"audio={self.device_name}"
        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "dshow",
            "-i",
            input_spec,
            "-ac",
            "1",
            "-ar",
            "44100",
            "-c:a",
            "pcm_s16le",
            str(self.out_path),
        ]
        self.status.emit(f"Áudio: gravando de «{self.display_name}»…")
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
            self.failed.emit(f"Falha ao iniciar captura de áudio: {e}")
            return

        while True:
            if self._stop:
                break
            if self._popen.poll() is not None:
                break
            time.sleep(0.08)

        if self._stop and self._popen.poll() is None:
            try:
                self._popen.wait(timeout=8)
            except Exception:
                try:
                    self._popen.kill()
                except Exception:
                    pass

        stderr_txt = ""
        try:
            if self._popen.stderr is not None:
                stderr_txt = (self._popen.stderr.read() or "").strip()
        except Exception:
            stderr_txt = ""

        rc = int(self._popen.returncode if self._popen.returncode is not None else 1)
        if self.out_path.exists() and self.out_path.stat().st_size > 1024:
            self.finished.emit(str(self.out_path))
            return

        msg = stderr_txt or f"Falha na gravação de áudio (código {rc})."
        self.failed.emit(msg)
