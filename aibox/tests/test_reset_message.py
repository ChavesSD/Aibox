from __future__ import annotations

import os
import sys
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

from aibox.adb import AdbDevice  # noqa: E402
from aibox.main_window import MainWindow  # noqa: E402

ADDR = "10.0.0.76:5555"


class TestMonitorAfterReset(unittest.TestCase):
    """Depois de um reset o aparelho some: o monitor não pode mostrar o erro técnico de reconexão."""

    def _window(self, *, device_present: bool = False):
        w = MainWindow()
        w.bg.run = lambda fn, ok, err: ok(fn())  # type: ignore[method-assign]  # síncrono nos testes
        adb = mock.Mock()
        adb.list_devices.return_value = [AdbDevice(ADDR, "device", "x")] if device_present else []
        adb.connect.side_effect = Exception(
            "cannot connect to 10.0.0.76:5555: Uma tentativa de conexão falhou (10060)"
        )
        adb.disconnect.return_value = ""
        w.adb = adb
        w.wifi_state = "connected"
        w.wifi_connected_address = ADDR
        w.wifi_reconnect_inflight = False
        w.wifi_reconnect_failures = 0
        errors: list[str] = []
        w._err_dialog = errors.append  # type: ignore[method-assign]
        return w, adb, errors

    def test_after_reset_shows_friendly_info_once_and_never_the_raw_error(self) -> None:
        w, adb, errors = self._window()
        w._reset_requested_at = time.monotonic()
        w._reset_notified = False
        with mock.patch.object(QMessageBox, "information") as info:
            w._wifi_monitor_tick()
            self.assertEqual(info.call_count, 1)
            text = info.call_args[0][2]
            self.assertIn("restauração de fábrica foi iniciada", text)
            self.assertIn("Wi‑Fi", text)
            self.assertIn("reconecte", text)
            w._notify_reset_in_progress(ADDR)  # idempotente
            self.assertEqual(info.call_count, 1)
        self.assertEqual(errors, [])  # nada de "cannot connect to…"
        adb.connect.assert_not_called()  # não tenta reconectar um aparelho que está sendo apagado
        self.assertEqual(w.wifi_state, "disconnected")
        self.assertIsNone(w.wifi_connected_address)
        self.assertIn("restauração de fábrica", w.txt_connect_out.toPlainText() + w.txt_reset_out.toPlainText())

    def test_reset_pending_but_device_still_present_does_nothing(self) -> None:
        w, adb, errors = self._window(device_present=True)
        w._reset_requested_at = time.monotonic()
        with mock.patch.object(QMessageBox, "information") as info:
            w._wifi_monitor_tick()
        info.assert_not_called()
        self.assertEqual(errors, [])
        self.assertEqual(w.wifi_state, "connected")

    def test_without_reset_three_failures_give_friendly_but_detailed_message(self) -> None:
        w, adb, errors = self._window()
        w._reset_requested_at = None
        for _ in range(3):
            w.wifi_state = "connected"
            w.wifi_connected_address = ADDR
            w._wifi_monitor_tick()
        self.assertEqual(len(errors), 1)
        msg = errors[0]
        self.assertIn("perdeu a conexão", msg)
        self.assertIn(ADDR, msg)
        self.assertIn("ligado", msg)
        self.assertIn("Detalhe técnico", msg)
        self.assertIn("cannot connect", msg)  # o detalhe técnico continua disponível, mas por último
        self.assertLess(msg.index("perdeu a conexão"), msg.index("cannot connect"))
        self.assertEqual(adb.connect.call_count, 3)

    def test_expired_reset_window_behaves_like_normal_loss(self) -> None:
        w, adb, errors = self._window()
        w._reset_requested_at = time.monotonic() - 5000  # passou da janela de tolerância
        self.assertFalse(w._reset_recent())
        for _ in range(3):
            w.wifi_state = "connected"
            w.wifi_connected_address = ADDR
            w._wifi_monitor_tick()
        self.assertEqual(len(errors), 1)

    def test_reset_recent_window(self) -> None:
        w, _adb, _e = self._window()
        w._reset_requested_at = None
        self.assertFalse(w._reset_recent())
        w._reset_requested_at = time.monotonic()
        self.assertTrue(w._reset_recent())


if __name__ == "__main__":
    unittest.main(verbosity=2)
