import json
import subprocess
import tempfile
import unittest
from pathlib import Path


def _ffprobe_duration_seconds(ffprobe: str, path: Path) -> float:
    cp = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    obj = json.loads(cp.stdout or "{}")
    dur = (obj.get("format") or {}).get("duration")
    return float(dur or 0.0)


class TestRecordingDuration(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from aibox.recording import _resolve_ffmpeg, _resolve_ffprobe

        cls.ffmpeg = _resolve_ffmpeg()
        cls.ffprobe = _resolve_ffprobe()
        if not cls.ffmpeg or not cls.ffprobe:
            raise unittest.SkipTest("ffmpeg/ffprobe não disponível no ambiente.")

    def _make_raw_h264(self, seconds: int, fps: int, raw_path: Path) -> None:
        subprocess.run(
            [
                self.ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"testsrc=size=1280x720:rate={fps}",
                "-t",
                str(int(seconds)),
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-tune",
                "zerolatency",
                "-f",
                "h264",
                str(raw_path),
            ],
            check=True,
            timeout=max(60, int(seconds) * 3),
        )

    def test_mp4_duration_5s(self) -> None:
        from aibox.recording import _convert_h264_to_mp4

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            raw = td / "in_5.h264"
            mp4 = td / "out_5.mp4"
            self._make_raw_h264(seconds=5, fps=30, raw_path=raw)
            ok, msg = _convert_h264_to_mp4(self.ffmpeg, raw, mp4, fps=30, expected_dur_s=5.0)
            self.assertTrue(ok, msg)
            self.assertTrue(mp4.exists())
            d = _ffprobe_duration_seconds(self.ffprobe, mp4)
            self.assertLess(abs(d - 5.0), 0.35)

    def test_mp4_duration_30s(self) -> None:
        from aibox.recording import _convert_h264_to_mp4

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            raw = td / "in_30.h264"
            mp4 = td / "out_30.mp4"
            self._make_raw_h264(seconds=30, fps=30, raw_path=raw)
            ok, msg = _convert_h264_to_mp4(self.ffmpeg, raw, mp4, fps=30, expected_dur_s=30.0)
            self.assertTrue(ok, msg)
            self.assertTrue(mp4.exists())
            d = _ffprobe_duration_seconds(self.ffprobe, mp4)
            self.assertLess(abs(d - 30.0), 0.35)

    def test_mp4_duration_60s(self) -> None:
        from aibox.recording import _convert_h264_to_mp4

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            raw = td / "in_60.h264"
            mp4 = td / "out_60.mp4"
            self._make_raw_h264(seconds=60, fps=30, raw_path=raw)
            ok, msg = _convert_h264_to_mp4(self.ffmpeg, raw, mp4, fps=30, expected_dur_s=60.0)
            self.assertTrue(ok, msg)
            self.assertTrue(mp4.exists())
            d = _ffprobe_duration_seconds(self.ffprobe, mp4)
            self.assertLess(abs(d - 60.0), 0.35)


if __name__ == "__main__":
    unittest.main(verbosity=2)

