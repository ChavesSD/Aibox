from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from aibox.adb import Adb, AdbDevice, AdbError, find_adb
from aibox.main_window import MainWindow
from aibox.workers import Background


class TestRemainingFeatures(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_find_adb_and_version(self) -> None:
        path = find_adb()
        self.assertTrue(Path(path).exists())
        adb = Adb(path)
        ver = adb.version()
        self.assertIn("Android Debug Bridge", ver)
        devices = adb.list_devices()
        self.assertIsInstance(devices, list)

    def test_connect_detects_failure_message(self) -> None:
        adb = Adb(find_adb())
        fake = mock.Mock()
        fake.returncode = 0
        fake.stdout = "failed to connect to 10.0.0.1:5555\n"
        fake.stderr = ""
        with mock.patch.object(adb, "_run", return_value=fake):
            with self.assertRaises(AdbError):
                adb.connect("10.0.0.1:5555")

    def test_install_detects_failure_message(self) -> None:
        adb = Adb(find_adb())
        fake = mock.Mock()
        fake.returncode = 0
        fake.stdout = "Failure [INSTALL_FAILED_INVALID_APK]\n"
        fake.stderr = ""
        with tempfile.NamedTemporaryFile(suffix=".apk", delete=False) as tf:
            apk = Path(tf.name)
        try:
            with mock.patch.object(adb, "_run", return_value=fake):
                with self.assertRaises(AdbError):
                    adb.install_apk("serial", apk)
        finally:
            apk.unlink(missing_ok=True)

    def test_main_window_smoke_navigation(self) -> None:
        with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.Ok):
            with mock.patch.object(QMessageBox, "critical", return_value=QMessageBox.Ok):
                w = MainWindow()
                try:
                    self.assertEqual(len(w.nav_buttons), 10)
                    self.assertEqual(w.pages.count(), 10)
                    expected = [
                        "Conectar",
                        "DPI",
                        "Instalar",
                        "Desinstalar",
                        "Debug",
                        "Otimizar",
                        "Restauração",
                        "Gravar",
                        "Print",
                        "Sobre",
                    ]
                    for i, name in enumerate(expected):
                        self.assertEqual(w.nav_buttons[i].property("fullText"), name)

                    for idx in range(10):
                        w._nav_jump(idx)
                        self.assertEqual(w.pages.currentIndex(), idx)

                    w._apply_sidebar_collapsed(True)
                    self.assertEqual(w.sidebar.width(), 64)
                    w._apply_sidebar_collapsed(False)
                    self.assertEqual(w.sidebar.width(), 196)

                    out = w._ensure_output_dir()
                    self.assertTrue(out.exists())

                    # Sem dispositivo: ações devem avisar e não crashar
                    self.assertIsNone(w._require_serial())
                    w.take_screenshot()
                    w.start_recording()
                    w.install_apk()
                    w.start_factory_reset()
                    w.start_logcat()

                    # Estado de gravação permanece idle sem device
                    self.assertEqual(w.recording_state, "idle")
                    self.assertIsNone(w.record_thread)
                    self.assertIsNone(w.logcat_thread)
                finally:
                    w.close()

    def test_background_worker(self) -> None:
        bg = Background()
        done: list[object] = []
        err: list[str] = []

        def ok(v: object) -> None:
            done.append(v)

        def on_err(m: str) -> None:
            err.append(m)

        bg.run(lambda: 42, ok, on_err)
        # Processa eventos do Qt até o worker concluir
        for _ in range(200):
            self.app.processEvents()
            if done or err:
                break
            QApplication.processEvents()
            import time

            time.sleep(0.01)
        self.assertEqual(done, [42])
        self.assertEqual(err, [])

        bg.run(lambda: (_ for _ in ()).throw(RuntimeError("boom")), ok, on_err)
        for _ in range(200):
            self.app.processEvents()
            if err:
                break
            import time

            time.sleep(0.01)
        self.assertTrue(any("boom" in e for e in err))

    def test_require_serial_rejects_offline(self) -> None:
        with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.Ok):
            w = MainWindow()
            try:
                w.devices = [AdbDevice(serial="ABC", state="offline", model="Pixel")]
                w.cmb_device.clear()
                w.cmb_device.addItem(w.devices[0].label, w.devices[0].serial)
                self.assertIsNone(w._require_serial())

                w.devices = [AdbDevice(serial="ABC", state="device", model="Pixel")]
                w.cmb_device.clear()
                w.cmb_device.addItem(w.devices[0].label, w.devices[0].serial)
                self.assertEqual(w._require_serial(), "ABC")
            finally:
                w.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
