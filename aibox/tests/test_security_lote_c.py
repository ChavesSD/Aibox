from __future__ import annotations

import hashlib
import io
import os
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

from aibox import github_fetch
from aibox.apk_sync import (
    ApksManifest,
    RemoteApk,
    parse_apks_manifest,
    sync_apks,
)
from aibox.updater import (
    ReleaseAsset,
    UpdateError,
    cleanup_update_leftovers,
    download_asset,
    parse_manifest,
)
from aibox.ziputil import UnsafeZipError, safe_extract_zip, validate_zip

GOOD_SHA = "a" * 64
GOOD_URL = "https://github.com/ChavesSD/ReleasesAibox/releases/download/v1.2.0/Aibox-x64-1.2.0.zip"


def _zip_bytes(entries: dict[str, bytes], *, attr: dict[str, int] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            info = zipfile.ZipInfo(name)
            if attr and name in attr:
                info.external_attr = attr[name]
            zf.writestr(info, data)
    return buf.getvalue()


class TestZipUtil(unittest.TestCase):
    def _open(self, entries, **kw) -> zipfile.ZipFile:
        return zipfile.ZipFile(io.BytesIO(_zip_bytes(entries, **kw)))

    def test_accepts_normal_tree(self) -> None:
        with self._open({"Aibox/Aibox.exe": b"x", "Aibox/_internal/a.dll": b"y"}) as zf:
            validate_zip(zf)

    def test_rejects_unsafe_names(self) -> None:
        for bad in ("../evil.exe", "a/../../evil.exe", "/abs.exe", "C:/x.exe", "Aibox.exe:evil",
                    "CON.txt", "nul", "a/aux.dll", "trailing.", "space "):
            with self._open({bad: b"x"}) as zf, self.assertRaises(UnsafeZipError, msg=bad):
                validate_zip(zf)

    def test_rejects_symlink_and_bomb(self) -> None:
        with self._open({"l": b"x"}, attr={"l": 0o120777 << 16}) as zf, self.assertRaises(UnsafeZipError):
            validate_zip(zf)
        with self._open({"a": b"x" * 100}) as zf, self.assertRaises(UnsafeZipError):
            validate_zip(zf, max_total=10)
        with self._open({"a": b"1", "b": b"2"}) as zf, self.assertRaises(UnsafeZipError):
            validate_zip(zf, max_files=1)

    def test_safe_extract_writes_inside_dest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self._open({"d/f.txt": b"ok"}) as zf:
            safe_extract_zip(zf, Path(tmp))
            self.assertEqual((Path(tmp) / "d" / "f.txt").read_bytes(), b"ok")


class TestUrlPolicy(unittest.TestCase):
    def test_allowlist(self) -> None:
        self.assertTrue(github_fetch.is_allowed_url(GOOD_URL))
        self.assertTrue(github_fetch.is_allowed_url("https://objects.githubusercontent.com/x"))
        for bad in ("http://github.com/x", "https://evil.example.com/a.zip", "file:///C:/a.zip",
                    "ftp://github.com/a", "https://github.com.evil.com/a", "", "https://"):
            self.assertFalse(github_fetch.is_allowed_url(bad), bad)

    def test_safe_urlopen_blocks_before_network(self) -> None:
        with mock.patch("aibox.github_fetch.urllib.request.urlopen") as op:
            with self.assertRaises(urllib.error.URLError):
                github_fetch.safe_urlopen(urllib.request.Request("http://github.com/a"), 1)
            op.assert_not_called()

    def test_redirect_to_foreign_host_rejected(self) -> None:
        h = github_fetch._AllowedRedirect()
        req = urllib.request.Request(GOOD_URL)
        with self.assertRaises(urllib.error.URLError):
            h.redirect_request(req, None, 302, "Found", {}, "https://evil.example.com/a.zip")
        with self.assertRaises(urllib.error.URLError):
            h.redirect_request(req, None, 302, "Found", {}, "http://github.com/a.zip")

    def test_manifest_mirrors_exclude_cdn(self) -> None:
        url = "https://raw.githubusercontent.com/ChavesSD/ReleasesAibox/main/latest.json"
        self.assertFalse(any("jsdelivr" in u for u in github_fetch.releases_mirror_urls(url, include_cdn=False)))
        self.assertTrue(any("jsdelivr" in u for u in github_fetch.releases_mirror_urls(url)))

    def test_read_limited(self) -> None:
        with self.assertRaises(urllib.error.URLError):
            github_fetch.read_limited(io.BytesIO(b"x" * 20), 10)
        self.assertEqual(github_fetch.read_limited(io.BytesIO(b"abc"), 10), b"abc")


class TestUpdateManifestStrict(unittest.TestCase):
    def _data(self, **asset) -> dict:
        a = {"name": "Aibox-x64-1.2.0.zip", "url": GOOD_URL, "sha256": GOOD_SHA, "size": 10}
        a.update(asset)
        return {"version": "1.2.0", "asset": a}

    def test_valid(self) -> None:
        self.assertEqual(parse_manifest(self._data()).asset.sha256, GOOD_SHA)

    def test_rejects_bad_assets(self) -> None:
        for patch in (
            {"sha256": ""}, {"sha256": "abc"}, {"sha256": "Z" * 64},
            {"name": "..\\..\\evil.zip"}, {"name": "a/b.zip"}, {"name": "x.exe"}, {"name": "C:\\x.zip"},
            {"url": "http://github.com/a.zip"}, {"url": "https://evil.example.com/a.zip"},
            {"size": -1}, {"size": 10**12},
        ):
            with self.assertRaises(UpdateError, msg=str(patch)):
                parse_manifest(self._data(**patch))


class TestApksManifestStrict(unittest.TestCase):
    def _item(self, **kw) -> dict:
        d = {
            "category": "Totem", "filename": "Upzz.apk", "label": "Upzz", "sha256": GOOD_SHA, "size": 5,
            "url": "https://raw.githubusercontent.com/ChavesSD/ReleasesAibox/main/apk_assets/Totem-Upzz.apk",
        }
        d.update(kw)
        return d

    def test_invalid_entries_dropped(self) -> None:
        m = parse_apks_manifest({"apks": [
            self._item(),
            self._item(filename="..\\..\\evil.apk"),
            self._item(category="C:\\Windows"),
            self._item(category="../Totem"),
            self._item(sha256=""),
            self._item(url="https://evil.example.com/x.apk"),
            self._item(filename="a.bat"),
        ]})
        self.assertEqual([a.filename for a in m.apks], ["Upzz.apk"])

    def test_all_invalid_raises(self) -> None:
        with self.assertRaises(UpdateError):
            parse_apks_manifest({"apks": [self._item(sha256="")]})


class FakeResp(io.BytesIO):
    def __init__(self, data: bytes, length: int | None = None) -> None:
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) if length is None else length)}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestDownloadSafety(unittest.TestCase):
    def _asset(self, data: bytes, size: int | None = None) -> ReleaseAsset:
        return ReleaseAsset(name="a.zip", url=GOOD_URL, sha256=hashlib.sha256(data).hexdigest(),
                            size=len(data) if size is None else size)

    def test_good_download_replaces(self) -> None:
        data = b"novo conteudo"
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "a.zip"
            with mock.patch("aibox.github_fetch.urllib.request.urlopen", side_effect=lambda r, timeout=None: FakeResp(data)):
                download_asset(self._asset(data), dest, expected_sha256=self._asset(data).sha256)
            self.assertEqual(dest.read_bytes(), data)
            self.assertFalse(dest.with_suffix(".zip.part").exists())

    def test_bad_checksum_keeps_old_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "a.zip"
            dest.write_bytes(b"VERSAO BOA ANTIGA")
            with mock.patch("aibox.github_fetch.urllib.request.urlopen", side_effect=lambda r, timeout=None: FakeResp(b"adulterado")):
                with self.assertRaises(UpdateError):
                    download_asset(self._asset(b"outro"), dest, expected_sha256="b" * 64)
            self.assertEqual(dest.read_bytes(), b"VERSAO BOA ANTIGA")
            self.assertFalse(dest.with_suffix(".zip.part").exists())

    def test_stream_over_limit_aborted(self) -> None:
        data = b"x" * 5000
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "a.zip"
            with mock.patch("aibox.github_fetch.urllib.request.urlopen", side_effect=lambda r, timeout=None: FakeResp(data, length=10)):
                with self.assertRaises(UpdateError):
                    download_asset(self._asset(data, size=10), dest, max_bytes=100)
            self.assertFalse(dest.exists())

    def test_foreign_url_never_requested(self) -> None:
        asset = ReleaseAsset(name="a.zip", url="https://evil.example.com/a.zip", sha256=GOOD_SHA, size=1)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("aibox.github_fetch.urllib.request.urlopen") as op:
                with self.assertRaises(UpdateError):
                    download_asset(asset, Path(tmp) / "a.zip", expected_sha256=GOOD_SHA)
                op.assert_not_called()


class TestApkSyncSafety(unittest.TestCase):
    def _manifest(self, sha: str, url: str = "https://raw.githubusercontent.com/ChavesSD/ReleasesAibox/main/apk_assets/Totem-Upzz.apk"):
        return ApksManifest("1", "", (RemoteApk("Totem", "Upzz.apk", "Upzz", sha, 5, url),))

    def test_failed_download_keeps_existing_apk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk = root / "Totem" / "Upzz.apk"
            apk.parent.mkdir(parents=True)
            apk.write_bytes(b"apk antigo bom")
            with mock.patch("aibox.github_fetch.urllib.request.urlopen",
                            side_effect=lambda r, timeout=None: FakeResp(b"corrompido")), \
                    mock.patch("aibox.apk_sync._github_api_file", return_value=b"corrompido"), \
                    mock.patch("aibox.github_fetch.time.sleep"):
                res = sync_apks(root=root, manifest=self._manifest("c" * 64))
            self.assertEqual(len(res.failed), 1)
            self.assertEqual(apk.read_bytes(), b"apk antigo bom")
            self.assertEqual(list((root / "Totem").glob("*.part")), [])

    def test_empty_checksum_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("aibox.github_fetch.urllib.request.urlopen") as op:
                res = sync_apks(root=Path(tmp), manifest=self._manifest(""))
                op.assert_not_called()
            self.assertEqual(res.downloaded, 0)
            self.assertEqual(len(res.failed), 1)

    def test_good_sync_installs(self) -> None:
        data = b"APK-BOM"
        sha = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch("aibox.github_fetch.urllib.request.urlopen", side_effect=lambda r, timeout=None: FakeResp(data)):
                res = sync_apks(root=root, manifest=self._manifest(sha))
            self.assertEqual(res.downloaded, 1, res.failed)
            self.assertEqual((root / "Totem" / "Upzz.apk").read_bytes(), data)


class TestUpdateHelper(unittest.TestCase):
    def test_wrong_sha_aborts_before_extract(self) -> None:
        from aibox import update_helper

        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "p.zip"
            pkg.write_bytes(_zip_bytes({"Aibox/Aibox.exe": b"x"}))
            inst = Path(tmp) / "Aibox"
            inst.mkdir()
            with mock.patch.object(update_helper, "stage_new_onedir") as st:
                rc = update_helper.main(
                    ["--pid", "0", "--install-dir", str(inst), "--package", str(pkg), "--sha256", "d" * 64]
                )
            self.assertEqual(rc, 3)
            st.assert_not_called()

    def test_stage_rejects_malicious_zip(self) -> None:
        from aibox import update_helper

        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "p.zip"
            pkg.write_bytes(_zip_bytes({"Aibox/Aibox.exe": b"x", "Aibox/../../escape.txt": b"y"}))
            inst = Path(tmp) / "Aibox"
            inst.mkdir()
            with self.assertRaises(UnsafeZipError):
                update_helper.stage_new_onedir(pkg, inst)
            self.assertFalse((Path(tmp).parent / "escape.txt").exists())

    def test_swap_script_keeps_backup(self) -> None:
        from aibox import update_helper

        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            s = update_helper._write_swap_script(
                install_dir=t / "Aibox", new_dir=t / "Aibox.new-1", helper_pid=123, restart=True, script_dir=t
            )
            text = s.read_text(encoding="ascii")
            self.assertNotIn("DeleteFolder", text)
            self.assertIn("System32", text)

    def test_cleanup_leftovers_only_old(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            inst = t / "Aibox"
            inst.mkdir()
            old = t / "Aibox.new-1"
            old.mkdir()
            fresh = t / "Aibox.extract-2"
            fresh.mkdir()
            past = time.time() - 3 * 86400
            os.utime(old, (past, past))
            cleanup_update_leftovers(inst)
            self.assertFalse(old.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(inst.exists())


class TestInstallerAtomic(unittest.TestCase):
    def _run_install(self, tmp: Path, payload: bytes, *, fail_copy: bool = False) -> Path:
        from installer import setup_main

        pay = tmp / "payload.zip"
        pay.write_bytes(payload)
        inst = tmp / "Aibox"
        patches = [
            mock.patch.object(setup_main, "INSTALL_DIR", inst),
            mock.patch.object(setup_main, "_payload_path", return_value=pay),
            mock.patch.object(setup_main, "_stop_running_app"),
            mock.patch.object(setup_main, "_create_shortcut"),
            mock.patch.object(setup_main, "_write_uninstaller"),
            mock.patch.object(setup_main, "trust_installed_app"),
        ]
        if fail_copy:
            patches.append(mock.patch.object(setup_main.shutil, "copy2", side_effect=OSError("disco cheio")))
        for p in patches:
            p.start()
        try:
            setup_main.perform_install()
        finally:
            for p in patches:
                p.stop()
        return inst

    def test_install_replaces_and_keeps_capturas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            inst = t / "Aibox"
            (inst / "capturas").mkdir(parents=True)
            (inst / "capturas" / "foto.png").write_bytes(b"png")
            (inst / "velho.txt").write_bytes(b"velho")
            self._run_install(t, _zip_bytes({"Aibox/Aibox.exe": b"EXE-NOVO", "Aibox/lib.dll": b"d"}))
            self.assertEqual((inst / "Aibox.exe").read_bytes(), b"EXE-NOVO")
            self.assertFalse((inst / "velho.txt").exists())
            self.assertEqual((inst / "capturas" / "foto.png").read_bytes(), b"png")
            self.assertFalse((t / "Aibox.installing").exists())
            self.assertFalse((t / "Aibox.old").exists())

    def test_failed_copy_keeps_previous_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            inst = t / "Aibox"
            inst.mkdir()
            (inst / "Aibox.exe").write_bytes(b"EXE-ANTIGO")
            with self.assertRaises(OSError):
                self._run_install(t, _zip_bytes({"Aibox/Aibox.exe": b"EXE-NOVO"}), fail_copy=True)
            self.assertEqual((inst / "Aibox.exe").read_bytes(), b"EXE-ANTIGO")
            self.assertFalse((t / "Aibox.installing").exists())

    def test_malicious_payload_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            with self.assertRaises(UnsafeZipError):
                self._run_install(t, _zip_bytes({"Aibox/Aibox.exe": b"x", "../../x.dll": b"y"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
