from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from aibox.recording import (
    _extend_static_recording,
    _ffmpeg_info,
    _is_single_frame_recording,
    _mp4_is_playable,
    _resolve_ffmpeg,
)


class TestStaticScreenRecording(unittest.TestCase):
    """screenrecord numa tela parada gera 1 quadro e duração N/A: deve virar um vídeo com o tempo real."""

    def setUp(self) -> None:
        self.ffmpeg = _resolve_ffmpeg()
        if not self.ffmpeg:
            self.skipTest("ffmpeg indisponível")
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.one = self.dir / "um_quadro.mp4"
        self.normal = self.dir / "normal.mp4"
        subprocess.run(
            [self.ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
             "-frames:v", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(self.one)],
            check=True,
        )
        subprocess.run(
            [self.ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
             "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(self.normal)],
            check=True,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_detects_single_frame_only(self) -> None:
        self.assertFalse(_mp4_is_playable(self.one))
        self.assertTrue(_is_single_frame_recording(self.one))
        self.assertTrue(_mp4_is_playable(self.normal))
        self.assertFalse(_is_single_frame_recording(self.normal))

    def test_truncated_file_is_not_treated_as_static(self) -> None:
        bad = self.dir / "cortado.mp4"
        bad.write_bytes(self.normal.read_bytes()[:3000])  # sem moov: arquivo realmente incompleto
        self.assertFalse(_is_single_frame_recording(bad))

    def test_extension_gives_real_duration_and_cleans_up(self) -> None:
        out = self.dir / "estendido.mp4"
        ok, err = _extend_static_recording(self.ffmpeg, self.one, out, 5.0)
        self.assertTrue(ok, err)
        self.assertTrue(_mp4_is_playable(out))
        dur, _audio = _ffmpeg_info(out)
        self.assertAlmostEqual(dur or 0, 5.0, delta=0.3)
        self.assertFalse(out.with_suffix(".frame.png").exists())

    def test_extension_is_clamped(self) -> None:
        out = self.dir / "curto.mp4"
        ok, _err = _extend_static_recording(self.ffmpeg, self.one, out, 0.1)  # mínimo de 1 s
        self.assertTrue(ok)
        self.assertGreaterEqual(_ffmpeg_info(out)[0] or 0, 0.9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
