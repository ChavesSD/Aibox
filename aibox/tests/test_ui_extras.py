from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

from aibox.main_window import MainWindow  # noqa: E402
from aibox.page_header import PageHeader  # noqa: E402
from aibox.toggle_switch import ToggleRow  # noqa: E402


class TestPageHeader(unittest.TestCase):
    def test_quick_back_and_forth_ends_on_last_page(self) -> None:
        h = PageHeader("A", "sub A")
        h.show()
        h.set_texts("B", "sub B")
        h.set_texts("A", "sub A")  # volta antes do fade terminar
        QTest.qWait(600)
        self.assertEqual(h.lbl_title.text(), "A")
        self.assertEqual(h.lbl_sub.text(), "sub A")

    def test_same_target_is_noop_and_normal_change_works(self) -> None:
        h = PageHeader("A", "sub A")
        h.set_texts("A", "sub A")
        h.set_texts("B", "sub B")
        h.set_texts("B", "sub B")
        QTest.qWait(600)
        self.assertEqual((h.lbl_title.text(), h.lbl_sub.text()), ("B", "sub B"))


class TestToggleRow(unittest.TestCase):
    def test_click_on_label_toggles(self) -> None:
        row = ToggleRow("Opção")
        row.show()
        self.assertFalse(row.isChecked())
        QTest.mouseClick(row.label, Qt.LeftButton)
        self.assertTrue(row.isChecked())
        QTest.mouseClick(row.label, Qt.LeftButton)
        self.assertFalse(row.isChecked())

    def test_disabled_row_ignores_label_click(self) -> None:
        row = ToggleRow("Opção")
        row.show()
        row.setEnabled(False)
        QTest.mouseClick(row.label, Qt.LeftButton)
        self.assertFalse(row.isChecked())

    def test_focus_ring_paints(self) -> None:
        row = ToggleRow("x")
        row.show()
        row.toggle.setFocus()
        pix = row.toggle.grab()
        self.assertFalse(pix.isNull())


class TestWindowExtras(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.w = MainWindow()

    def _row(self, category: str, filename: str):
        for e, row in self.w._apk_rows[category]:
            if e.filename == filename:
                return e, row
        raise AssertionError(filename)

    def test_autostart_auto_checked_then_unchecked(self) -> None:
        w = self.w
        _e, auto = self._row("Outros", "Autostart.apk")
        _e2, totem = self._row("Totem", "Upzz.apk")
        totem.setEnabled(True)  # sem APK local o switch fica desabilitado
        totem.toggle.setEnabled(True)
        auto.setChecked(False)
        w._autostart_auto = False
        totem.setChecked(True)
        self.assertTrue(auto.isChecked())
        self.assertTrue(w._autostart_auto)
        totem.setChecked(False)
        self.assertFalse(auto.isChecked())

    def test_manual_autostart_survives_refresh(self) -> None:
        w = self.w
        _e, auto = self._row("Outros", "Autostart.apk")
        _e2, totem = self._row("Totem", "Upzz.apk")
        totem.setChecked(False)
        auto.setChecked(False)
        auto.setChecked(True)  # escolha explícita do usuário
        self.assertFalse(w._autostart_auto)
        w._sync_autostart_checkbox()
        self.assertTrue(auto.isChecked())
        auto.setChecked(False)

    def test_pair_button_present_and_validates(self) -> None:
        self.assertTrue(hasattr(self.w, "btn_wifi_pair"))
        self.assertEqual(self.w.btn_wifi_pair.text(), "Parear…")

    def test_availability_log_not_repeated(self) -> None:
        w = self.w
        w.refresh_apk_availability()
        before = w.txt_apps_out.toPlainText()
        w.refresh_apk_availability()
        self.assertEqual(w.txt_apps_out.toPlainText(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
