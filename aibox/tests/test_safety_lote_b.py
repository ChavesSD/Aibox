from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from aibox.adb import AdbDevice  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

from aibox.main_window import MainWindow  # noqa: E402


class TestWindowSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.w = MainWindow()

    def test_wifi_address_validation(self) -> None:
        n = self.w._normalize_wifi_address
        self.assertEqual(n("192.168.0.10"), ("192.168.0.10:5555", None))
        self.assertEqual(n(" 192.168.0.10:5556 ")[0], "192.168.0.10:5556")
        for bad in ("", "5555", "0.0.0.0", "127.0.0.1:5555", "255.255.255.255", "224.0.0.1",
                    "192.168.0.256", "192.168.0.10:0", "192.168.0.10:70000", "192.168.².1",
                    "192.168..10", "a.b.c.d", "192.168.0.10:abc"):
            self.assertIsNone(n(bad)[0], bad)
            self.assertIsNotNone(n(bad)[1], bad)

    def test_unicode_digits_do_not_raise(self) -> None:
        self.assertFalse(self.w._wifi_address_is_valid("١٩٢.١٦٨.٠.١"))

    def test_invalid_output_dir_does_not_lock_recording(self) -> None:
        w = self.w
        w.adb = mock.Mock()
        w._require_serial = lambda: "S1"  # type: ignore[method-assign]
        w.txt_out_gravar.setText("Z:/pasta/inexistente/x")
        with mock.patch.object(QMessageBox, "warning") as warn, mock.patch.object(
            Path, "mkdir", side_effect=OSError("sem acesso")
        ):
            w.start_recording()
        self.assertEqual(w.recording_state, "idle")
        self.assertIsNone(w.record_thread)
        self.assertTrue(warn.called)
        self.assertTrue(w.btn_record_toggle.isEnabled())

    def test_thread_construction_failure_returns_to_idle(self) -> None:
        w = self.w
        w.adb = mock.Mock()
        w.adb.adb_path = "adb"
        w._require_serial = lambda: "S1"  # type: ignore[method-assign]
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            w.txt_out_gravar.setText(d)
            with mock.patch("aibox.main_window.ScreenRecordThread", side_effect=RuntimeError("boom")), \
                    mock.patch.object(w, "_err_dialog") as err:
                w.start_recording()
        self.assertEqual(w.recording_state, "idle")
        self.assertIsNone(w.record_thread)
        self.assertTrue(err.called)

    def test_active_operations_and_close_confirmation(self) -> None:
        w = MainWindow()
        w._install_busy = True
        self.assertIn("instalação de APKs", w._active_operations())
        from PySide6.QtGui import QCloseEvent

        ev = QCloseEvent()
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.No):
            w.closeEvent(ev)
        self.assertFalse(ev.isAccepted())
        w._install_busy = False
        ev2 = QCloseEvent()
        w.closeEvent(ev2)
        self.assertTrue(ev2.isAccepted())

    def test_install_queue_exception_releases_ui(self) -> None:
        w = self.w
        w._install_busy = True
        w.btn_install.setEnabled(False)
        w._install_queue = [object()]  # entrada inválida: o impl vai lançar
        w._install_next_in_queue("S1")
        self.assertFalse(w._install_busy)
        self.assertTrue(w.btn_install.isEnabled())

    def test_uninstall_queue_exception_releases_ui(self) -> None:
        w = self.w
        w._uninstall_busy = True
        w._uninstall_queue = [object()]
        w._uninstall_next("S1")
        self.assertFalse(w._uninstall_busy)
        self.assertTrue(w.btn_uninstall_run.isEnabled())

    def test_logcat_stale_stopped_does_not_clear_new_thread(self) -> None:
        w = self.w
        old, new = object(), object()
        w.logcat_thread = new  # type: ignore[assignment]
        w._on_logcat_stopped(old)
        self.assertIs(w.logcat_thread, new)
        w._on_logcat_stopped(new)
        self.assertIsNone(w.logcat_thread)

    def test_device_busy_blocks_reset(self) -> None:
        w = self.w
        w._optimize_busy = True
        self.assertIsNotNone(w._device_busy())
        w._optimize_busy = False
        self.assertIsNone(w._device_busy())

    def test_quick_action_ignores_double_click(self) -> None:
        w = self.w
        calls: list[int] = []
        btn = w.btn_screenshot
        w._quick_busy.add("print")
        w._quick_action("print", btn, lambda: calls.append(1), lambda _v: None)
        self.assertEqual(calls, [])
        w._quick_busy.discard("print")

    def test_logcat_document_limited(self) -> None:
        self.assertEqual(self.w.txt_logcat.document().maximumBlockCount(), 5000)


class TestWorkersAndLogcat(unittest.TestCase):
    def test_wait_idle_keeps_refs_when_timeout(self) -> None:
        from aibox.workers import Background

        bg = Background()
        sentinel = object()
        bg._active.add(sentinel)  # type: ignore[arg-type]
        with mock.patch.object(bg.pool, "waitForDone", return_value=False):
            bg.wait_idle(1)
        self.assertIn(sentinel, bg._active)
        with mock.patch.object(bg.pool, "waitForDone", return_value=True):
            bg.wait_idle(1)
        self.assertEqual(len(bg._active), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
