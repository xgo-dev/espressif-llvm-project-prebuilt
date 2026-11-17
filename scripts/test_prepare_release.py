#!/usr/bin/env python3
"""Reject incomplete, mixed-version and corrupted release artifact sets."""
from pathlib import Path
import tempfile
import unittest

from prepare_release import digest, expected_archives, prepare


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.artifacts = self.root / "artifacts"
        self.output = self.root / "release"
        self.payload, self.sdk = "23.1.6_20261118", "23.1.6"
        self.files = []
        for i, name in enumerate(sorted(expected_archives(self.payload, self.sdk))):
            path = self.artifacts / str(i) / name
            path.parent.mkdir(parents=True)
            path.write_bytes(name.encode())
            path.with_name(name + ".sha256").write_text(f"{digest(path)}  /build/dist/{name}\n")
            self.files.append(path)

    def assemble(self):
        prepare(self.artifacts, self.output, self.payload, self.sdk)

    def test_complete_release(self):
        self.assemble()
        self.assertEqual(len(list(self.output.glob("*.tar.xz"))), 8)
        self.assertEqual(len(list(self.output.glob("*.sha256"))), 9)
        lines = (self.output / f"clang-esp-{self.payload}-checksum.sha256").read_text().splitlines()
        self.assertEqual(len(lines), 8)
        self.assertTrue(any("llvm-sdk-" in line for line in lines))

    def test_missing_sdk(self):
        next(p for p in self.files if p.name.startswith("llvm-sdk-")).unlink()
        with self.assertRaisesRegex(ValueError, "missing=.*llvm-sdk"):
            self.assemble()
        self.assertFalse(self.output.exists())

    def test_missing_host(self):
        self.files[0].unlink()
        with self.assertRaisesRegex(ValueError, "incomplete release"):
            self.assemble()

    def test_duplicate_archive(self):
        (self.artifacts / self.files[0].name).write_bytes(b"duplicate")
        with self.assertRaisesRegex(ValueError, "duplicate archive"):
            self.assemble()

    def test_unexpected_version(self):
        (self.artifacts / "llvm-sdk-22.1.8-i686-w64-mingw32.tar.xz").write_bytes(b"old")
        with self.assertRaisesRegex(ValueError, "unexpected=.*22.1.8"):
            self.assemble()

    def test_corrupt_archive(self):
        self.files[0].write_bytes(b"corrupted")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.assemble()
        self.assertFalse(self.output.exists())

    def test_missing_checksum(self):
        self.files[0].with_name(self.files[0].name + ".sha256").unlink()
        with self.assertRaises(FileNotFoundError):
            self.assemble()
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
