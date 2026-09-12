#!/usr/bin/env python3
"""Exercise payload checks against real PE files from the pinned bootstrap."""
import contextlib
import io
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from validate_windows import MACHINES, validate

BOOTSTRAP = Path(sys.argv.pop(1)).resolve()


class WindowsPayloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "bin").mkdir()
        self.target = "aarch64-w64-mingw32"
        self.populate(self.target)

    def populate(self, target):
        for path in (self.root / "bin").iterdir():
            path.unlink()
        for path in (BOOTSTRAP / target / "bin").glob("*.dll"):
            shutil.copy2(path, self.root / "bin" / path.name)
        (self.root / "LLGO-LLVM-MANIFEST.txt").write_text(f"host_target={target}\n")

    def test_native_architectures(self):
        for target in MACHINES:
            with self.subTest(target=target), contextlib.redirect_stdout(io.StringIO()):
                self.populate(target)
                validate(self.root, target)

    def test_mixed_architecture(self):
        shutil.copy2(BOOTSTRAP / "x86_64-w64-mingw32/bin/libunwind.dll", self.root / "bin/libunwind.dll")
        with self.assertRaisesRegex(ValueError, "PE machine"):
            validate(self.root, self.target)

    def test_missing_runtime(self):
        (self.root / "bin/libunwind.dll").unlink()
        with self.assertRaisesRegex(ValueError, "unbundled non-system dependency libunwind.dll"):
            validate(self.root, self.target)

    def test_wrong_manifest(self):
        with self.assertRaisesRegex(ValueError, "manifest host_target"):
            validate(self.root, "x86_64-w64-mingw32")

    def test_non_windows_runner(self):
        with self.assertRaisesRegex(ValueError, "native validation requires Windows"):
            validate(self.root, self.target, native=True)


if __name__ == "__main__":
    unittest.main()
