from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from aibox.updater import (
    UpdateError,
    check_for_updates,
    compare_semver,
    is_newer,
    parse_manifest,
    parse_semver,
    sha256_file,
    verify_sha256,
)
from aibox.update_helper import _extract_onedir
from unittest import mock
import urllib.error


class TestSemver(unittest.TestCase):
    def test_parse(self) -> None:
        self.assertEqual(parse_semver("1.2.3"), (1, 2, 3, ""))
        self.assertEqual(parse_semver("v1.0.0"), (1, 0, 0, ""))

    def test_compare(self) -> None:
        self.assertEqual(compare_semver("1.0.0", "1.0.0"), 0)
        self.assertLess(compare_semver("1.0.0", "1.0.1"), 0)
        self.assertGreater(compare_semver("1.1.0", "1.0.9"), 0)
        self.assertTrue(is_newer("1.2.0", "1.0.0"))
        self.assertFalse(is_newer("1.0.0", "1.0.0"))

    def test_invalid(self) -> None:
        with self.assertRaises(UpdateError):
            parse_semver("abc")


class TestCheckUpdates(unittest.TestCase):
    def test_http_404_means_up_to_date(self) -> None:
        err = urllib.error.HTTPError(
            url="https://example.com/latest.json",
            code=404,
            msg="Not Found",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )
        with mock.patch("aibox.github_fetch.urllib.request.urlopen", side_effect=err):
            result = check_for_updates(current_version="1.0.0")
        self.assertFalse(result.update_available)
        self.assertEqual(result.current_version, "1.0.0")
        self.assertEqual(result.remote.version, "1.0.0")

    def test_http_429_is_not_up_to_date(self) -> None:
        err = urllib.error.HTTPError(
            url="https://raw.githubusercontent.com/ChavesSD/ReleasesAibox/main/latest.json",
            code=429,
            msg="Too Many Requests",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )
        with mock.patch("aibox.github_fetch.urllib.request.urlopen", side_effect=err):
            with mock.patch("aibox.github_fetch.time.sleep"):
                with self.assertRaises(UpdateError) as ctx:
                    check_for_updates(current_version="1.0.0")
        self.assertIn("limitou", str(ctx.exception).lower())


class TestManifest(unittest.TestCase):
    def test_parse_manifest(self) -> None:
        data = {
            "version": "1.2.0",
            "notes": {"added": ["A"], "fixed": ["B"], "removed": []},
            "asset": {
                "name": "Aibox-windows-x64-1.2.0.zip",
                "url": "https://example.com/a.zip",
                "sha256": "abc",
                "size": 10,
            },
        }
        m = parse_manifest(data)
        self.assertEqual(m.version, "1.2.0")
        self.assertEqual(m.notes.added, ["A"])
        self.assertEqual(m.asset.url, "https://example.com/a.zip")


class TestApksManifest(unittest.TestCase):
    def test_parse_apks_manifest(self) -> None:
        from aibox.apk_sync import parse_apks_manifest, plan_apk_sync

        data = {
            "version": "1.0.0",
            "updated_at": "2026-08-13",
            "apks": [
                {
                    "category": "Totem",
                    "filename": "Upzz.apk",
                    "label": "Upzz",
                    "sha256": "abc",
                    "size": 10,
                    "url": "https://example.com/Totem-Upzz.apk",
                }
            ],
        }
        m = parse_apks_manifest(data)
        self.assertEqual(m.version, "1.0.0")
        self.assertEqual(len(m.apks), 1)
        self.assertEqual(m.apks[0].category, "Totem")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = plan_apk_sync(m, root=root)
        self.assertEqual(plan[0].status, "missing")

    def test_releases_mirror_urls(self) -> None:
        from aibox.apk_sync import releases_mirror_urls

        url = "https://raw.githubusercontent.com/ChavesSD/ReleasesAibox/main/apks.json"
        mirrors = releases_mirror_urls(url)
        self.assertEqual(mirrors[0], url)
        self.assertIn(
            "https://cdn.jsdelivr.net/gh/ChavesSD/ReleasesAibox@main/apks.json",
            mirrors,
        )
        self.assertIn(
            "https://github.com/ChavesSD/ReleasesAibox/raw/refs/heads/main/apks.json",
            mirrors,
        )

    def test_latest_json_has_mirrors(self) -> None:
        from aibox.github_fetch import releases_mirror_urls

        url = "https://raw.githubusercontent.com/ChavesSD/ReleasesAibox/main/latest.json"
        mirrors = releases_mirror_urls(url)
        self.assertIn(
            "https://cdn.jsdelivr.net/gh/ChavesSD/ReleasesAibox@main/latest.json",
            mirrors,
        )

    def test_parse_repo_asset_name(self) -> None:
        from aibox.apk_sync import parse_repo_asset_name

        self.assertEqual(parse_repo_asset_name("Totem-Upzz.apk"), ("Totem", "Upzz.apk"))
        self.assertEqual(
            parse_repo_asset_name("Totem-Ai_Horizontal.apk"),
            ("Totem", "Ai_Horizontal.apk"),
        )
        self.assertEqual(
            parse_repo_asset_name("Outros-ADB_Wifi.apk"),
            ("Outros", "ADB_Wifi.apk"),
        )
        self.assertIsNone(parse_repo_asset_name("Upzz.apk"))

    def test_empty_sha256_keeps_existing_file(self) -> None:
        from aibox.apk_sync import ApksManifest, RemoteApk, plan_apk_sync

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = root / "Totem" / "Upzz.apk"
            local.parent.mkdir(parents=True)
            local.write_bytes(b"apk")
            manifest = ApksManifest(
                version="1.0.0",
                updated_at="",
                apks=(
                    RemoteApk(
                        category="Totem",
                        filename="Upzz.apk",
                        label="Upzz",
                        sha256="",
                        size=3,
                        url="https://example.com/Totem-Upzz.apk",
                    ),
                ),
            )
            plan = plan_apk_sync(manifest, root=root)
        self.assertEqual(plan[0].status, "current")

    def test_http_get_429_then_404_is_not_missing(self) -> None:
        from aibox.apk_sync import ApksManifestNotFound, UpdateError, _http_get

        def fake_urlopen(req, timeout=None):
            url = getattr(req, "full_url", str(req))
            code = 429 if "raw.githubusercontent.com" in url else 404
            raise urllib.error.HTTPError(url, code, "err", hdrs=None, fp=None)  # type: ignore[arg-type]

        with mock.patch("aibox.github_fetch.urllib.request.urlopen", side_effect=fake_urlopen):
            with mock.patch("aibox.github_fetch.time.sleep"):
                with self.assertRaises(UpdateError) as ctx:
                    _http_get(
                        "https://raw.githubusercontent.com/ChavesSD/ReleasesAibox/main/apks.json",
                        timeout_s=1,
                        accept="application/json",
                    )
        self.assertNotIsInstance(ctx.exception, ApksManifestNotFound)
        self.assertIn("limitou", str(ctx.exception).lower())


class TestChecksumAndExtract(unittest.TestCase):
    def test_sha_and_extract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = root / "payload"
            payload.mkdir()
            (payload / "Aibox.exe").write_bytes(b"fake-exe")
            (payload / "_internal").mkdir()
            (payload / "_internal" / "x.txt").write_text("ok", encoding="utf-8")

            zpath = root / "pkg.zip"
            with zipfile.ZipFile(zpath, "w") as zf:
                for p in payload.rglob("*"):
                    if p.is_file():
                        zf.write(p, arcname=str(Path("Aibox") / p.relative_to(payload)))

            digest = sha256_file(zpath)
            verify_sha256(zpath, digest)
            with self.assertRaises(UpdateError):
                verify_sha256(zpath, "0" * 64)

            install = root / "install"
            install.mkdir()
            (install / "Aibox.exe").write_bytes(b"old")
            _extract_onedir(zpath, install)
            self.assertTrue((install / "Aibox.exe").exists())
            self.assertEqual((install / "Aibox.exe").read_bytes(), b"fake-exe")
            self.assertTrue((install / "_internal" / "x.txt").exists())


if __name__ == "__main__":
    unittest.main()
