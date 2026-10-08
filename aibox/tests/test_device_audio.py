from __future__ import annotations

import unittest

from aibox.device_audio import (
    parse_alsa_captures,
    parse_sdk,
    pick_alsa_capture,
    scrcpy_audio_args_for_sdk,
)


class TestDeviceAudio(unittest.TestCase):
    def test_parse_sdk(self) -> None:
        self.assertEqual(parse_sdk("33"), 33)
        self.assertEqual(parse_sdk("30\r\n"), 30)
        self.assertIsNone(parse_sdk(""))
        self.assertIsNone(parse_sdk("n/a"))

    def test_scrcpy_args_android_13_keeps_hdmi(self) -> None:
        args, warn = scrcpy_audio_args_for_sdk(33)
        self.assertIn("--audio-dup", args)
        self.assertIn("playback", args)
        self.assertEqual(warn, "")

    def test_scrcpy_args_android_11_warns_mute(self) -> None:
        args, warn = scrcpy_audio_args_for_sdk(30)
        self.assertIn("output", args)
        self.assertTrue(warn)

    def test_scrcpy_args_android_10_unsupported(self) -> None:
        args, warn = scrcpy_audio_args_for_sdk(29)
        self.assertEqual(args, [])
        self.assertTrue(warn)

    def test_pick_hdmi_loopback_not_mic(self) -> None:
        pcm = "\n".join(
            [
                "00-00: audiocodec :  : playback 1 : capture 1",
                "01-00: ahubhdmi :  : playback 1",
                "02-00: Loopback PCM :  : playback 1 : capture 1",
                "03-00: dmic :  : capture 1",
            ]
        )
        chosen = pick_alsa_capture(parse_alsa_captures(pcm))
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual(chosen.card, 2)
        self.assertIn("loopback", chosen.name.lower())

    def test_ignore_mic_only_cards(self) -> None:
        pcm = "00-00: audiocodec-mic :  : playback 1 : capture 1\n"
        self.assertIsNone(pick_alsa_capture(parse_alsa_captures(pcm)))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestMuxDeviceAudio(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        from aibox.recording import _resolve_ffmpeg

        self.ffmpeg = _resolve_ffmpeg()
        if not self.ffmpeg:
            self.skipTest("ffmpeg indisponível")
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _make_media(self):
        import subprocess

        v = self.dir / "v.mp4"
        a = self.dir / "a.wav"
        subprocess.run(
            [self.ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
             "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(v)],
            check=True,
        )
        subprocess.run(
            [self.ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
             "-t", "3", "-ac", "2", str(a)],
            check=True,
        )
        return v, a

    def test_mux_adds_audio_track(self) -> None:
        from aibox.recording import _has_audio_stream, mux_audio_into_mp4

        v, a = self._make_media()
        out = self.dir / "o.mp4"
        ok, err = mux_audio_into_mp4(self.ffmpeg, v, a, out)
        self.assertTrue(ok, err)
        self.assertTrue(_has_audio_stream(out))

    def test_mux_with_delay(self) -> None:
        from aibox.recording import _has_audio_stream, mux_audio_into_mp4

        v, a = self._make_media()
        out = self.dir / "o.mp4"
        ok, err = mux_audio_into_mp4(self.ffmpeg, v, a, out, audio_delay_s=1.0)
        self.assertTrue(ok, err)
        self.assertTrue(_has_audio_stream(out))
