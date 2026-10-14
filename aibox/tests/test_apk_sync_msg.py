from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aibox.apk_sync import ApksManifest, RemoteApk, friendly_sync_error, sync_apks
from aibox.updater import UpdateError


class _Resp(io.BytesIO):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestFriendlySyncError(unittest.TestCase):
    def test_checksum_error_explains_stale_catalog(self) -> None:
        raw = UpdateError("Checksum inválido.\nEsperado: bff7db69\nObtido: e00d25fe")
        msg = friendly_sync_error(raw)
        self.assertIn("apks.json desatualizado", msg)
        self.assertIn("publish_apks.py", msg)
        self.assertNotIn("Esperado:", msg)  # nada de hashes crus para o técnico

    def test_other_errors_are_unchanged(self) -> None:
        self.assertEqual(friendly_sync_error(UpdateError("Timeout no download")), "Timeout no download")
        self.assertEqual(friendly_sync_error(None), "")

    def test_sync_reports_friendly_message_and_keeps_old_file(self) -> None:
        good = b"APK-NOVO"
        manifest = ApksManifest(
            "1.0.3",
            "",
            (
                RemoteApk(
                    "Totem", "Upzz.apk", "Upzz", hashlib.sha256(b"outro conteudo").hexdigest(), len(good),
                    "https://raw.githubusercontent.com/ChavesSD/ReleasesAibox/main/apk_assets/Totem-Upzz.apk",
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "Totem" / "Upzz.apk"
            old.parent.mkdir(parents=True)
            old.write_bytes(b"VERSAO ANTIGA BOA")
            with mock.patch("aibox.github_fetch.urllib.request.urlopen", side_effect=lambda r, timeout=None: _Resp(good)), \
                    mock.patch("aibox.apk_sync._github_api_file", return_value=good), \
                    mock.patch("aibox.github_fetch.time.sleep"):
                res = sync_apks(root=root, manifest=manifest)
            self.assertEqual(len(res.failed), 1)
            self.assertIn("apks.json desatualizado", res.failed[0])
            self.assertEqual(old.read_bytes(), b"VERSAO ANTIGA BOA")


if __name__ == "__main__":
    unittest.main(verbosity=2)
