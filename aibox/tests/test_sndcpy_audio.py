from __future__ import annotations

import math
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

from aibox import device_audio, sndcpy_audio  # noqa: E402
from aibox.sndcpy_audio import (  # noqa: E402
    SNDCPY_APK_SHA256,
    SndcpyCapture,
    pcm_peak_dbfs,
    pcm_to_wav,
    resolve_sndcpy_apk,
)


def _tone(seconds: float, amp: float = 0.5, rate: int = 48000) -> bytes:
    n = int(seconds * rate)
    frames = bytearray()
    for i in range(n):
        v = int(amp * 32767 * math.sin(2 * math.pi * 440 * i / rate))
        frames += struct.pack("<hh", v, v)
    return bytes(frames)


class FakeDeviceServer:
    """Imita o socket do sndcpy depois do `adb forward`."""

    def __init__(self, payload: bytes, *, close_immediately_times: int = 0, stream_forever: bool = False) -> None:
        self.payload = payload
        self.close_immediately_times = close_immediately_times
        self.stream_forever = stream_forever
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(5)
        self.port = self.srv.getsockname()[1]
        self.connections = 0
        self._stop = False
        self.t = threading.Thread(target=self._serve, daemon=True)
        self.t.start()

    def _serve(self) -> None:
        try:
            self.srv.settimeout(0.2)
        except OSError:
            return
        while not self._stop:
            try:
                c, _ = self.srv.accept()
            except (socket.timeout, OSError):
                continue
            self.connections += 1
            if self.connections <= self.close_immediately_times:
                c.close()  # adb aceitou a conexão local, mas o app ainda não abriu o socket
                continue
            try:
                c.sendall(self.payload)
                while self.stream_forever and not self._stop:
                    c.sendall(b"\x00" * 1920 if not self.payload else self.payload[:1920])
                    time.sleep(0.01)
                time.sleep(0.2)
            except OSError:
                pass
            finally:
                c.close()

    def close(self) -> None:
        self._stop = True
        self.srv.close()


class FakeAdb:
    """Substitui run_hidden: registra os comandos e responde como o adb."""

    def __init__(self, *, install_ok: bool = True, installed: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.install_ok = install_ok
        self.installed = installed

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        joined = " ".join(cmd)
        if "dumpsys package" in joined:
            out = (
                "versionName=1.1\n android.permission.RECORD_AUDIO: granted=true, flags=[]\n"
                if self.installed
                else ""
            )
            return subprocess.CompletedProcess(cmd, 0, out, "")
        if " install " in f" {joined} ":
            out = "Performing Streamed Install\nSuccess\n" if self.install_ok else "Failure [INSTALL_FAILED_OLDER_SDK]\n"
            return subprocess.CompletedProcess(cmd, 0 if self.install_ok else 1, out, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def has(self, *parts: str) -> bool:
        return any(all(p in " ".join(c) for p in parts) for c in self.calls)


class TestApkIntegrity(unittest.TestCase):
    def test_bundled_apk_matches_pinned_hash(self) -> None:
        apk = resolve_sndcpy_apk()
        if apk is None:
            self.skipTest("rode python build_exe.py (ou build_exe._ensure_sndcpy) para baixar o APK")
        self.assertEqual(sndcpy_audio.sha256_file(apk), SNDCPY_APK_SHA256)

    def test_tampered_apk_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "sndcpy"
            d.mkdir()
            (d / "sndcpy.apk").write_bytes(b"APK ADULTERADO")
            with mock.patch.object(sndcpy_audio, "resource_path", return_value=d), \
                    mock.patch.object(sndcpy_audio, "package_dir", return_value=Path(tmp) / "nada"):
                self.assertIsNone(resolve_sndcpy_apk())


class TestPcmHelpers(unittest.TestCase):
    def test_wav_conversion_and_peak(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pcm = Path(tmp) / "a.pcm"
            pcm.write_bytes(_tone(1.0, 0.5) + b"\x01")  # byte solto no fim é descartado
            wav = Path(tmp) / "a.wav"
            n = pcm_to_wav(pcm, wav)
            self.assertEqual(n, 48000 * 4)
            with wave.open(str(wav)) as w:
                self.assertEqual((w.getnchannels(), w.getframerate(), w.getsampwidth()), (2, 48000, 2))
                self.assertEqual(w.getnframes(), 48000)
            self.assertAlmostEqual(pcm_peak_dbfs(pcm), -6.0, delta=0.3)

    def test_silence_and_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pcm = Path(tmp) / "s.pcm"
            pcm.write_bytes(b"\x00" * 4000)
            self.assertEqual(pcm_peak_dbfs(pcm), -math.inf)
            pcm.write_bytes(b"")
            self.assertIsNone(pcm_peak_dbfs(pcm))


class TestCapture(unittest.TestCase):
    def setUp(self) -> None:
        apk = Path(tempfile.gettempdir()) / "fake_sndcpy.apk"
        apk.write_bytes(b"x")
        self.apk_patch = mock.patch.object(sndcpy_audio, "resolve_sndcpy_apk", return_value=apk)
        self.apk_patch.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.pcm = Path(self.tmp.name) / "cap.pcm"

    def tearDown(self) -> None:
        self.apk_patch.stop()
        self.tmp.cleanup()

    def _capture(self, server: FakeDeviceServer, fake: FakeAdb, stop_after: float = 0.6, **kw):
        t0 = time.monotonic()
        cap = SndcpyCapture(
            "adb", "S1", self.pcm,
            should_stop=lambda: time.monotonic() - t0 > stop_after,
            status=lambda m: None,
            **kw,
        )
        with mock.patch.object(sndcpy_audio, "run_hidden", fake), \
                mock.patch.object(sndcpy_audio, "free_port", return_value=server.port):
            return cap.run()

    def test_captures_pcm_and_cleans_up(self) -> None:
        server = FakeDeviceServer(_tone(1.0), stream_forever=False)
        fake = FakeAdb()
        try:
            res = self._capture(server, fake, stop_after=0.8)
        finally:
            server.close()
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(res.bytes_written, 48000 * 4)
        self.assertEqual(self.pcm.stat().st_size, 48000 * 4)
        # fluxo esperado no aparelho
        self.assertTrue(fake.has("install", "-t", "-r", "-g"))
        self.assertTrue(fake.has("appops", "set", "com.rom1v.sndcpy", "PROJECT_MEDIA", "allow"))
        self.assertTrue(fake.has("forward", f"tcp:{server.port}", "localabstract:sndcpy"))
        self.assertTrue(fake.has("am", "start", "com.rom1v.sndcpy/.MainActivity"))
        # limpeza
        self.assertTrue(fake.has("force-stop", "com.rom1v.sndcpy"))
        self.assertTrue(fake.has("forward", "--remove", f"tcp:{server.port}"))

    def test_skips_install_when_right_version_already_there(self) -> None:
        server = FakeDeviceServer(_tone(0.5))
        fake = FakeAdb(installed=True)
        try:
            res = self._capture(server, fake, stop_after=0.6)
        finally:
            server.close()
        self.assertTrue(res.ok, res.reason)
        self.assertFalse(fake.has(" install "))
        self.assertTrue(fake.has("appops", "PROJECT_MEDIA", "allow"))  # a liberação é sempre reaplicada

    def test_installs_when_missing_or_permission_not_granted(self) -> None:
        server = FakeDeviceServer(_tone(0.5))
        fake = FakeAdb(installed=False)
        try:
            self._capture(server, fake, stop_after=0.6)
        finally:
            server.close()
        self.assertTrue(fake.has("install", "-t", "-r", "-g"))

    def test_retries_until_app_opens_socket(self) -> None:
        server = FakeDeviceServer(_tone(0.5), close_immediately_times=2)
        try:
            res = self._capture(server, FakeAdb(), stop_after=3.0, connect_deadline_s=6.0)
        finally:
            server.close()
        self.assertTrue(res.ok, res.reason)
        self.assertGreaterEqual(server.connections, 3)

    def test_no_data_reports_dialog_hint(self) -> None:
        server = FakeDeviceServer(b"", close_immediately_times=999)
        try:
            res = self._capture(server, FakeAdb(), stop_after=30, connect_deadline_s=1.5)
        finally:
            server.close()
        self.assertFalse(res.ok)
        self.assertIn("Começar agora", res.reason)

    def test_install_failure_is_reported_and_cleaned(self) -> None:
        server = FakeDeviceServer(b"")
        fake = FakeAdb(install_ok=False)
        try:
            res = self._capture(server, fake)
        finally:
            server.close()
        self.assertFalse(res.ok)
        self.assertIn("instalar", res.reason)
        self.assertFalse(fake.has("appops"))  # não segue adiante sem o app

    def test_missing_apk(self) -> None:
        self.apk_patch.stop()
        with mock.patch.object(sndcpy_audio, "resolve_sndcpy_apk", return_value=None):
            cap = SndcpyCapture("adb", "S1", self.pcm, should_stop=lambda: False, status=lambda m: None)
            res = cap.run()
        self.apk_patch.start()
        self.assertFalse(res.ok)
        self.assertIn("ausente", res.reason)

    def test_stop_during_connect_returns_quickly(self) -> None:
        server = FakeDeviceServer(b"", close_immediately_times=999)
        t0 = time.monotonic()
        try:
            res = self._capture(server, FakeAdb(), stop_after=0.3, connect_deadline_s=30.0)
        finally:
            server.close()
        self.assertFalse(res.ok)
        self.assertLess(time.monotonic() - t0, 5.0)


class TestThreadIntegration(unittest.TestCase):
    """DeviceAudioRecordThread no Android 10 deve usar o sndcpy e entregar um WAV válido."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "rec.wav"
        apk = Path(self.tmp.name) / "x.apk"
        apk.write_bytes(b"x")
        self.patches = [
            mock.patch.object(sndcpy_audio, "resolve_sndcpy_apk", return_value=apk),
            mock.patch.object(device_audio, "_device_sdk", return_value=29),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _run_thread(self, payload: bytes):
        server = FakeDeviceServer(payload)
        thr = device_audio.DeviceAudioRecordThread("adb", "S1", self.out)
        msgs: list[str] = []
        done: list[str] = []
        fail: list[str] = []
        thr.status.connect(msgs.append)
        thr.finished.connect(done.append)
        thr.failed.connect(fail.append)
        threading.Timer(0.8, thr.stop).start()
        try:
            with mock.patch.object(sndcpy_audio, "run_hidden", FakeAdb()), \
                    mock.patch.object(sndcpy_audio, "free_port", return_value=server.port):
                thr.run()
        finally:
            server.close()
        QCoreApplication.processEvents()
        return thr, msgs, done, fail

    def test_android10_uses_sndcpy_and_writes_wav(self) -> None:
        thr, msgs, done, fail = self._run_thread(_tone(1.0))
        self.assertEqual(fail, [])
        self.assertEqual(done, [str(self.out)])
        with wave.open(str(self.out)) as w:
            self.assertEqual(w.getframerate(), 48000)
            self.assertGreaterEqual(w.getnframes(), 40000)
        self.assertIsNotNone(thr.audio_started_at)
        self.assertTrue(any("SDK 29" in m for m in msgs))
        self.assertTrue(any("pico" in m for m in msgs))
        self.assertFalse(self.out.with_suffix(".pcm").exists())

    def test_silent_playback_is_flagged(self) -> None:
        _thr, msgs, done, _fail = self._run_thread(b"\x00" * 4 * 48000)
        self.assertEqual(done, [str(self.out)])
        self.assertTrue(any("silêncio" in m for m in msgs))

    def test_falls_back_with_clear_message_when_sndcpy_fails(self) -> None:
        thr = device_audio.DeviceAudioRecordThread("adb", "S1", self.out)
        fail: list[str] = []
        thr.failed.connect(fail.append)
        with mock.patch.object(sndcpy_audio, "run_hidden", FakeAdb(install_ok=False)), \
                mock.patch.object(thr, "_capture_with_tinycap", return_value="O Mini PC não tem tinycap."):
            thr.run()
        QCoreApplication.processEvents()
        self.assertEqual(len(fail), 1)
        self.assertIn("tinycap", fail[0])
        self.assertIn("Aplicativo auxiliar", fail[0])


class TestVideoWaitsForAudio(unittest.TestCase):
    """O vídeo só começa quando o áudio está entrando: sem perder os primeiros segundos de som."""

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls._qapp = QApplication.instance() or QApplication(sys.argv)
        from aibox.main_window import MainWindow

        cls.w = MainWindow()

    def _prepare(self):
        from PySide6.QtCore import QObject, Signal

        class FakeVideo(QObject):
            status = Signal(str)
            finished = Signal(str)
            failed = Signal(str)

            def __init__(self, *a, **k) -> None:
                super().__init__()
                self.started_at = 0.0
                self.started = False

            def start(self) -> None:
                self.started = True

            def stop(self) -> None:
                pass

            def wait(self, *a) -> bool:
                return True

            def isRunning(self) -> bool:
                return False

        class FakeAudio(FakeVideo):
            ready = Signal()

            def __init__(self, *a, **k) -> None:
                super().__init__()
                self.audio_started_at = None

        w = self.w
        w.recording_state = "idle"
        w.record_thread = None
        w.audio_thread = None
        w._video_pending = False
        w.adb = mock.Mock()
        w.adb.adb_path = "adb"
        w._require_serial = lambda: "S1"  # type: ignore[method-assign]
        w.txt_out_gravar.setText(tempfile.gettempdir())
        w.chk_record_audio.setChecked(True)
        return FakeVideo, FakeAudio

    def test_video_waits_for_ready(self) -> None:
        FakeVideo, FakeAudio = self._prepare()
        w = self.w
        with mock.patch("aibox.main_window.ScreenRecordThread", FakeVideo),                 mock.patch("aibox.main_window.DeviceAudioRecordThread", FakeAudio),                 mock.patch.object(w, "_start_recording_ui"):
            w.start_recording()
            self.assertTrue(w.audio_thread.started)
            self.assertFalse(w.record_thread.started)  # ainda esperando o áudio
            self.assertEqual(w.recording_state, "starting")
            w.audio_thread.ready.emit()
            self.assertTrue(w.record_thread.started)
            self.assertEqual(w.recording_state, "recording")
            w.audio_thread.ready.emit()  # duplicado não reinicia
            w.record_thread = None
            w.audio_thread = None
            w.recording_state = "idle"

    def test_audio_failure_starts_video_anyway(self) -> None:
        FakeVideo, FakeAudio = self._prepare()
        w = self.w
        with mock.patch("aibox.main_window.ScreenRecordThread", FakeVideo),                 mock.patch("aibox.main_window.DeviceAudioRecordThread", FakeAudio),                 mock.patch.object(w, "_start_recording_ui"):
            w.start_recording()
            w.audio_thread.failed.emit("sem áudio")
            self.assertTrue(w.record_thread.started)
            self.assertIn("sem o áudio", w.txt_gravar_out.toPlainText())
            w.record_thread = None
            w.audio_thread = None
            w.recording_state = "idle"

    def test_without_audio_video_starts_immediately(self) -> None:
        FakeVideo, FakeAudio = self._prepare()
        w = self.w
        w.chk_record_audio.setChecked(False)
        with mock.patch("aibox.main_window.ScreenRecordThread", FakeVideo),                 mock.patch("aibox.main_window.DeviceAudioRecordThread", FakeAudio),                 mock.patch.object(w, "_start_recording_ui"):
            w.start_recording()
            self.assertTrue(w.record_thread.started)
            self.assertIsNone(w.audio_thread)
            w.record_thread = None
            w.recording_state = "idle"
        w.chk_record_audio.setChecked(True)

    def test_fallback_timer_starts_video_once(self) -> None:
        FakeVideo, FakeAudio = self._prepare()
        w = self.w
        with mock.patch("aibox.main_window.ScreenRecordThread", FakeVideo),                 mock.patch("aibox.main_window.DeviceAudioRecordThread", FakeAudio),                 mock.patch.object(w, "_start_recording_ui"):
            w.start_recording()
            w._begin_video_recording()  # prazo de segurança
            started_at = w.record_thread.started_at
            w._begin_video_recording()  # segunda chamada é ignorada
            self.assertEqual(w.record_thread.started_at, started_at)
            w.record_thread = None
            w.audio_thread = None
            w.recording_state = "idle"


class TestNegativeDelayMux(unittest.TestCase):
    def test_audio_started_before_video_is_trimmed(self) -> None:
        from aibox.recording import _has_audio_stream, _resolve_ffmpeg, mux_audio_into_mp4

        ffmpeg = _resolve_ffmpeg()
        if not ffmpeg:
            self.skipTest("ffmpeg indisponível")
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            v, a, o = d / "v.mp4", d / "a.wav", d / "o.mp4"
            subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
                            "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(v)], check=True)
            subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                            "-t", "5", "-ac", "2", str(a)], check=True)
            for delay in (-1.5, 0.0, 1.0):
                ok, err = mux_audio_into_mp4(ffmpeg, v, a, o, audio_delay_s=delay)
                self.assertTrue(ok, f"{delay}: {err}")
                self.assertTrue(_has_audio_stream(o))
                dur = subprocess.run([ffmpeg, "-hide_banner", "-i", str(o)], capture_output=True, text=True).stderr
                self.assertIn("Duration: 00:00:03", dur)


if __name__ == "__main__":
    unittest.main(verbosity=2)
