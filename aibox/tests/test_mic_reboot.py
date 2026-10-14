from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

from aibox.adb import AdbDevice  # noqa: E402
from aibox.reboot_validate import wait_and_validate  # noqa: E402

SERIAL = "10.0.0.76:5555"


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class FakeAdb:
    def __init__(self, *, offline_after_reboot=True, boots_after=10.0, focus="mCurrentFocus=Window{x u0 com.app/.Main}") -> None:
        self.clk = FakeClock()
        self.rebooted_at: float | None = None
        self.offline_after_reboot = offline_after_reboot
        self.boots_after = boots_after
        self.focus = focus
        self.connects = 0

    def reboot(self, serial):
        self.rebooted_at = self.clk.t

    def list_devices(self):
        if self.rebooted_at is None or not self.offline_after_reboot:
            return [AdbDevice(SERIAL, "device", "x")]
        el = self.clk.t - self.rebooted_at
        if 4 <= el < self.boots_after:
            return []
        return [AdbDevice(SERIAL, "device", "x")] if el >= self.boots_after else [AdbDevice(SERIAL, "device", "x")]

    def connect(self, addr):
        self.connects += 1

    def shell_try(self, serial, cmd, timeout_s=4):
        if "boot_completed" in cmd:
            return "1\n"
        if "dumpsys window" in cmd:
            return self.focus
        if "pidof" in cmd:
            return ""
        return ""


def _run(adb, pkgs=("com.app",), **kw):
    msgs: list[str] = []
    res = wait_and_validate(
        adb, SERIAL, SERIAL, list(pkgs), msgs.append, sleep=adb.clk.sleep, clock=adb.clk.now, **kw
    )
    return res, msgs


class TestRebootValidate(unittest.TestCase):
    def test_ok_when_app_in_focus(self) -> None:
        res, msgs = _run(FakeAdb())
        self.assertTrue(res.ok, res.message)
        self.assertIn("Autostart validado", res.message)

    def test_fails_when_app_never_opens(self) -> None:
        res, _ = _run(FakeAdb(focus="mCurrentFocus=Window{x u0 com.android.launcher/.Home}"), focus_timeout=20)
        self.assertFalse(res.ok)
        self.assertEqual(res.stage, "autostart")

    def test_device_never_reboots(self) -> None:
        res, _ = _run(FakeAdb(offline_after_reboot=False), offline_timeout=10)
        self.assertFalse(res.ok)
        self.assertEqual(res.stage, "offline")

    def test_never_returns(self) -> None:
        res, _ = _run(FakeAdb(boots_after=10**6), online_timeout=30)
        self.assertFalse(res.ok)
        self.assertEqual(res.stage, "online")
        self.assertIn("depuração", res.message)

    def test_reconnects_wifi_while_waiting(self) -> None:
        adb = FakeAdb()
        _run(adb)
        self.assertGreaterEqual(adb.connects, 1)

    def test_cancel(self) -> None:
        adb = FakeAdb()
        res = wait_and_validate(
            adb, SERIAL, SERIAL, ["com.app"], lambda m: None, should_stop=lambda: True,
            sleep=adb.clk.sleep, clock=adb.clk.now,
        )
        self.assertFalse(res.ok)
        self.assertIn("cancelada", res.message)


class TestMicParserAndMux(unittest.TestCase):
    def test_parser_lists_microphones(self) -> None:
        from aibox import mic_record

        self.assertTrue(callable(mic_record.list_microphones))

    def test_mux_two_tracks_has_single_audio_stream(self) -> None:
        from aibox.recording import _has_audio_stream, _resolve_ffmpeg, mux_audios_into_mp4

        ffmpeg = _resolve_ffmpeg()
        if not ffmpeg:
            self.skipTest("ffmpeg indisponível")
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            v, a1, a2, out = d / "v.mp4", d / "a1.wav", d / "a2.wav", d / "o.mp4"

            def ff(*args):
                subprocess.run([ffmpeg, "-y", "-loglevel", "error", *args], check=True)

            ff("-f", "lavfi", "-i", "testsrc=size=160x120:rate=10", "-t", "3", "-pix_fmt", "yuv420p", str(v))
            ff("-f", "lavfi", "-i", "sine=frequency=440:duration=3", str(a1))
            ff("-f", "lavfi", "-i", "sine=frequency=880:duration=2", str(a2))
            ok, err = mux_audios_into_mp4(ffmpeg, v, [(a1, 0.0), (a2, 0.5)], out)
            self.assertTrue(ok, err)
            self.assertTrue(_has_audio_stream(out))
            # uma faixa só: delega ao mux simples
            out1 = d / "o1.mp4"
            ok, err = mux_audios_into_mp4(ffmpeg, v, [(a1, 0.0)], out1)
            self.assertTrue(ok, err)
            ok, _ = mux_audios_into_mp4(ffmpeg, v, [], d / "o2.mp4")
            self.assertFalse(ok)


class TestUiGating(unittest.TestCase):
    def test_video_waits_for_all_audio_sources(self) -> None:
        from aibox.main_window import MainWindow

        w = MainWindow()
        w._video_pending = True
        w._audio_expected = 2
        w._audio_ready = 0
        with mock.patch.object(w, "_begin_video_recording") as begin:
            w._on_audio_source_ready()
            begin.assert_not_called()
            w._on_audio_source_ready()
            begin.assert_called_once()

    def test_reboot_toggle_default_on_and_busy_flags(self) -> None:
        from aibox.main_window import MainWindow

        w = MainWindow()
        self.assertTrue(w.chk_reboot_after.isChecked())
        self.assertFalse(w.chk_record_mic.isChecked())
        w._reboot_busy = True
        self.assertIn("reinício", w._device_busy())
        self.assertTrue(any("reinício" in o for o in w._active_operations()))

    def test_finish_incomplete_does_not_reboot(self) -> None:
        from aibox.main_window import MainWindow

        w = MainWindow()
        w.adb = mock.Mock()
        w._install_reboot_serial = SERIAL
        w._install_reboot_pkgs = ["com.app"]
        with mock.patch.object(w, "_start_reboot_validation") as st:
            w._finish_install_queue(complete=False)
            st.assert_not_called()
        w._install_reboot_serial = SERIAL
        w._install_reboot_pkgs = ["com.app"]
        with mock.patch.object(w, "_start_reboot_validation") as st:
            w._finish_install_queue(complete=True)
            st.assert_called_once()

    def test_toggle_off_never_reboots(self) -> None:
        from aibox.main_window import MainWindow

        w = MainWindow()
        w.adb = mock.Mock()
        w._install_reboot_serial = None
        w._install_reboot_pkgs = ["com.app"]
        with mock.patch.object(w, "_start_reboot_validation") as st:
            w._finish_install_queue(complete=True)
            st.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
