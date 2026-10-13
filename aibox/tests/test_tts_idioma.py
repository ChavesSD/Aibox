from __future__ import annotations

import unittest
from unittest import mock

from aibox.adb import Adb

POLONES = (
    '<hierarchy bounds="[0,0][1280,720]"><node text="polonês (Polônia) vozes" bounds="[80,10][600,60]"/>'
    '<node text="Tamanho: cerca de 22 MB"/>'
    '<node text="Voz V" checked="false" bounds="[80,400][240,450]"/></hierarchy>'
)
PORTUGUES = (
    '<hierarchy bounds="[0,0][1280,720]"><node text="português (Brasil) vozes" bounds="[80,10][600,60]"/>'
    '<node text="Tamanho: cerca de 8,9 MB"/>'
    '<node text="Voz V" checked="false" bounds="[80,400][240,450]"/></hierarchy>'
)
PORTUGUES_MARCADA = PORTUGUES.replace('checked="false"', 'checked="true"')
RUSSO = POLONES.replace("polonês (Polônia)", "russo (Rússia)")
LISTA = '<hierarchy><node text="Dados de conversão de texto em voz"/><node text="alemão (Alemanha)"/></hierarchy>'


def _adb() -> Adb:
    a = Adb.__new__(Adb)
    a.adb_path = "adb"
    a._adb_dir = "."
    a._display_blanked = False
    a.restart_guard = None
    return a


class TestTitleParsing(unittest.TestCase):
    def test_title_extracted(self) -> None:
        self.assertEqual(Adb.xml_voice_language_title(POLONES), "polonês (Polônia)")
        self.assertEqual(Adb.xml_voice_language_title(PORTUGUES), "português (Brasil)")
        eng = '<hierarchy><node text="Portuguese (Brazil) voices"/></hierarchy>'
        self.assertEqual(Adb.xml_voice_language_title(eng), "Portuguese (Brazil)")

    def test_other_screens_have_no_title(self) -> None:
        self.assertIsNone(Adb.xml_voice_language_title(LISTA))
        self.assertIsNone(Adb.xml_voice_language_title(""))
        self.assertIsNone(Adb.xml_voice_language_title("<hierarchy"))

    def test_pt_br_detection(self) -> None:
        for ok in ("português (Brasil)", "Portuguese (Brazil)", "PORTUGUÊS (BRASIL)"):
            self.assertTrue(Adb._locale_title_is_pt_br(ok), ok)
        for bad in ("português (Portugal)", "polonês (Polônia)", "inglês (Reino Unido)", "", None):
            self.assertFalse(Adb._locale_title_is_pt_br(bad), bad)


class TestEnsurePortugueseBrazil(unittest.TestCase):
    def _run(self, dumps: list[str], start_xml: str):
        a = _adb()
        keys: list[str] = []
        seq = iter(dumps)
        a.shell_try = lambda s, cmd, timeout_s=0: keys.append(cmd) or ""  # type: ignore[method-assign]
        a._tts_press_enter = lambda s: keys.append("ENTER")  # type: ignore[method-assign]
        a._dump_ui_xml = lambda s: next(seq, dumps[-1] if dumps else "")  # type: ignore[method-assign]
        trail: list[str] = []
        msgs: list[str] = []
        with mock.patch("aibox.adb.time.sleep"):
            xml, ok = a._tts_ensure_portuguese_brazil("S1", start_xml, msgs.append, trail)
        return xml, ok, keys, trail, msgs

    def test_already_correct_does_nothing(self) -> None:
        _x, ok, keys, trail, _m = self._run([], PORTUGUES)
        self.assertTrue(ok)
        self.assertEqual(keys, [])
        self.assertEqual(trail, [])

    def test_unknown_screen_is_left_alone(self) -> None:
        _x, ok, keys, _t, _m = self._run([], LISTA)
        self.assertTrue(ok)
        self.assertEqual(keys, [])

    def test_polish_goes_back_one_down_and_opens_portuguese(self) -> None:
        xml, ok, keys, trail, msgs = self._run([PORTUGUES], POLONES)
        self.assertTrue(ok)
        self.assertEqual(keys, ["input keyevent 4", "input keyevent 20", "ENTER"])  # BACK, DOWN, ENTER
        self.assertEqual(trail, ["idioma-corrigido=1"])
        self.assertEqual(Adb.xml_voice_language_title(xml), "português (Brasil)")
        self.assertTrue(any("polonês" in m for m in msgs))

    def test_language_after_portuguese_alphabetically_goes_up(self) -> None:
        _x, ok, keys, _t, _m = self._run([PORTUGUES], RUSSO)
        self.assertTrue(ok)
        self.assertEqual(keys[1], "input keyevent 19")  # CIMA

    def test_two_corrections(self) -> None:
        _x, ok, _k, trail, _m = self._run([POLONES, PORTUGUES], POLONES)
        self.assertTrue(ok)
        self.assertEqual(trail, ["idioma-corrigido=2"])

    def test_gives_up_instead_of_confirming_wrong_language(self) -> None:
        _x, ok, keys, trail, msgs = self._run([POLONES], POLONES)
        self.assertFalse(ok)
        self.assertTrue(any(t.startswith("idioma-errado=") for t in trail))
        self.assertLessEqual(keys.count("ENTER"), 4)  # nunca entra em loop infinito
        self.assertTrue(any("NÃO foi selecionada" in m for m in msgs))


class TestNavigationIntegration(unittest.TestCase):
    def _nav(self, dumps: list[str], *, select_ok: bool = True):
        a = _adb()
        a.TTS_PT_BR_DOWNLOAD_WAIT_S = 0
        a.TTS_PT_BR_DOWNLOAD_POLL_S = 0
        keys: list[str] = []
        a.shell_try = lambda s, cmd, timeout_s=0: keys.append(cmd) or ""  # type: ignore[method-assign]
        a._tts_dpad_down = mock.Mock()  # type: ignore[method-assign]
        a._tts_press_enter = lambda s: keys.append("ENTER")  # type: ignore[method-assign]
        seq = iter(dumps)
        last = {"v": ""}

        def dump(_s: str) -> str:
            try:
                last["v"] = next(seq)
            except StopIteration:
                pass
            return last["v"]

        a._dump_ui_xml = dump  # type: ignore[method-assign]
        a._tts_select_voice_v_now = mock.Mock(return_value=select_ok)  # type: ignore[method-assign]
        with mock.patch("aibox.adb.time.sleep"):
            ok, trail = a._tts_navigate_installer("S1")
        return a, ok, trail, keys

    def test_first_arrow_lost_is_corrected_and_succeeds(self) -> None:
        # 1º dump (após o Enter): polonês -> corrige -> português; depois a Voz V é marcada.
        a, ok, trail, keys = self._nav([POLONES, PORTUGUES, PORTUGUES, PORTUGUES_MARCADA, PORTUGUES_MARCADA])
        self.assertTrue(ok)
        self.assertIn("idioma-corrigido=1", trail)
        self.assertIn("voz-v-ok", trail)
        self.assertEqual(keys[1:3], ["input keyevent 4", "input keyevent 20"])  # 1º ENTER foi o do idioma errado

    def test_correct_language_first_try_is_unchanged(self) -> None:
        a, ok, trail, keys = self._nav([PORTUGUES, PORTUGUES, PORTUGUES_MARCADA, PORTUGUES_MARCADA])
        self.assertTrue(ok)
        self.assertFalse(any(t.startswith("idioma") for t in trail))
        self.assertNotIn("input keyevent 4", keys)

    def test_wrong_language_never_reports_success(self) -> None:
        a, ok, trail, _k = self._nav([POLONES])
        self.assertFalse(ok)
        self.assertTrue(any(t.startswith("idioma-errado=") for t in trail))
        a._tts_select_voice_v_now.assert_not_called()  # não marca a Voz V de outro idioma

    def test_final_screen_in_wrong_language_fails(self) -> None:
        # idioma certo no começo, mas a tela final aparece em outro idioma
        a, ok, trail, _k = self._nav([PORTUGUES, PORTUGUES, POLONES, POLONES], select_ok=False)
        self.assertFalse(ok)
        self.assertTrue(any(t.startswith("idioma-errado=") for t in trail))


class TestOptimizeDiskMessage(unittest.TestCase):
    def _report(self, dexopt_ok: bool) -> str:
        a = _adb()
        calls = {"df": 0}

        def lax(serial, cmd, timeout_s=60):
            if cmd.startswith("df -k /data"):
                calls["df"] += 1
                used = 1910020 if calls["df"] == 1 else 1970312
                return f"/dev/block/mmcblk0p17  12335084 {used} 10425064  16% /data"
            return ""

        a.shell_lax = lax  # type: ignore[method-assign]
        a._shell_lax_soft = lax  # type: ignore[method-assign]
        a._optimize_dexopt = lambda s: (dexopt_ok, "msg")  # type: ignore[method-assign]
        a._optimize_cache_footprint_kb = lambda s: (0, "")  # type: ignore[method-assign]
        with mock.patch("aibox.adb.time.sleep"):
            return a.optimize_device("S1")

    def test_message_blames_dexopt_when_it_ran(self) -> None:
        text = self._report(True)
        self.assertIn("aumentou", text)
        self.assertIn("dexopt", text.split("aumentou", 1)[1].splitlines()[0])
        self.assertNotIn("uso normal do sistema", text)

    def test_message_stays_generic_without_dexopt(self) -> None:
        text = self._report(False)
        self.assertIn("uso normal do sistema", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
