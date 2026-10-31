#!/usr/bin/env python3
"""Check that Windows tool deduplication preserves contents and tar hard links."""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

from deduplicate_windows_tools import deduplicate


class WindowsPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ESP packaging 空格 ")
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)
        self.root = self.parent / "esp-clang"
        self.bin = self.root / "bin"
        self.bin.mkdir(parents=True)
        self.tools = {
            "clang.exe": b"MZ clang contents",
            "clang++.exe": b"MZ clang contents",
            "clang-cpp.exe": b"MZ clang contents",
            "clang-as.exe": b"MZ clang contents",
            "xtensa-esp32-elf-clang-as.exe": b"MZ clang contents",
            "xtensa-esp8266-elf-clang-as.exe": b"MZ clang contents",
            "lld.exe": b"MZ linker contents",
            "ld.lld.exe": b"MZ linker contents",
            "different.exe": b"MZ CLANG contents",  # Equal size, different bytes.
            "libclang.dll": b"MZ clang contents",  # Only executable aliases.
        }
        for name, contents in self.tools.items():
            (self.bin / name).write_bytes(contents)
        (self.root / "LICENSE.txt").write_text("license\n")

    def pack(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return deduplicate(self.root)

    def test_all_paths_and_contents_preserved(self):
        count, saved = self.pack()
        self.assertEqual(count, 6)
        self.assertEqual(saved, 5 * len(self.tools["clang.exe"]) + len(self.tools["lld.exe"]))
        self.assertEqual({p.name for p in self.bin.iterdir()}, set(self.tools))
        for name, contents in self.tools.items():
            self.assertEqual((self.bin / name).read_bytes(), contents)
            self.assertFalse((self.bin / name).is_symlink())
        self.assertTrue((self.bin / "clang.exe").samefile(self.bin / "clang++.exe"))
        self.assertTrue((self.bin / "lld.exe").samefile(self.bin / "ld.lld.exe"))
        self.assertFalse((self.bin / "clang.exe").samefile(self.bin / "different.exe"))
        self.assertFalse((self.bin / "clang.exe").samefile(self.bin / "libclang.dll"))
        self.assertEqual(self.pack(), (0, 0))
        self.assertEqual(deduplicate(self.root, check=True), (0, 0))

    def test_archive_and_extraction_preserve_hard_links(self):
        self.pack()
        archive = self.parent / "tools.tar"
        subprocess.run(["tar", "-cf", str(archive), "-C", str(self.parent), "esp-clang"],
                       env=dict(os.environ, COPYFILE_DISABLE="1"), check=True)
        with tarfile.open(archive) as stream:
            members = stream.getmembers()
            self.assertEqual(sum(member.islnk() for member in members), 6)
            self.assertFalse(any(member.issym() for member in members))
        destination = self.parent / "extracted"
        destination.mkdir()
        subprocess.run(["tar", "-xf", str(archive), "-C", str(destination)], check=True)
        root = destination / "esp-clang"
        deduplicate(root, check=True)
        for name, contents in self.tools.items():
            self.assertEqual((root / "bin" / name).read_bytes(), contents)
        self.assertEqual((root / "LICENSE.txt").read_text(), "license\n")

    def test_check_rejects_flattened_duplicates(self):
        with self.assertRaisesRegex(ValueError, "not a hard link"):
            deduplicate(self.root, check=True)

    def test_failed_link_preserves_original_files(self):
        with mock.patch("deduplicate_windows_tools.os.link", side_effect=OSError("unsupported")):
            with self.assertRaisesRegex(OSError, "unsupported"):
                self.pack()
        self.assertEqual({p.name for p in self.bin.iterdir()}, set(self.tools))
        for name, contents in self.tools.items():
            self.assertEqual((self.bin / name).read_bytes(), contents)

    @unittest.skipIf(os.name == "nt", "Windows does not expose POSIX executable permission bits")
    def test_different_permissions_are_preserved(self):
        alias = self.bin / "clang++.exe"
        alias.chmod(0o600)
        (self.bin / "clang.exe").chmod(0o755)
        self.pack()
        self.assertFalse(alias.samefile(self.bin / "clang.exe"))
        self.assertEqual(alias.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.bin / "clang.exe").stat().st_mode & 0o777, 0o755)

    @unittest.skipIf(os.name == "nt", "Creating test symlinks needs Windows developer privileges")
    def test_symlinks_are_unchanged(self):
        alias = self.bin / "symlink.exe"
        alias.symlink_to("clang.exe")
        self.pack()
        self.assertTrue(alias.is_symlink())
        self.assertEqual(os.readlink(alias), "clang.exe")


if __name__ == "__main__":
    unittest.main()
