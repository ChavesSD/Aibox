from __future__ import annotations

import unittest
from unittest import mock

from aibox.adb import Adb, AutostartConfigResult, TtsConfigResult
from aibox.apks_catalog import APK_CATALOG, ApkEntry
from aibox.main_window import (
    expand_install_dependencies,
    install_apk_phase_percent,
    is_autostart_apk,
    wants_boot_autostart,
)


def _catalog(category: str, filename: str) -> ApkEntry:
    for e in APK_CATALOG:
        if e.category == category and e.filename == filename:
            return e
    raise AssertionError(f"APK não encontrado: {category}/{filename}")


TTS = _catalog("Outros", "Sintese_de_Voz.apk")
AUTOSTART = _catalog("Outros", "Autostart.apk")
ADB_WIFI = _catalog("Outros", "ADB_Wifi.apk")
PAINEL = _catalog("Painel", "Aiclass.apk")
TOTEM = _catalog("Totem", "Upzz.apk")


class TestInstallProgress(unittest.TestCase):
    def test_apk_phase_caps_at_60_when_post_install(self) -> None:
        self.assertEqual(install_apk_phase_percent(0, 4, has_post_install=True), 0)
        self.assertEqual(install_apk_phase_percent(2, 4, has_post_install=True), 30)
        self.assertEqual(install_apk_phase_percent(4, 4, has_post_install=True), 60)

    def test_apk_phase_reaches_100_without_post_install(self) -> None:
        self.assertEqual(install_apk_phase_percent(4, 4, has_post_install=False), 100)

    def test_tts_result_blocks_autostart(self) -> None:
        fail = TtsConfigResult(
            voice_v_confirmed=False,
            engine_locale_ok=True,
            message="parcial",
        )
        self.assertFalse(fail.ok)
        ok = TtsConfigResult(
            voice_v_confirmed=True,
            engine_locale_ok=True,
            message="ok",
        )
        self.assertTrue(ok.ok)

    def test_tts_retries_when_first_attempt_misses_voice_v(self) -> None:
        adb = Adb.__new__(Adb)
        calls = {"n": 0}
        fail = TtsConfigResult(False, False, "parcial")
        ok = TtsConfigResult(True, True, "ok")

        def once(*_a, **_k):
            calls["n"] += 1
            return fail if calls["n"] == 1 else ok

        adb._configure_tts_pt_br_voice_v_once = once  # type: ignore[method-assign]
        adb.close_settings_and_tts_ui = lambda *_a, **_k: None  # type: ignore[method-assign]
        with mock.patch("aibox.adb.time.sleep"):
            result = Adb.configure_tts_pt_br_voice_v(adb, "serial")
        self.assertEqual(calls["n"], 2)
        self.assertTrue(result.voice_v_confirmed)
        self.assertIn("2ª", result.message)

    def test_autostart_retries_when_first_attempt_fails(self) -> None:
        adb = Adb.__new__(Adb)
        calls = {"n": 0}

        def once(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                return AutostartConfigResult(ok=False, message="falhou")
            return AutostartConfigResult(ok=True, message="ok")

        adb._configure_boot_autostart_once = once  # type: ignore[method-assign]
        adb.close_settings_and_tts_ui = lambda *_a, **_k: None  # type: ignore[method-assign]
        with mock.patch("aibox.adb.time.sleep"):
            result = Adb.configure_boot_autostart(adb, "serial", ["com.example.app"])
        self.assertEqual(calls["n"], 2)
        self.assertTrue(result.ok)
        self.assertIn("2ª", result.message)


class TestAutostartXml(unittest.TestCase):
    def test_auto_startup_off_toggle_is_detected(self) -> None:
        xml = """
        <hierarchy>
          <node bounds="[0,80][1920,200]">
            <node text="Auto startup" bounds="[40,90][600,180]"/>
            <node class="android.widget.ToggleButton" text="OFF" checked="false"
                  bounds="[1500,100][1860,170]"/>
          </node>
        </hierarchy>
        """
        self.assertFalse(Adb._ui_autostart_is_on(xml))
        state, xy = Adb._ui_autostart_startup_state(xml)
        self.assertEqual(state, "off")
        self.assertIsNotNone(xy)
        self.assertGreater(xy[0], 1400)

    def test_auto_startup_equals_off_text(self) -> None:
        xml = """
        <hierarchy>
          <node text="Auto startup =  OFF" bounds="[0,80][1920,160]"/>
        </hierarchy>
        """
        state, xy = Adb._ui_autostart_startup_state(xml)
        self.assertEqual(state, "off")
        self.assertIsNotNone(xy)

    def test_auto_startup_on_toggle_is_detected(self) -> None:
        xml = """
        <hierarchy>
          <node bounds="[0,80][1920,200]">
            <node text="Auto startup" bounds="[40,90][600,180]"/>
            <node class="android.widget.ToggleButton" text="ON" checked="true"
                  bounds="[1500,100][1860,170]"/>
          </node>
        </hierarchy>
        """
        self.assertTrue(Adb._ui_autostart_is_on(xml))

    def test_unrelated_checked_toggle_is_not_auto_startup(self) -> None:
        xml = """
        <hierarchy>
          <node class="android.widget.ToggleButton" text="Wifi" checked="true"
                bounds="[10,10][80,40]"/>
          <node text="Auto startup" bounds="[40,90][600,180]"/>
        </hierarchy>
        """
        self.assertFalse(Adb._ui_autostart_is_on(xml))


class TestVoiceVXml(unittest.TestCase):
    def test_voice_v_checked_is_selected(self) -> None:
        xml = """
        <hierarchy>
          <node text="Voz V" checked="true" selected="false" bounds="[0,0][100,40]"/>
        </hierarchy>
        """
        self.assertTrue(Adb.xml_voice_v_selected(xml))

    def test_voice_v_unchecked_is_not_selected(self) -> None:
        xml = """
        <hierarchy>
          <node text="Voz V" checked="false" selected="false" bounds="[0,0][100,40]"/>
        </hierarchy>
        """
        self.assertFalse(Adb.xml_voice_v_selected(xml))

    def test_parent_row_checked_counts(self) -> None:
        xml = """
        <hierarchy>
          <node checked="true" selected="false">
            <node text="Voz V" checked="false" selected="false"/>
          </node>
        </hierarchy>
        """
        self.assertTrue(Adb.xml_voice_v_selected(xml))

    def test_checkbox_sibling_counts(self) -> None:
        xml = """
        <hierarchy>
          <node>
            <node text="Voz V"/>
            <node checked="true" class="android.widget.CheckBox"/>
          </node>
        </hierarchy>
        """
        self.assertTrue(Adb.xml_voice_v_selected(xml))

    def test_busy_download(self) -> None:
        self.assertTrue(Adb.xml_tts_busy('<node text="Baixando 40%"/>'))
        self.assertFalse(Adb.xml_tts_busy('<node text="Voz V"/>'))

    def test_country_picker_detects_brasil_and_portugal(self) -> None:
        xml = '<node text="Brasil"/><node text="Portugal"/>'
        self.assertTrue(Adb.xml_is_country_picker(xml))
        self.assertFalse(Adb.xml_is_country_picker('<node text="Português"/>'))

    def test_language_list_with_brazil_variant_is_not_country(self) -> None:
        xml = (
            '<node text="English"/><node text="Español"/>'
            '<node text="Português (Brasil)"/><node text="Português (Portugal)"/>'
        )
        self.assertTrue(Adb.xml_has_language_list(xml))
        self.assertFalse(Adb.xml_is_country_picker(xml))
        self.assertEqual(Adb._tts_classify_screen(xml), "language")

    def test_classify_country_and_voices(self) -> None:
        country = '<node text="País"/><node text="Brasil"/><node text="Portugal"/>'
        self.assertEqual(Adb._tts_classify_screen(country), "country")
        voices = '<node text="Voz V"/><node text="Fazer o download"/>'
        self.assertEqual(Adb._tts_classify_screen(voices), "voices")
        download = '<node text="Fazer o download do pacote de voz"/>'
        self.assertEqual(Adb._tts_classify_screen(download), "download")
        self.assertFalse(Adb.xml_has_voice_list(download))

    def test_hdmi_list_row_is_not_full_screen(self) -> None:
        self.assertFalse(Adb._node_is_full_screen(1920, 72, 1920, 1080))
        self.assertTrue(Adb._node_is_full_screen(1920, 1080, 1920, 1080))
        self.assertFalse(Adb._node_is_full_screen(1280, 56, 1280, 720))

    def test_portuguese_brazil_ui_label(self) -> None:
        self.assertTrue(Adb._tts_is_portuguese_brazil("português (Brasil)"))
        self.assertTrue(Adb._tts_is_portuguese_brazil("Português (Brasil)"))
        self.assertTrue(Adb._tts_is_portuguese_brazil("portuguese (Brazil)"))
        self.assertFalse(Adb._tts_is_portuguese_brazil("português (Portugal)"))
        self.assertFalse(Adb._tts_is_portuguese_brazil("português"))
        xml = """
        <hierarchy bounds="[0,0][1920,1080]">
          <node text="português (Portugal)" bounds="[0,480][1920,552]"/>
          <node bounds="[0,400][1920,472]">
            <node text="português"/>
            <node text="(Brasil)"/>
          </node>
        </hierarchy>
        """
        self.assertEqual(Adb._tts_find_pt_br_center(xml), (960, 436))
        xml_exact = (
            '<hierarchy bounds="[0,0][1920,1080]">'
            '<node text="português (Brasil)" bounds="[80,300][1840,380]"/>'
            "</hierarchy>"
        )
        self.assertEqual(Adb._tts_find_pt_br_center(xml_exact), (960, 340))

    def test_voice_v_exact_label(self) -> None:
        self.assertTrue(Adb._tts_is_voice_v_label("Voz V"))
        self.assertTrue(Adb._tts_is_voice_v_label("voz v"))
        self.assertFalse(Adb._tts_is_voice_v_label("V"))
        self.assertFalse(Adb._tts_is_voice_v_label("Voz X"))
        xml = """
        <hierarchy bounds="[0,0][1920,1080]">
          <node clickable="true" bounds="[0,200][1920,280]">
            <node text="Voz V" bounds="[80,210][240,260]"/>
          </node>
        </hierarchy>
        """
        self.assertEqual(Adb._tts_find_voice_v_center(xml), (960, 240))

    def test_tts_dpad_recipe_counts(self) -> None:
        self.assertEqual(Adb.TTS_PT_BR_DOWN_COUNT, 38)
        self.assertEqual(Adb.TTS_VOICE_V_DOWN_COUNT, 5)
        self.assertEqual(Adb.TTS_PT_BR_DOWNLOAD_WAIT_S, 30)
        self.assertEqual(Adb.TTS_PT_BR_DOWNLOAD_POLL_S, 30)
        self.assertEqual(Adb.TTS_INSTALLER_READY_WAIT_S, 15)
        self.assertEqual(Adb.CONFIG_RETRY_ATTEMPTS, 2)
        cmd38 = Adb._tts_keyevent_cmd(38, enter=False)
        self.assertTrue(cmd38.startswith("input keyevent "))
        self.assertEqual(cmd38.split().count("20"), 38)
        self.assertNotIn("23", cmd38.split())
        cmd5 = Adb._tts_keyevent_cmd(5, enter=False)
        self.assertEqual(cmd5.split().count("20"), 5)

    def test_voice_list_detects_voz_v(self) -> None:
        self.assertTrue(Adb.xml_has_voice_list('<node text="Voz V"/>'))
        self.assertFalse(Adb.xml_has_voice_list('<node text="Português"/>'))

    def test_loading_splash_is_not_ready_installer(self) -> None:
        self.assertEqual(Adb._tts_classify_screen('<node text="Loading"/>'), "unknown")
        self.assertEqual(Adb._tts_classify_screen('<node text="Carregando"/>'), "unknown")
        self.assertEqual(Adb._tts_classify_screen("<hierarchy></hierarchy>"), "unknown")
        self.assertTrue(Adb.xml_tts_busy('<node text="Loading"/>'))

    def test_country_brazil_center_ignores_portugal_and_language_row(self) -> None:
        country = (
            '<hierarchy bounds="[0,0][1920,1080]">'
            '<node text="Portugal" bounds="[0,200][1920,280]"/>'
            '<node text="Brasil" bounds="[0,300][1920,380]"/>'
            "</hierarchy>"
        )
        self.assertEqual(Adb._tts_find_country_brazil_center(country), (960, 340))
        self.assertTrue(Adb._tts_is_country_brazil_label("Brasil"))
        self.assertTrue(Adb._tts_is_country_brazil_label("Brazil"))
        self.assertFalse(Adb._tts_is_country_brazil_label("português (Brasil)"))
        self.assertFalse(Adb._tts_is_country_brazil_label("Portugal"))
        language = (
            '<hierarchy bounds="[0,0][1920,1080]">'
            '<node text="português (Brasil)" bounds="[80,300][1840,380]"/>'
            "</hierarchy>"
        )
        self.assertIsNone(Adb._tts_find_country_brazil_center(language))

    def test_wait_ready_skips_splash_until_language_list(self) -> None:
        screens = [
            "",
            "<hierarchy></hierarchy>",
            '<node text="Loading"/>',
            (
                '<node text="English"/><node text="Español"/>'
                '<node text="Português (Brasil)"/>'
            ),
        ]
        step = {"n": 0}
        adb = Adb.__new__(Adb)

        def dump(_serial: str) -> str:
            xml = screens[min(step["n"], len(screens) - 1)]
            step["n"] += 1
            return xml

        adb._dump_ui_xml = dump  # type: ignore[method-assign]
        adb.shell_try = lambda *_a, **_k: None  # type: ignore[method-assign]
        with mock.patch("aibox.adb.time.sleep"):
            ok = Adb._tts_wait_voice_installer_ready(
                adb, "serial", "com.google.android.tts", timeout_s=5
            )
        self.assertTrue(ok)
        self.assertGreaterEqual(step["n"], 4)

    def test_navigate_taps_visible_pt_br_without_blind_dpad(self) -> None:
        language = (
            '<hierarchy bounds="[0,0][1920,1080]">'
            '<node text="English" bounds="[0,200][1920,280]"/>'
            '<node text="Español" bounds="[0,280][1920,360]"/>'
            '<node text="português (Brasil)" bounds="[80,400][1840,480]"/>'
            "</hierarchy>"
        )
        voices = (
            '<hierarchy bounds="[0,0][1920,1080]">'
            '<node text="Voz V" checked="true" bounds="[0,200][1920,280]"/>'
            "</hierarchy>"
        )
        adb = Adb.__new__(Adb)
        taps: list[tuple[int, int]] = []
        downs: list[object] = []
        dumps = {"n": 0}

        def dump(_serial: str) -> str:
            dumps["n"] += 1
            return language if dumps["n"] == 1 else voices

        adb._tts_wait_voice_installer_ready = lambda *_a, **_k: True  # type: ignore[method-assign]
        adb._dump_ui_xml = dump  # type: ignore[method-assign]
        adb.shell_try = lambda *_a, **_k: "ok"  # type: ignore[method-assign]
        adb._tts_dpad_down = lambda _s, n: downs.append(n)  # type: ignore[method-assign]
        adb._tts_press_enter = lambda *_a, **_k: downs.append("enter")  # type: ignore[method-assign]
        adb._tap_only = (  # type: ignore[method-assign]
            lambda _s, x, y: taps.append((x, y)) or True
        )
        adb._wait_ui_change = lambda *_a, **_k: voices  # type: ignore[method-assign]
        adb._tts_select_voice_v_now = lambda *_a, **_k: True  # type: ignore[method-assign]
        with mock.patch("aibox.adb.time.sleep"):
            ok, trail = Adb._tts_navigate_installer(adb, "serial")
        self.assertTrue(ok)
        self.assertIn("tap-pt-BR", trail)
        self.assertNotIn(38, downs)
        self.assertTrue(taps)


class TestFocusParse(unittest.TestCase):
    def test_parse_tts_focus(self) -> None:
        line = (
            "  mCurrentFocus=Window{abc u0 "
            "com.google.android.tts/com.google.android.tts.local.voicepack.ui.VoiceDataInstallActivity}"
        )
        self.assertEqual(Adb.parse_focus_package(line), "com.google.android.tts")

    def test_parse_autostart_focus(self) -> None:
        line = "mFocusedApp=AppWindowToken{x token=Token{y ActivityRecord{z u0 com.autostart/.Main t10}}}"
        pkg = Adb.parse_focus_package(line)
        self.assertTrue(pkg == "com.autostart" or "autostart" in pkg)

    def test_tts_line_is_not_autostart(self) -> None:
        line = "mCurrentFocus=Window{abc u0 com.google.android.tts/.Foo}"
        self.assertEqual(Adb.parse_focus_package(line), "com.google.android.tts")
        self.assertNotIn("autostart", Adb.parse_focus_package(line))


class TestAutostartInstallDeps(unittest.TestCase):
    def _expand(self, *entries: ApkEntry) -> list[ApkEntry]:
        return expand_install_dependencies(
            list(entries), tts=TTS, autostart=AUTOSTART
        )

    def test_any_apk_pulls_autostart(self) -> None:
        out = self._expand(ADB_WIFI)
        self.assertEqual([e.filename for e in out], ["Autostart.apk", "ADB_Wifi.apk"])

    def test_totem_still_pulls_autostart(self) -> None:
        out = self._expand(TOTEM)
        self.assertEqual([e.filename for e in out], ["Autostart.apk", "Upzz.apk"])

    def test_painel_pulls_tts_then_autostart(self) -> None:
        out = self._expand(PAINEL)
        self.assertEqual(
            [e.filename for e in out],
            ["Sintese_de_Voz.apk", "Autostart.apk", "Aiclass.apk"],
        )

    def test_autostart_alone_does_not_duplicate(self) -> None:
        out = self._expand(AUTOSTART)
        self.assertEqual([e.filename for e in out], ["Autostart.apk"])

    def test_boot_targets_exclude_autostart_and_tts(self) -> None:
        self.assertFalse(wants_boot_autostart(AUTOSTART))
        self.assertFalse(wants_boot_autostart(TTS))
        self.assertTrue(wants_boot_autostart(ADB_WIFI))
        self.assertTrue(wants_boot_autostart(PAINEL))
        self.assertTrue(is_autostart_apk(AUTOSTART))
        self.assertFalse(is_autostart_apk(ADB_WIFI))

    def test_autostart_search_labels_use_app_name(self) -> None:
        labels = Adb._autostart_app_search_labels(
            "com.example.aiclass", "Aiclass"
        )
        self.assertIn("Aiclass", labels)
        self.assertIn("Aiclass - Painel", labels)
        self.assertNotIn("Painel", labels)


class TestApkCatalog(unittest.TestCase):
    def test_ai_horizontal_is_in_totem(self) -> None:
        entry = _catalog("Totem", "Ai_Horizontal.apk")
        self.assertEqual(entry.label, "AI Horizontal")
        self.assertTrue(wants_boot_autostart(entry))
