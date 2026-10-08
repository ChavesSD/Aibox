from __future__ import annotations

import unittest
from unittest import mock

from aibox.adb import Adb, TtsConfigResult

VOZ_SELECTED = '<hierarchy><node text="Voz V" checked="true" bounds="[0,0][200,50]"/></hierarchy>'
VOZ_UNSELECTED = (
    '<hierarchy bounds="[0,0][1920,1080]"><node clickable="true" bounds="[0,200][1920,280]">'
    '<node text="Voz V" checked="false" bounds="[80,210][240,260]"/></node></hierarchy>'
)
OTHER_SCREEN = '<hierarchy><node text="Configurações gerais" bounds="[0,0][200,50]"/></hierarchy>'
AUTOSTART_OFF = (
    '<hierarchy><node bounds="[0,80][1920,200]"><node text="Auto startup" bounds="[40,90][600,180]"/>'
    '<node class="android.widget.ToggleButton" text="OFF" checked="false" bounds="[1500,100][1860,170]"/>'
    "</node></hierarchy>"
)
AUTOSTART_ON = AUTOSTART_OFF.replace('text="OFF" checked="false"', 'text="ON" checked="true"')


def _adb() -> Adb:
    a = Adb.__new__(Adb)
    a.adb_path = "adb"
    a._adb_dir = "."
    a._display_blanked = False
    a.restart_guard = None
    return a


class TestVoiceVNavigation(unittest.TestCase):
    """Sucesso só sem ressalva quando a Voz V é vista marcada; o resto é sinalizado."""

    def _run(self, dumps: list[str], *, select_now: bool = False):
        a = _adb()
        a.TTS_PT_BR_DOWNLOAD_WAIT_S = 0
        a.TTS_PT_BR_DOWNLOAD_POLL_S = 0
        a._tts_dpad_down = mock.Mock()
        a._tts_press_enter = mock.Mock()
        seq = iter(dumps)
        last = {"v": ""}

        def dump(_serial: str) -> str:
            try:
                last["v"] = next(seq)
            except StopIteration:
                pass
            return last["v"]

        a._dump_ui_xml = dump  # type: ignore[method-assign]
        a._tts_select_voice_v_now = mock.Mock(return_value=select_now)  # type: ignore[method-assign]
        with mock.patch("aibox.adb.time.sleep"):
            ok, trail = a._tts_navigate_installer("S1")
        return a, ok, trail

    def test_selected_voice_is_verified_success(self) -> None:
        # após-enter, poll (não vê Voz V), depois da sequência final: marcada
        _a, ok, trail = self._run([OTHER_SCREEN, OTHER_SCREEN, VOZ_SELECTED])
        self.assertTrue(ok)
        self.assertIn("voz-v-ok", trail)
        self.assertNotIn("voz-v-nao-verificada", trail)

    def test_voice_visible_but_unmarked_taps_then_confirms(self) -> None:
        a, ok, trail = self._run([OTHER_SCREEN, OTHER_SCREEN, VOZ_UNSELECTED], select_now=True)
        self.assertTrue(ok)
        self.assertIn("voz-v-ok", trail)
        a._tts_select_voice_v_now.assert_called()

    def test_voice_visible_unmarked_unconfirmed_is_flagged_not_silent(self) -> None:
        _a, ok, trail = self._run([OTHER_SCREEN, OTHER_SCREEN, VOZ_UNSELECTED], select_now=False)
        self.assertTrue(ok)  # mantém o comportamento que funciona nos Mini PCs
        self.assertIn("voz-v-nao-verificada", trail)

    def test_empty_dump_is_retried_and_flagged(self) -> None:
        a, ok, trail = self._run([OTHER_SCREEN, OTHER_SCREEN, "", "", "", ""])
        self.assertTrue(ok)
        self.assertIn("sem-dump", trail)
        self.assertIn("voz-v-nao-verificada", trail)

    def test_empty_dump_recovers_on_retry(self) -> None:
        _a, ok, trail = self._run([OTHER_SCREEN, OTHER_SCREEN, "", VOZ_SELECTED])
        self.assertTrue(ok)
        self.assertIn("voz-v-ok", trail)
        self.assertNotIn("sem-dump", trail)

    def test_unrelated_screen_fails(self) -> None:
        _a, ok, trail = self._run([OTHER_SCREEN, OTHER_SCREEN, OTHER_SCREEN])
        self.assertFalse(ok)

    def test_message_warns_when_unverified(self) -> None:
        a = _adb()
        a._resolve_tts_engine_package = mock.Mock(return_value="com.google.android.tts")  # type: ignore[method-assign]
        a.set_autostart_apps_enabled = mock.Mock()  # type: ignore[method-assign]
        a._tts_prepare_fast_ui = mock.Mock(return_value={})  # type: ignore[method-assign]
        a._tts_restore_animations = mock.Mock()  # type: ignore[method-assign]
        a.shell_try = mock.Mock(return_value="")  # type: ignore[method-assign]
        a._tts_apply_engine_locale = mock.Mock()  # type: ignore[method-assign]
        a._open_tts_voice_data_install = mock.Mock(return_value=True)  # type: ignore[method-assign]
        a._tts_navigate_installer = mock.Mock(return_value=(True, ["voz-v-nao-verificada"]))  # type: ignore[method-assign]
        a._tts_wake_engine_service = mock.Mock()  # type: ignore[method-assign]
        a._tts_verify_engine_locale = mock.Mock(return_value=True)  # type: ignore[method-assign]
        a.close_settings_and_tts_ui = mock.Mock()  # type: ignore[method-assign]
        res = a._configure_tts_pt_br_voice_v_once("S1", manage_display=False)
        self.assertIsInstance(res, TtsConfigResult)
        self.assertTrue(res.voice_v_confirmed)
        self.assertIn("ATENÇÃO", res.message)
        a._tts_restore_animations.assert_called_once()


class TestAutostartToggle(unittest.TestCase):
    def test_empty_dump_never_taps(self) -> None:
        a = _adb()
        dumps = iter(["", "", AUTOSTART_ON])
        a._dump_ui_xml = lambda s: next(dumps, "")  # type: ignore[method-assign]
        a._tap_only = mock.Mock(return_value=True)  # type: ignore[method-assign]
        a.shell_try = mock.Mock()  # type: ignore[method-assign]
        with mock.patch("aibox.adb.time.sleep"):
            self.assertTrue(a._ui_turn_on_autostart("S1"))
        a._tap_only.assert_not_called()

    def test_off_then_on_taps_once(self) -> None:
        a = _adb()
        dumps = iter([AUTOSTART_OFF, AUTOSTART_ON])
        a._dump_ui_xml = lambda s: next(dumps, AUTOSTART_ON)  # type: ignore[method-assign]
        a._tap_only = mock.Mock(return_value=True)  # type: ignore[method-assign]
        with mock.patch("aibox.adb.time.sleep"):
            self.assertTrue(a._ui_turn_on_autostart("S1"))
        self.assertEqual(a._tap_only.call_count, 1)

    def test_all_dumps_empty_is_failure_without_taps(self) -> None:
        a = _adb()
        a._dump_ui_xml = lambda s: ""  # type: ignore[method-assign]
        a._tap_only = mock.Mock()  # type: ignore[method-assign]
        with mock.patch("aibox.adb.time.sleep"):
            self.assertFalse(a._ui_turn_on_autostart("S1"))
        a._tap_only.assert_not_called()


class TestLabelMatching(unittest.TestCase):
    def test_short_text_does_not_match_long_label(self) -> None:
        a = _adb()
        taps: list[tuple[int, int]] = []
        a._tap_only = lambda s, x, y: taps.append((x, y)) or True  # type: ignore[method-assign]
        xml = '<hierarchy><node text="app" bounds="[0,0][100,40]"/></hierarchy>'
        self.assertFalse(a._ui_tap_label_anywhere("S1", ("totem app",), xml=xml))
        self.assertEqual(taps, [])

    def test_exact_and_contained_still_match(self) -> None:
        a = _adb()
        taps: list[tuple[int, int]] = []
        a._tap_only = lambda s, x, y: taps.append((x, y)) or True  # type: ignore[method-assign]
        xml = '<hierarchy><node text="Totem Interativo" bounds="[0,0][100,40]"/></hierarchy>'
        self.assertTrue(a._ui_tap_label_anywhere("S1", ("totem interativo",), xml=xml))
        self.assertTrue(a._ui_tap_label_anywhere("S1", ("totem",), xml=xml))  # rótulo contido no texto
        self.assertEqual(len(taps), 2)


class TestAutostartVerification(unittest.TestCase):
    def _run(self, main_xml: str):
        a = _adb()
        a.shell_try = mock.Mock(return_value="")  # type: ignore[method-assign]
        a._open_autostart_main = mock.Mock(return_value=True)  # type: ignore[method-assign]
        a._ui_dismiss_autostart_overlays = mock.Mock()  # type: ignore[method-assign]
        a._ui_turn_on_autostart = mock.Mock(return_value=True)  # type: ignore[method-assign]
        a._ui_open_applications_add = mock.Mock(return_value=True)  # type: ignore[method-assign]
        a._ui_pick_autostart_app_from_list = mock.Mock(return_value=True)  # type: ignore[method-assign]
        a._dump_ui_xml = mock.Mock(return_value=main_xml)  # type: ignore[method-assign]
        with mock.patch("aibox.adb.time.sleep"):
            return a._configure_autostart_app_ui(
                "S1", "com.autostart", ["br.com.x.totem"], labels={"br.com.x.totem": "Totem Interativo"}
            )

    def test_confirmed_on_main_screen(self) -> None:
        xml = '<hierarchy><node text="Auto startup"/><node text="Totem Interativo"/></hierarchy>'
        res = self._run(xml)
        self.assertTrue(res.ok)
        self.assertIn("conferido na tela", res.message)

    def test_missing_app_on_main_screen_fails(self) -> None:
        xml = '<hierarchy><node text="Auto startup"/><node text="Outro app"/></hierarchy>'
        res = self._run(xml)
        self.assertFalse(res.ok)
        self.assertIn("não confirmado na lista", res.message)

    def test_unreadable_screen_keeps_previous_behavior(self) -> None:
        res = self._run("")
        self.assertTrue(res.ok)
        self.assertIn("conferência indisponível", res.message)

    def test_other_screen_keeps_previous_behavior(self) -> None:
        res = self._run('<hierarchy><node text="Configurações"/></hierarchy>')
        self.assertTrue(res.ok)


class TestDisplayRestoreE(unittest.TestCase):
    def _restore(self, state: dict[str, str]) -> str:
        a = _adb()
        sent: list[str] = []
        a.shell_try = lambda s, cmd, timeout_s=0: sent.append(cmd) or ""  # type: ignore[method-assign]
        a._tts_restore_display("S1", state)
        return sent[0]

    def test_null_brightness_with_animations_still_restores_brightness(self) -> None:
        cmd = self._restore(
            {"system:screen_brightness": "null", "global:window_animation_scale": "1.0"}
        )
        self.assertIn("screen_brightness 128", cmd)
        self.assertIn("window_animation_scale 1.0", cmd)

    def test_saved_zero_brightness_becomes_128(self) -> None:
        cmd = self._restore({"system:screen_brightness": "0"})
        self.assertIn("screen_brightness 128", cmd)
        self.assertNotIn("screen_brightness 0", cmd)

    def test_real_brightness_is_kept(self) -> None:
        cmd = self._restore({"system:screen_brightness": "90"})
        self.assertIn("screen_brightness 90", cmd)
        self.assertNotIn("screen_brightness 128", cmd)

    def test_empty_fb_value_still_unblanks(self) -> None:
        cmd = self._restore({"fb:/sys/class/graphics/fb0/blank": ""})
        self.assertIn("echo 0 > /sys/class/graphics/fb0/blank", cmd)


class TestAnimationRestore(unittest.TestCase):
    def test_prepare_returns_originals_and_restore_applies_them(self) -> None:
        a = _adb()
        sent: list[str] = []

        def shell_try(_s, cmd, timeout_s=0):
            sent.append(cmd)
            return "WA=0.5\nTA=null\nAA=1.0\n" if "echo WA" in cmd else ""

        a.shell_try = shell_try  # type: ignore[method-assign]
        state = a._tts_prepare_fast_ui("S1")
        self.assertEqual(state["global:window_animation_scale"], "0.5")
        a._tts_restore_animations("S1", state)
        cmd = sent[-1]
        self.assertIn("window_animation_scale 0.5", cmd)
        self.assertIn("transition_animation_scale 1.0", cmd)  # "null" -> padrão
        self.assertIn("animator_duration_scale 1.0", cmd)

    def test_restore_without_state_uses_default(self) -> None:
        a = _adb()
        sent: list[str] = []
        a.shell_try = lambda s, cmd, timeout_s=0: sent.append(cmd) or ""  # type: ignore[method-assign]
        a._tts_restore_animations("S1", {})
        self.assertEqual(sent[0].count("1.0"), 3)

    def test_user_who_had_animations_off_stays_off(self) -> None:
        a = _adb()
        sent: list[str] = []
        a.shell_try = lambda s, cmd, timeout_s=0: sent.append(cmd) or ""  # type: ignore[method-assign]
        a._tts_restore_animations("S1", {"global:window_animation_scale": "0"})
        self.assertIn("window_animation_scale 0;", sent[0] + ";")

    def test_untrusted_values_rejected(self) -> None:
        a = _adb()
        sent: list[str] = []
        a.shell_try = lambda s, cmd, timeout_s=0: sent.append(cmd) or ""  # type: ignore[method-assign]
        a._tts_restore_animations("S1", {"global:window_animation_scale": "1; reboot"})
        self.assertNotIn("reboot", sent[0])


class TestSmallE(unittest.TestCase):
    def test_monkey_with_zero_events_falls_back_to_am_start(self) -> None:
        a = _adb()
        outs = iter(["Events injected: 0\n** Monkey aborted", "Starting: Intent { cmp=x/.Main }"])
        a.shell_combined = lambda s, cmd, timeout_s=0: next(outs)  # type: ignore[method-assign]
        a.launchable_component = mock.Mock(return_value="com.autostart/.Main")  # type: ignore[method-assign]
        with mock.patch("aibox.adb.time.sleep"):
            self.assertTrue(a._open_autostart_main("S1", "com.autostart"))
        a.launchable_component.assert_called_once()

    def test_monkey_success_does_not_need_fallback(self) -> None:
        a = _adb()
        a.shell_combined = lambda s, cmd, timeout_s=0: "Events injected: 1\n## Network stats"  # type: ignore[method-assign]
        a.launchable_component = mock.Mock()  # type: ignore[method-assign]
        with mock.patch("aibox.adb.time.sleep"):
            self.assertTrue(a._open_autostart_main("S1", "com.autostart"))
        a.launchable_component.assert_not_called()

    def test_close_ui_removes_temp_dump(self) -> None:
        a = _adb()
        sent: list[str] = []
        a.shell_try = lambda s, cmd, timeout_s=0: sent.append(cmd) or ""  # type: ignore[method-assign]
        a.close_settings_and_tts_ui("S1")
        self.assertIn("rm -f /sdcard/aibox_ui.xml", sent[0])

    def test_unknown_screen_after_enter_is_reclassified(self) -> None:
        a = _adb()
        a.TTS_PT_BR_DOWNLOAD_WAIT_S = 0
        a.TTS_PT_BR_DOWNLOAD_POLL_S = 0
        a._tts_dpad_down = mock.Mock()
        a._tts_press_enter = mock.Mock()
        country = (
            '<hierarchy bounds="[0,0][1920,1080]"><node text="País / Região"/>'
            '<node text="Brasil" clickable="true" bounds="[0,0][400,60]"/></hierarchy>'
        )
        seq = iter(["<hierarchy/>", "<hierarchy/>", country, OTHER_SCREEN, OTHER_SCREEN, OTHER_SCREEN])
        a._dump_ui_xml = lambda s: next(seq, OTHER_SCREEN)  # type: ignore[method-assign]
        a._tts_open_country_brazil = mock.Mock(return_value=(True, ["pais-ok"]))  # type: ignore[method-assign]
        with mock.patch("aibox.adb.time.sleep"):
            _ok, trail = a._tts_navigate_installer("S1")
        self.assertIn("apos-enter=country", trail)
        a._tts_open_country_brazil.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
