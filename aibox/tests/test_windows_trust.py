from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from installer.versioninfo import version_tuple, write_version_file
from installer.windows_trust import unblock_file, unblock_tree


class TestWindowsTrust(unittest.TestCase):
    def test_version_tuple(self) -> None:
        self.assertEqual(version_tuple("1.0.0"), (1, 0, 0, 0))
        self.assertEqual(version_tuple("v2.3.4"), (2, 3, 4, 0))

    def test_write_version_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "ver.txt"
            write_version_file(dest, version="1.0.0", filename="Aibox.exe")
            text = dest.read_text(encoding="utf-8")
            self.assertIn("Intelite", text)
            self.assertIn("Aibox.exe", text)
            self.assertIn("filevers=(1, 0, 0, 0)", text)

    def test_unblock_removes_zone_identifier(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.exe"
            path.write_bytes(b"mz")
            ads = f"{path}:Zone.Identifier"
            with open(ads, "w", encoding="utf-8") as fh:
                fh.write("[ZoneTransfer]\nZoneId=3\n")
            self.assertTrue(Path(tmp).exists())
            unblock_file(path)
            try:
                with open(ads, encoding="utf-8"):
                    still_there = True
            except OSError:
                still_there = False
            self.assertFalse(still_there)
            unblock_tree(Path(tmp))
