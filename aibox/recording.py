from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .procutil import popen_hidden, run_hidden


def _is_executable_file(p: Path) -> bool:
    try:
        return p.exists() and p.is_file()
    except OSError:
        return False


def _check_binary(path: str, version_args: list[str]) -> bool:
    try:
        cp = run_hidden([path] + version_args, capture_output=True, text=True, timeout=6)
    except Exception:
        return False
    return cp.returncode == 0


def _iter_windows_registered_paths() -> list[Path]:
    if os.name != "nt":
        return []
    try:
        import winreg  # type: ignore
    except Exception:
        return []

    def read_path(key, subkey: str) -> list[str]:
        try:
            with winreg.OpenKey(key, subkey) as k:
                val, _typ = winreg.QueryValueEx(k, "Path")
        except Exception:
            return []
        if not isinstance(val, str):
            return []
        return [p for p in val.split(";") if p.strip()]

    raw = []
    raw += read_path(winreg.HKEY_CURRENT_USER, r"Environment")
    raw += read_path(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")

    out: list[Path] = []
    seen: set[str] = set()
    for seg in raw:
        seg = os.path.expandvars(seg.strip().strip('"'))
        if not seg:
            continue
        norm = os.path.normcase(os.path.normpath(seg))
        if norm in seen:
            continue
        seen.add(norm)
        out.append(Path(seg))
    return out


def _resolve_ffmpeg() -> str | None:
    env = (
        os.environ.get("AIBOX_FFMPEG_PATH")
        or os.environ.get("CONTROID_FFMPEG_PATH")
        or os.environ.get("FFMPEG_PATH")
    )
    if env:
        p = Path(env)
        if _is_executable_file(p) and _check_binary(str(p), ["-version"]):
            return str(p)

    env_dir = os.environ.get("AIBOX_FFMPEG_DIR") or os.environ.get("CONTROID_FFMPEG_DIR")
    if env_dir:
        for name in ("ffmpeg.exe", "ffmpeg"):
            p = Path(env_dir) / name
            if _is_executable_file(p) and _check_binary(str(p), ["-version"]):
                return str(p)
        for name in ("ffmpeg.exe", "ffmpeg"):
            p = Path(env_dir) / "bin" / name
            if _is_executable_file(p) and _check_binary(str(p), ["-version"]):
                return str(p)

    which = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if which and _check_binary(which, ["-version"]):
        return which

    from .paths import executable_dir, package_dir

    base = package_dir()
    roots = [base, base.parent, executable_dir(), Path.cwd()]
    for r in roots:
        for rel in (
            Path("ffmpeg.exe"),
            Path("ffmpeg") / "ffmpeg.exe",
            Path("ffmpeg") / "bin" / "ffmpeg.exe",
            Path("ffmpeg") / "bin" / "ffmpeg",
            Path("tools") / "ffmpeg.exe",
            Path("tools") / "ffmpeg" / "bin" / "ffmpeg.exe",
        ):
            p = r / rel
            if _is_executable_file(p) and _check_binary(str(p), ["-version"]):
                return str(p)

    for d in _iter_windows_registered_paths():
        for name in ("ffmpeg.exe", "ffmpeg"):
            p = d / name
            if _is_executable_file(p) and _check_binary(str(p), ["-version"]):
                return str(p)

    return None


def _resolve_ffprobe() -> str | None:
    env = (
        os.environ.get("AIBOX_FFPROBE_PATH")
        or os.environ.get("CONTROID_FFPROBE_PATH")
        or os.environ.get("FFPROBE_PATH")
    )
    if env:
        p = Path(env)
        if _is_executable_file(p) and _check_binary(str(p), ["-version"]):
            return str(p)

    env_dir = os.environ.get("AIBOX_FFMPEG_DIR") or os.environ.get("CONTROID_FFMPEG_DIR")
    if env_dir:
        for name in ("ffprobe.exe", "ffprobe"):
            p = Path(env_dir) / name
            if _is_executable_file(p) and _check_binary(str(p), ["-version"]):
                return str(p)
        for name in ("ffprobe.exe", "ffprobe"):
            p = Path(env_dir) / "bin" / name
            if _is_executable_file(p) and _check_binary(str(p), ["-version"]):
                return str(p)

    which = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    if which and _check_binary(which, ["-version"]):
        return which

    from .paths import executable_dir, package_dir

    base = package_dir()
    roots = [base, base.parent, executable_dir(), Path.cwd()]
    for r in roots:
        for rel in (
            Path("ffprobe.exe"),
            Path("ffmpeg") / "bin" / "ffprobe.exe",
            Path("ffmpeg") / "bin" / "ffprobe",
            Path("tools") / "ffmpeg" / "bin" / "ffprobe.exe",
        ):
            p = r / rel
            if _is_executable_file(p) and _check_binary(str(p), ["-version"]):
                return str(p)

    for d in _iter_windows_registered_paths():
        for name in ("ffprobe.exe", "ffprobe"):
            p = d / name
            if _is_executable_file(p) and _check_binary(str(p), ["-version"]):
                return str(p)

    return None


def _duration_seconds_ffprobe(path: Path) -> float | None:
    ffprobe = _resolve_ffprobe()
    if not ffprobe:
        return None

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    cp = run_hidden(cmd, capture_output=True, text=True, timeout=20)
    if cp.returncode != 0:
        return None
    try:
        obj = json.loads(cp.stdout or "{}")
    except Exception:
        return None
    fmt = obj.get("format") if isinstance(obj, dict) else None
    if not isinstance(fmt, dict):
        return None
    raw = fmt.get("duration")
    try:
        return float(raw)
    except Exception:
        return None


def _mp4_is_playable(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 1024:
        return False
    try:
        data = path.read_bytes()[:65536]
    except OSError:
        return False
    if b"ftyp" not in data:
        return False
    # moov pode estar no início (faststart) ou no fim do arquivo
    try:
        raw = path.read_bytes() if path.stat().st_size <= 8 * 1024 * 1024 else None
    except OSError:
        raw = None
    if raw is not None:
        if b"moov" not in raw:
            return False
    else:
        # arquivo grande: confia no ffprobe
        pass
    d = _duration_seconds_ffprobe(path)
    return d is not None and d > 0.2


def _convert_h264_to_mp4(
    ffmpeg: str,
    raw_path: Path,
    mp4_path: Path,
    fps: int,
    expected_dur_s: float | None = None,
) -> tuple[bool, str]:
    cmd_encode = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-framerate",
        str(int(fps)),
        "-i",
        str(raw_path),
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-shortest",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-vsync",
        "cfr",
        "-vf",
        f"fps={int(fps)}",
        "-r",
        str(int(fps)),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(mp4_path),
    ]
    cp2 = run_hidden(cmd_encode, capture_output=True, text=True, timeout=300)
    if cp2.returncode == 0 and mp4_path.exists() and mp4_path.stat().st_size > 1024:
        if expected_dur_s is None:
            return True, ""
        d = _duration_seconds_ffprobe(mp4_path)
        if d is None or expected_dur_s <= 0:
            return True, ""
        tolerance = max(0.5, expected_dur_s * 0.04)
        if abs(d - expected_dur_s) <= tolerance:
            return True, ""
        return False, f"Duração inesperada: {d:.2f}s (esperado ~{expected_dur_s:.2f}s)."

    msg = (cp2.stderr or cp2.stdout or "").strip()
    return False, msg or "Falha na conversão."


def _remux_compatible_mp4(ffmpeg: str, src: Path, dst: Path) -> tuple[bool, str]:
    """Gera MP4 compatível (H.264 + AAC + faststart) para players do Windows."""
    attempts: list[list[str]] = [
        # 1) remux rápido preservando vídeo
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-shortest",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            str(dst),
        ],
        # 2) reencode compatível com Windows Media Player
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-shortest",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "main",
            "-level",
            "4.0",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            str(dst),
        ],
    ]
    last_err = ""
    for cmd in attempts:
        try:
            if dst.exists():
                dst.unlink(missing_ok=True)
        except Exception:
            pass
        cp = run_hidden(cmd, capture_output=True, text=True, timeout=300)
        if cp.returncode == 0 and _mp4_is_playable(dst):
            return True, ""
        last_err = (cp.stderr or cp.stdout or "").strip()
        try:
            if dst.exists():
                dst.unlink(missing_ok=True)
        except Exception:
            pass
    return False, last_err or "Falha ao gerar MP4 compatível."


class ScreenRecordThread(QThread):
    status = Signal(str)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        adb_path: str,
        serial: str,
        out_path: Path,
        bit_rate: int = 4000000,
        size: str | None = None,
        time_limit_s: int = 180,
        started_at: float | None = None,
    ) -> None:
        super().__init__()
        self.adb_path = str(adb_path)
        self.serial = str(serial)
        self.out_path = Path(out_path)
        self.bit_rate = int(bit_rate) if bit_rate else 0
        self.size = size
        self.time_limit_s = max(1, min(int(time_limit_s), 180))
        self.started_at = float(started_at) if started_at is not None else time.monotonic()
        self._stop = False
        self._popen: subprocess.Popen | None = None
        self._remote: str | None = None

    def stop(self) -> None:
        """Interrompe o screenrecord com SIGINT e deixa o ADB finalizar o arquivo (moov)."""
        self._stop = True
        for shell_cmd in (
            "kill -INT $(pidof screenrecord) 2>/dev/null",
            "pkill -INT screenrecord 2>/dev/null",
            "killall -INT screenrecord 2>/dev/null",
        ):
            try:
                run_hidden(
                    [self.adb_path, "-s", self.serial, "shell", shell_cmd],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    stdin=subprocess.DEVNULL,
                )
            except Exception:
                pass
        # Não mata o processo adb imediatamente: o screenrecord precisa gravar o átomo moov.

    def _wait_screenrecord_exit(self, grace_s: float = 20.0) -> None:
        if self._popen is None:
            return
        deadline = time.monotonic() + max(1.0, grace_s)
        while time.monotonic() < deadline:
            if self._popen.poll() is not None:
                return
            time.sleep(0.1)
        try:
            self._popen.terminate()
        except Exception:
            pass
        try:
            self._popen.wait(timeout=8)
        except Exception:
            try:
                self._popen.kill()
            except Exception:
                pass

    def run(self) -> None:
        remote: str | None = None
        try:
            started_at = self.started_at
            self.out_path.parent.mkdir(parents=True, exist_ok=True)
            ts = int(time.time())
            remote = f"/sdcard/aibox_record_{ts}.mp4"
            self._remote = remote

            cmd = [self.adb_path, "-s", self.serial, "shell", "screenrecord"]
            if self.size:
                cmd += ["--size", str(self.size)]
            if self.bit_rate > 0:
                cmd += ["--bit-rate", str(int(self.bit_rate))]
            cmd += ["--time-limit", str(int(self.time_limit_s)), remote]

            self.status.emit(f"$ {' '.join(cmd)}")
            self._popen = popen_hidden(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                bufsize=0,
            )

            while True:
                if self._stop:
                    self._wait_screenrecord_exit(grace_s=20.0)
                    break
                if self._popen.poll() is not None:
                    break
                time.sleep(0.08)

            try:
                self._popen.wait(timeout=5)
            except Exception:
                try:
                    self._popen.kill()
                except Exception:
                    pass

            elapsed = max(0.0, time.monotonic() - started_at)
            rc = int(self._popen.returncode or 0)

            stderr_txt = ""
            try:
                if self._popen.stderr is not None:
                    stderr_txt = self._popen.stderr.read().decode("utf-8", "replace").strip()
            except Exception:
                stderr_txt = ""
            try:
                if self._popen.stderr is not None:
                    self._popen.stderr.close()
            except Exception:
                pass

            # screenrecord costuma sair 0 ou 130/143 após SIGINT; só falha se encerrou sozinho cedo demais
            if (not self._stop) and (rc not in (0, 130, 137, 143) or elapsed < 1.5):
                msg = "A gravação encerrou automaticamente."
                if stderr_txt:
                    msg = (msg + "\n\n" + stderr_txt).strip()
                self.failed.emit(msg)
                return

            # dá tempo do Android fechar o MP4 no disco antes do pull
            time.sleep(1.2)
            try:
                run_hidden(
                    [self.adb_path, "-s", self.serial, "shell", "sync"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    stdin=subprocess.DEVNULL,
                )
            except Exception:
                pass

            pull_tmp = self.out_path.with_name(self.out_path.stem + ".pull.mp4")
            try:
                if pull_tmp.exists():
                    pull_tmp.unlink(missing_ok=True)
            except Exception:
                pass

            cp_pull = run_hidden(
                [self.adb_path, "-s", self.serial, "pull", remote, str(pull_tmp)],
                capture_output=True,
                text=True,
                timeout=300,
                stdin=subprocess.DEVNULL,
            )
            if cp_pull.returncode != 0 or not pull_tmp.exists() or pull_tmp.stat().st_size < 1024:
                err = (cp_pull.stderr or cp_pull.stdout or "").strip()
                self.failed.emit((err or "Falha ao baixar o vídeo do dispositivo.").strip())
                return

            if not _mp4_is_playable(pull_tmp):
                try:
                    pull_tmp.unlink(missing_ok=True)
                except Exception:
                    pass
                self.failed.emit(
                    "O arquivo gravado no aparelho ficou incompleto (sem metadados MP4).\n\n"
                    "Isso costuma acontecer se a gravação for interrompida de forma abrupta "
                    "ou se a conexão Wi‑Fi oscilar. Grave novamente e aguarde alguns segundos "
                    "após clicar em Parar."
                )
                return

            ffmpeg = _resolve_ffmpeg()
            if ffmpeg:
                out_tmp = self.out_path.with_name(self.out_path.stem + ".compat.mp4")
                ok, err = _remux_compatible_mp4(ffmpeg, pull_tmp, out_tmp)
                try:
                    pull_tmp.unlink(missing_ok=True)
                except Exception:
                    pass
                if not ok:
                    self.failed.emit(
                        "Não foi possível gerar um MP4 compatível com o Windows.\n\n"
                        + (err or "Falha no pós-processamento.")
                    )
                    return
                out_tmp.replace(self.out_path)
            else:
                # Sem ffmpeg: só aceita o arquivo se já for reproduzível
                pull_tmp.replace(self.out_path)
                if not _mp4_is_playable(self.out_path):
                    try:
                        self.out_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    self.failed.emit(
                        "Vídeo incompleto e ffmpeg não encontrado para corrigir o arquivo.\n"
                        "Instale o ffmpeg ou grave novamente."
                    )
                    return

            d = _duration_seconds_ffprobe(self.out_path)
            if d is not None:
                self.status.emit(f"Duração MP4: {d:.2f}s")

            if not _mp4_is_playable(self.out_path):
                self.failed.emit("O MP4 final não pôde ser validado. Tente gravar novamente.")
                return

            self.finished.emit(str(self.out_path))
        except Exception as e:
            self.failed.emit(str(e))
        finally:
            if remote:
                try:
                    run_hidden(
                        [self.adb_path, "-s", self.serial, "shell", "rm", "-f", remote],
                        capture_output=True,
                        text=True,
                        timeout=20,
                        stdin=subprocess.DEVNULL,
                    )
                except Exception:
                    pass
