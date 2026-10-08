from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aibox import recording
from aibox.recording import _ffmpeg_info, _has_audio_stream, _mp4_is_playable, _resolve_ffmpeg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import build_exe  # noqa: E402


class TestNoFfprobeFallback(unittest.TestCase):
    """O app empacotado leva só o ffmpeg.exe: duração e áudio vêm de `ffmpeg -i`."""

    def setUp(self) -> None:
        self.ffmpeg = _resolve_ffmpeg()
        if not self.ffmpeg:
            self.skipTest("ffmpeg indisponível")
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.video = d / "v.mp4"
        self.with_audio = d / "va.mp4"
        subprocess.run(
            [self.ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
             "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(self.video)],
            check=True,
        )
        subprocess.run(
            [self.ffmpeg, "-loglevel", "error", "-y", "-i", str(self.video), "-f", "lavfi",
             "-i", "sine=frequency=440:sample_rate=44100", "-t", "3", "-c:v", "copy", "-c:a", "aac", str(self.with_audio)],
            check=True,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_ffmpeg_info_duration_and_audio(self) -> None:
        dur, audio = _ffmpeg_info(self.with_audio)
        self.assertAlmostEqual(dur or 0, 3.0, delta=0.3)
        self.assertTrue(audio)
        dur2, audio2 = _ffmpeg_info(self.video)
        self.assertAlmostEqual(dur2 or 0, 3.0, delta=0.3)
        self.assertFalse(audio2)

    def test_playable_and_audio_detection_without_ffprobe(self) -> None:
        with mock.patch.object(recording, "_resolve_ffprobe", return_value=None):
            self.assertTrue(_mp4_is_playable(self.video))
            self.assertTrue(_has_audio_stream(self.with_audio))
            self.assertFalse(_has_audio_stream(self.video))

    def test_truncated_file_not_playable_without_ffprobe(self) -> None:
        bad = Path(self.tmp.name) / "bad.mp4"
        bad.write_bytes(self.video.read_bytes()[:3000])
        with mock.patch.object(recording, "_resolve_ffprobe", return_value=None):
            self.assertFalse(_mp4_is_playable(bad))

    def test_unreadable_info_is_none(self) -> None:
        self.assertEqual(_ffmpeg_info(Path(self.tmp.name) / "nao_existe.mp4"), (None, None))


class TestFrozenResolution(unittest.TestCase):
    def test_frozen_uses_only_bundled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "ffmpeg"
            bundled.mkdir()
            (bundled / "ffmpeg.exe").write_bytes(b"x")
            with mock.patch.object(recording, "is_frozen", return_value=True), \
                    mock.patch.object(recording, "package_dir", return_value=Path(tmp)), \
                    mock.patch.object(recording, "executable_dir", return_value=Path(tmp) / "nada"), \
                    mock.patch.object(recording, "_check_binary", return_value=True), \
                    mock.patch.object(recording.shutil, "which", side_effect=AssertionError("PATH não pode ser usado")), \
                    mock.patch.dict("os.environ", {"AIBOX_FFMPEG_PATH": r"C:\evil\ffmpeg.exe"}):
                self.assertEqual(Path(_resolve_ffmpeg()), bundled / "ffmpeg.exe")

    def test_frozen_without_bundle_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(recording, "is_frozen", return_value=True), \
                    mock.patch.object(recording, "package_dir", return_value=Path(tmp)), \
                    mock.patch.object(recording, "executable_dir", return_value=Path(tmp)), \
                    mock.patch.object(recording.shutil, "which", side_effect=AssertionError("PATH não pode ser usado")):
                self.assertIsNone(_resolve_ffmpeg())
                self.assertIsNone(recording._resolve_ffprobe())


class TestPreserveApks(unittest.TestCase):
    def test_update_keeps_downloaded_apks(self) -> None:
        from aibox import update_helper

        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            inst = t / "Aibox"
            (inst / "Apks" / "Totem").mkdir(parents=True)
            (inst / "Apks" / "Totem" / "Upzz.apk").write_bytes(b"APK-JA-BAIXADO")
            new = t / "Aibox.new-1"
            (new / "Apks").mkdir(parents=True)
            (new / "Apks" / "LEIA-ME.txt").write_text("novo")
            update_helper.preserve_user_data(inst, new)
            self.assertEqual((new / "Apks" / "Totem" / "Upzz.apk").read_bytes(), b"APK-JA-BAIXADO")
            self.assertEqual((new / "Apks" / "LEIA-ME.txt").read_text(), "novo")

    def test_stage_new_onedir_carries_apks(self) -> None:
        import io
        import zipfile

        from aibox import update_helper

        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            inst = t / "Aibox"
            (inst / "Apks" / "Outros").mkdir(parents=True)
            (inst / "Apks" / "Outros" / "Autostart.apk").write_bytes(b"A")
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("Aibox/Aibox.exe", b"EXE")
            pkg = t / "p.zip"
            pkg.write_bytes(buf.getvalue())
            new = update_helper.stage_new_onedir(pkg, inst)
            self.assertTrue((new / "Aibox.exe").is_file())
            self.assertEqual((new / "Apks" / "Outros" / "Autostart.apk").read_bytes(), b"A")

    def test_installer_keeps_apks(self) -> None:
        import io
        import zipfile

        from installer import setup_main

        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            inst = t / "Aibox"
            (inst / "Apks" / "Totem").mkdir(parents=True)
            (inst / "Apks" / "Totem" / "Upzz.apk").write_bytes(b"APK")
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("Aibox/Aibox.exe", b"EXE-NOVO")
                zf.writestr("Aibox/Apks/LEIA-ME.txt", b"leia")
            pay = t / "payload.zip"
            pay.write_bytes(buf.getvalue())
            patches = [
                mock.patch.object(setup_main, "INSTALL_DIR", inst),
                mock.patch.object(setup_main, "_payload_path", return_value=pay),
                mock.patch.object(setup_main, "_stop_running_app"),
                mock.patch.object(setup_main, "_create_shortcut"),
                mock.patch.object(setup_main, "_write_uninstaller"),
                mock.patch.object(setup_main, "trust_installed_app"),
            ]
            for p in patches:
                p.start()
            try:
                setup_main.perform_install()
            finally:
                for p in patches:
                    p.stop()
            self.assertEqual((inst / "Apks" / "Totem" / "Upzz.apk").read_bytes(), b"APK")
            self.assertEqual((inst / "Apks" / "LEIA-ME.txt").read_bytes(), b"leia")
            self.assertEqual((inst / "Aibox.exe").read_bytes(), b"EXE-NOVO")


class TestReleaseChecks(unittest.TestCase):
    def _with_notes(self, name: str, payload: dict | str | None):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "release_notes").mkdir()
        if payload is not None:
            text = payload if isinstance(payload, str) else json.dumps(payload)
            (root / "release_notes" / f"{name}.json").write_text(text, encoding="utf-8")
        return tmp, root

    def test_validation_rules(self) -> None:
        good = {"version": "9.9.9", "published_at": "2026-10-07", "notes": {"added": ["x"], "fixed": [], "removed": []}}
        cases = [
            (good, False),
            (None, True),
            ("{nao json", True),
            ({**good, "version": "1.0.0"}, True),
            ({**good, "published_at": ""}, True),
            ({**good, "published_at": "07/10/2026"}, True),
            ({**good, "notes": {"added": [], "fixed": [], "removed": []}}, True),
        ]
        for payload, expect_problem in cases:
            tmp, root = self._with_notes("9.9.9", payload)
            try:
                with mock.patch.object(build_exe, "ROOT", root), mock.patch.dict("os.environ", {}, clear=False):
                    import os

                    os.environ.pop("AIBOX_ALLOW_NO_NOTES", None)
                    problem = build_exe._validate_release_notes("9.9.9")
                self.assertEqual(problem is not None, expect_problem, str(payload))
            finally:
                tmp.cleanup()

    def test_escape_hatch(self) -> None:
        tmp, root = self._with_notes("9.9.9", None)
        try:
            with mock.patch.object(build_exe, "ROOT", root), mock.patch.dict("os.environ", {"AIBOX_ALLOW_NO_NOTES": "1"}):
                self.assertIsNone(build_exe._validate_release_notes("9.9.9"))
        finally:
            tmp.cleanup()

    def test_current_version_has_valid_notes(self) -> None:
        self.assertIsNone(build_exe._validate_release_notes(build_exe._app_version()))

    def test_ffmpeg_pin_is_well_formed(self) -> None:
        self.assertRegex(build_exe.FFMPEG_SHA256, r"^[0-9a-f]{64}$")
        self.assertTrue(build_exe.FFMPEG_URL.startswith("https://github.com/"))


class TestAutostartReenabled(unittest.TestCase):
    def test_post_install_reenables_autostart_even_if_tts_fails(self) -> None:
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        QApplication.instance() or QApplication(sys.argv)
        from aibox.adb import Adb, TtsConfigResult
        from aibox.main_window import MainWindow

        w = MainWindow()
        adb = Adb.__new__(Adb)
        adb.adb_path = "adb"
        adb._adb_dir = "."
        adb._display_blanked = False
        adb.restart_guard = None
        calls: list[bool] = []
        adb.set_autostart_apps_enabled = lambda s, enabled: calls.append(enabled)  # type: ignore[method-assign]
        adb.configure_tts_pt_br_voice_v = lambda *a, **k: TtsConfigResult(  # type: ignore[method-assign]
            voice_v_confirmed=False, engine_locale_ok=False, message="falhou"
        )
        adb.close_settings_and_tts_ui = lambda *a, **k: None  # type: ignore[method-assign]
        w.adb = adb
        w._install_queue = []
        w._install_need_tts_config = True
        w._install_tts_apk_ok = True
        w._install_configure_boot = False
        captured: dict = {}
        w.bg.run = lambda fn, ok, err: captured.update(fn=fn)  # type: ignore[method-assign]
        w._install_next_in_queue_impl("S1")
        msg, complete = captured["fn"]()
        self.assertFalse(complete)
        self.assertEqual(calls, [False, True])  # desabilitou para o TTS e SEMPRE reabilitou


if __name__ == "__main__":
    unittest.main(verbosity=2)
