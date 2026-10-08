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

    def _make_mp4_with_tone(self, seconds: int, mp4_path: Path, *, freq: int = 880) -> None:
        subprocess.run(
            [
                self.ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"testsrc=size=640x360:rate=30",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={int(freq)}:sample_rate=44100",
                "-t",
                str(int(seconds)),
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(mp4_path),
            ],
            check=True,
            timeout=max(60, int(seconds) * 3),
        )

    def _make_wav_tone(self, seconds: int, wav_path: Path, *, freq: int = 440) -> None:
        subprocess.run(
            [
                self.ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={int(freq)}:sample_rate=44100",
                "-t",
                str(int(seconds)),
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(wav_path),
            ],
            check=True,
            timeout=max(30, int(seconds) * 3),
        )

    def _mean_volume_db(self, path: Path) -> float:
        cp = subprocess.run(
            [
                self.ffmpeg,
                "-i",
                str(path),
                "-af",
                "volumedetect",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        text = (cp.stderr or "") + (cp.stdout or "")
        for line in text.splitlines():
            if "mean_volume:" in line:
                raw = line.split("mean_volume:", 1)[1].strip().split()[0]
                return float(raw)
        self.fail(f"volumedetect não retornou mean_volume:\n{text}")
        return 0.0

    def test_remux_preserves_existing_audio(self) -> None:
        from aibox.recording import _has_audio_stream, _remux_compatible_mp4

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            src = td / "src_tone.mp4"
            dst = td / "dst_tone.mp4"
            self._make_mp4_with_tone(seconds=3, mp4_path=src, freq=880)
            self.assertTrue(_has_audio_stream(src))
            ok, msg = _remux_compatible_mp4(self.ffmpeg, src, dst)
            self.assertTrue(ok, msg)
            self.assertTrue(_has_audio_stream(dst))
            self.assertGreater(self._mean_volume_db(dst), -45.0)

    def test_mux_wav_into_silent_mp4(self) -> None:
        from aibox.recording import _convert_h264_to_mp4, _has_audio_stream, mux_audio_into_mp4

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            raw = td / "in.h264"
            silent = td / "silent.mp4"
            wav = td / "mic.wav"
            muxed = td / "muxed.mp4"
            self._make_raw_h264(seconds=3, fps=30, raw_path=raw)
            ok, msg = _convert_h264_to_mp4(self.ffmpeg, raw, silent, fps=30, expected_dur_s=3.0)
            self.assertTrue(ok, msg)
            self._make_wav_tone(seconds=3, wav_path=wav, freq=440)
            ok, msg = mux_audio_into_mp4(self.ffmpeg, silent, wav, muxed)
            self.assertTrue(ok, msg)
            self.assertTrue(_has_audio_stream(muxed))
            d = _ffprobe_duration_seconds(self.ffprobe, muxed)
            self.assertLess(abs(d - 3.0), 0.5)
            self.assertGreater(self._mean_volume_db(muxed), -45.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

