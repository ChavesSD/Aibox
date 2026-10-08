from __future__ import annotations

import subprocess
import unittest

from aibox.adb import (
    Adb,
    AdbDevice,
    AdbError,
    is_valid_package_name,
    uninstall_protection,
)


def _fake_adb() -> Adb:
    a = Adb.__new__(Adb)
    a.adb_path = "adb"
    a._adb_dir = "."
    a._display_blanked = False
    a.restart_guard = None
    return a


def _cp(stdout: str = "", stderr: str = "", rc: int = 1) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["adb"], rc, stdout, stderr)


class TestPackageValidation(unittest.TestCase):
    def test_accepts_real_packages(self) -> None:
        for p in ("com.google.android.tts", "com.droidlogic.app.Autostart", "br.com.intelite.totem_2"):
            self.assertTrue(is_valid_package_name(p), p)

    def test_rejects_shell_injection(self) -> None:
        for p in ("a.b; rm -rf /", "com.x$(reboot)", "com.x b", "com.x\nfoo", "com", "", None, "com.x`id`"):
            self.assertFalse(is_valid_package_name(p), repr(p))

    def test_protection_levels(self) -> None:
        self.assertEqual(uninstall_protection("com.android.settings"), "blocked")
        self.assertEqual(uninstall_protection("com.google.android.gms"), "blocked")
        self.assertEqual(uninstall_protection("com.autostart"), "sensitive")
        self.assertEqual(uninstall_protection("br.com.intelite.totem"), "")

    def test_uninstall_blocks_essentials(self) -> None:
        with self.assertRaises(AdbError):
            _fake_adb().uninstall_package("s", "com.android.settings")
        with self.assertRaises(AdbError):
            _fake_adb().uninstall_package("s", "x; reboot")


class TestRetryPolicy(unittest.TestCase):
    def test_ambiguous_error_not_retried_for_shell_or_install(self) -> None:
        a = _fake_adb()
        cp = _cp(stderr="error: connection reset")
        self.assertFalse(a._needs_server_restart(cp, ["shell", "rm -rf x"]))
        self.assertFalse(a._needs_server_restart(cp, ["install", "-r", "a.apk"]))
        self.assertTrue(a._needs_server_restart(cp, ["devices", "-l"]))

    def test_command_never_ran_is_retried(self) -> None:
        a = _fake_adb()
        cp = _cp(stderr="cannot connect to daemon at tcp:5037")
        self.assertTrue(a._needs_server_restart(cp, ["install", "a.apk"]))

    def test_restart_guard_blocks(self) -> None:
        a = _fake_adb()
        a.restart_guard = lambda: True
        self.assertFalse(a._server_restart_allowed())
        a.restart_guard = lambda: False
        self.assertTrue(a._server_restart_allowed())


class TestFactoryResetSafety(unittest.TestCase):
    def test_timeout_is_not_device_gone(self) -> None:
        self.assertFalse(Adb._looks_like_device_gone("timeout executando: adb shell am start"))
        self.assertFalse(Adb._looks_like_device_gone("activity class does not exist / not found"))
        self.assertTrue(Adb._looks_like_device_gone("error: device offline"))

    def test_adb_error_is_not_offline(self) -> None:
        a = _fake_adb()

        def boom() -> list[AdbDevice]:
            raise AdbError("daemon morreu")

        a.list_devices = boom  # type: ignore[method-assign]
        self.assertFalse(a._wait_device_offline("S1", timeout_s=0.8))

    def test_missing_or_offline_is_offline(self) -> None:
        a = _fake_adb()
        a.list_devices = lambda: []  # type: ignore[method-assign]
        self.assertTrue(a._wait_device_offline("S1", timeout_s=1))
        a.list_devices = lambda: [AdbDevice("S1", "offline", "")]  # type: ignore[method-assign]
        self.assertTrue(a._wait_device_offline("S1", timeout_s=1))

    def test_dropped_requires_real_offline(self) -> None:
        a = _fake_adb()
        a.list_devices = lambda: [AdbDevice("S1", "device", "")]  # type: ignore[method-assign]
        a._wait_device_offline = lambda *_a, **_k: False  # type: ignore[method-assign]
        self.assertFalse(a._connection_dropped_by_reset("S1", "error: device offline"))
        a._wait_device_offline = lambda *_a, **_k: True  # type: ignore[method-assign]
        self.assertTrue(a._connection_dropped_by_reset("S1", "error: device offline"))

    def test_confirm_never_presses_enter_on_unknown_screen(self) -> None:
        a = _fake_adb()
        sent: list[str] = []
        a._wait_device_offline = lambda *_a, **_k: False  # type: ignore[method-assign]
        a._focused_action_label = lambda _s: "Redefinir rede Wi-Fi?" if False else "Configurações"  # type: ignore[method-assign]
        a.shell_lax = lambda _s, cmd, timeout_s=0: sent.append(cmd) or ""  # type: ignore[method-assign]
        self.assertFalse(a._confirm_factory_reset_on_ui("S1"))
        self.assertEqual(sent, [])

    def test_confirm_from_cancel_goes_down_once_then_enter(self) -> None:
        a = _fake_adb()
        sent: list[str] = []
        labels = iter(["Cancelar", "Apagar tudo"])
        calls = {"off": 0}

        def offline(*_a, **_k) -> bool:
            calls["off"] += 1
            return "input keyevent 23" in sent

        a._wait_device_offline = offline  # type: ignore[method-assign]
        a._focused_action_label = lambda _s: next(labels)  # type: ignore[method-assign]
        a.shell_lax = lambda _s, cmd, timeout_s=0: sent.append(cmd) or ""  # type: ignore[method-assign]
        self.assertTrue(a._confirm_factory_reset_on_ui("S1"))
        self.assertEqual(sent, ["input keyevent 20", "input keyevent 23"])

    def test_confirm_aborts_if_down_does_not_reach_confirm(self) -> None:
        a = _fake_adb()
        sent: list[str] = []
        labels = iter(["Cancelar", "Cancelar"])
        a._wait_device_offline = lambda *_a, **_k: False  # type: ignore[method-assign]
        a._focused_action_label = lambda _s: next(labels)  # type: ignore[method-assign]
        a.shell_lax = lambda _s, cmd, timeout_s=0: sent.append(cmd) or ""  # type: ignore[method-assign]
        self.assertFalse(a._confirm_factory_reset_on_ui("S1"))
        self.assertNotIn("input keyevent 23", sent)


if __name__ == "__main__":
    unittest.main(verbosity=2)
