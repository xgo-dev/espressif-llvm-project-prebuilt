#!/usr/bin/env python3
"""Exercise payload checks against real PE files from the pinned bootstrap."""
import contextlib
import io
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

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

    def shared_fixture(self):
        manifest = self.root / "LLGO-LLVM-MANIFEST.txt"
        manifest.write_text(f"host_target={self.target}\nwindows_llvm_link_mode=shared\n")
        self.shared_tools = {"clang.exe", "ld.lld.exe", "llc.exe", "opt.exe",
                             "llvm-ar.exe", "llvm-objcopy.exe", "llvm-readobj.exe"}
        for name in self.shared_tools | {"libLLVM.dll"}:
            shutil.copy2(BOOTSTRAP / self.target / "bin/libunwind.dll", self.root / "bin" / name)

    def test_shared_imports_are_required(self):
        self.shared_fixture()
        def inspect(path):
            return MACHINES[self.target], {"libllvm.dll"} if path.name in self.shared_tools else set()
        with mock.patch("validate_windows.inspect_pe", side_effect=inspect), \
                contextlib.redirect_stdout(io.StringIO()):
            validate(self.root, self.target, require_shared_llvm=True)
        for static_tool in self.shared_tools:
            def inspect_static(path):
                machine, imports = inspect(path)
                return machine, set() if path.name == static_tool else imports
            with self.subTest(static_tool=static_tool), \
                    mock.patch("validate_windows.inspect_pe", side_effect=inspect_static):
                with self.assertRaisesRegex(ValueError, "does not import.*shared LLVM DLL"):
                    validate(self.root, self.target, require_shared_llvm=True)

    def test_shared_dll_is_required(self):
        self.shared_fixture()
        (self.root / "bin/libLLVM.dll").unlink()
        with self.assertRaisesRegex(ValueError, "shared LLVM DLL is missing"):
            validate(self.root, self.target, require_shared_llvm=True)

    def test_static_sdk_remains_independent(self):
        with contextlib.redirect_stdout(io.StringIO()):
            validate(self.root, self.target)
        with self.assertRaisesRegex(ValueError, "link_mode is not shared"):
            validate(self.root, self.target, require_shared_llvm=True)

    def test_non_windows_runner(self):
        with self.assertRaisesRegex(ValueError, "native validation requires Windows"):
            validate(self.root, self.target, native=True)

    def test_sdk_manifest_version(self):
        manifest = self.root / "LLGO-LLVM-MANIFEST.txt"
        with self.assertRaisesRegex(ValueError, "manifest llvm_version is None"):
            validate(self.root, self.target, llvm_version="22.1.8")
        manifest.write_text(f"host_target={self.target}\nllvm_version=22.1.4\n")
        with self.assertRaisesRegex(ValueError, "expected 22.1.8"):
            validate(self.root, self.target, llvm_version="22.1.8")
        manifest.write_text(f"host_target={self.target}\nllvm_version=22.1.8\n")
        with contextlib.redirect_stdout(io.StringIO()):
            validate(self.root, self.target, llvm_version="22.1.8")

    def test_i686_native_host(self):
        target = "i686-w64-mingw32"
        self.populate(target)
        for host in ("X86", "AMD64"):
            with self.subTest(host=host), mock.patch("validate_windows.os.name", "nt"), \
                    mock.patch.dict("os.environ", {"PROCESSOR_ARCHITECTURE": host}, clear=True), \
                    contextlib.redirect_stdout(io.StringIO()):
                validate(self.root, target, native=True)
        with mock.patch("validate_windows.os.name", "nt"), \
                mock.patch.dict("os.environ", {"PROCESSOR_ARCHITECTURE": "ARM64"}, clear=True):
            with self.assertRaisesRegex(ValueError, "requires Windows AMD64, X86, got nt/ARM64"):
                validate(self.root, target, native=True)


if __name__ == "__main__":
    unittest.main()
