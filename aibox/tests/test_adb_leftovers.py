from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aibox import adb as adbmod
from aibox.adb import Adb, AdbError


def _fake() -> Adb:
    a = Adb.__new__(Adb)
    a.adb_path = "adb"
    a._adb_dir = "."
    a._display_blanked = False
    a.restart_guard = None
    return a


class TestScreencap(unittest.TestCase):
    def test_timeout_raises_and_leaves_no_tmp(self) -> None:
        a = _fake()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "p.png"
            with mock.patch.object(adbmod.subprocess, "run", side_effect=subprocess.TimeoutExpired("adb", 1)):
                with self.assertRaises(AdbError):
                    a.screenshot_png("S1", out)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_success_writes_file(self) -> None:
        a = _fake()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "p.png"
            cp = subprocess.CompletedProcess(["adb"], 0, b"\x89PNG-dados", b"")
            with mock.patch.object(adbmod.subprocess, "run", return_value=cp):
                a.screenshot_png("S1", out)
            self.assertEqual(out.read_bytes(), b"\x89PNG-dados")
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["p.png"])

    def test_empty_and_error_and_limit(self) -> None:
        a = _fake()
        with mock.patch.object(adbmod.subprocess, "run", return_value=subprocess.CompletedProcess(["adb"], 0, b"", b"")):
            with self.assertRaises(AdbError):
                a.screencap_png_bytes("S1")
        with mock.patch.object(adbmod.subprocess, "run", return_value=subprocess.CompletedProcess(["adb"], 1, b"", b"device offline")):
            with self.assertRaisesRegex(AdbError, "offline"):
                a.screencap_png_bytes("S1")
        with mock.patch.object(adbmod.subprocess, "run", return_value=subprocess.CompletedProcess(["adb"], 0, b"x" * 40, b"")):
            with self.assertRaises(AdbError):
                a._screencap_bytes("S1", timeout_s=1, max_bytes=10)


class TestOptimizeResilience(unittest.TestCase):
    def test_dexopt_timeout_stops_chain(self) -> None:
        a = _fake()
        calls: list[str] = []

        def lax(serial, cmd, timeout_s=60):
            calls.append(cmd)
            raise AdbError("Timeout executando: adb shell " + cmd)

        a.shell_lax = lax  # type: ignore[method-assign]
        ok, msg = a._optimize_dexopt("S1")
        self.assertFalse(ok)
        self.assertEqual(len(calls), 1)  # não encadeia outro dexopt em paralelo
        self.assertIn("excedeu", msg)

    def test_optimize_keeps_report_when_steps_fail(self) -> None:
        a = _fake()
        a.shell_lax = mock.Mock(side_effect=AdbError("device offline"))  # type: ignore[method-assign]
        with mock.patch("aibox.adb.time.sleep"):
            report = a.optimize_device("S1")
        self.assertIn("Resultado", report)
        self.assertIn("falha", report.lower())


class TestTtsInstall(unittest.TestCase):
    def test_already_exists_does_not_uninstall(self) -> None:
        a = _fake()
        a.install_apk = mock.Mock(side_effect=AdbError("Failure [INSTALL_FAILED_ALREADY_EXISTS]"))  # type: ignore[method-assign]
        a.shell_try = mock.Mock()  # type: ignore[method-assign]
        a._run = mock.Mock()  # type: ignore[method-assign]
        with self.assertRaises(AdbError):
            a.install_tts_apk("S1", Path("x.apk"))
        a.shell_try.assert_not_called()
        a._run.assert_not_called()

    def test_signature_mismatch_reinstalls(self) -> None:
        a = _fake()
        a.install_apk = mock.Mock(  # type: ignore[method-assign]
            side_effect=[AdbError("INSTALL_FAILED_UPDATE_INCOMPATIBLE"), "Success"]
        )
        a.shell_try = mock.Mock(return_value="")  # type: ignore[method-assign]
        a._run = mock.Mock()  # type: ignore[method-assign]
        a._resolve_tts_engine_package = mock.Mock(return_value="com.google.android.tts")  # type: ignore[method-assign]
        msg = a.install_tts_apk("S1", Path("x.apk"))
        self.assertIn("motor=com.google.android.tts", msg)
        self.assertEqual(a.install_apk.call_count, 2)


class TestDisplayRestore(unittest.TestCase):
    def test_restores_original_stay_on(self) -> None:
        a = _fake()
        sent: list[str] = []
        a.shell_try = lambda s, cmd, timeout_s=0: sent.append(cmd) or ""  # type: ignore[method-assign]
        a._tts_restore_display("S1", {"stay_on": "2", "system:screen_brightness": "90"})
        self.assertIn("stay_on_while_plugged_in 2", sent[0])
        self.assertNotIn("svc power stayon false", sent[0])

    def test_unknown_stay_on_falls_back(self) -> None:
        a = _fake()
        sent: list[str] = []
        a.shell_try = lambda s, cmd, timeout_s=0: sent.append(cmd) or ""  # type: ignore[method-assign]
        a._tts_restore_display("S1", {})
        self.assertIn("svc power stayon false", sent[0])

    def test_untrusted_values_never_reach_shell(self) -> None:
        a = _fake()
        sent: list[str] = []
        a.shell_try = lambda s, cmd, timeout_s=0: sent.append(cmd) or ""  # type: ignore[method-assign]
        a._tts_restore_display(
            "S1",
            {
                "stay_on": "7; reboot",
                "system:screen_brightness": "1; rm -rf /",
                "fb:/sys/class/graphics/fb0/blank": "0",
                "fb:/data/evil; reboot": "0",
                "bl:/sys/x/brightness": "5 && reboot",
            },
        )
        cmd = sent[0]
        self.assertNotIn("reboot", cmd)
        self.assertNotIn("rm -rf", cmd)
        self.assertIn("echo 0 > /sys/class/graphics/fb0/blank", cmd)


class TestAdbLookup(unittest.TestCase):
    def test_frozen_ignores_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as cwd_dir, tempfile.TemporaryDirectory() as exe_dir,                 mock.patch.object(adbmod, "is_frozen", return_value=True),                 mock.patch.object(adbmod, "executable_dir", return_value=Path(exe_dir)),                 mock.patch("aibox.adb.Path.cwd", return_value=Path(cwd_dir)):
            cands = [str(c) for c in adbmod._default_adb_candidates()]
        self.assertFalse(any(cwd_dir.lower() in c.lower() for c in cands))
        self.assertTrue(any(exe_dir.lower() in c.lower() for c in cands))

    def test_usb_ini_with_bom(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "adb_usb.ini").write_bytes(b"\xef\xbb\xbf0x18d1\n")
            with mock.patch.object(adbmod, "android_user_home", return_value=home):
                adbmod.ensure_adb_usb_ini()
            text = (home / "adb_usb.ini").read_text(encoding="utf-8")
            self.assertNotIn("﻿", text)
            self.assertIn("0x18d1", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
