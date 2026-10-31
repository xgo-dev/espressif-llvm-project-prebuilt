#!/usr/bin/env python3
"""Test runtime/SDK separation and preservation of loader names and resources."""
import contextlib
import io
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

from package_unix import prune, validate_dependencies, validate_layout


class UnixPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ESP runtime 空格 ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "esp-clang"
        self.files = {
            "bin/clang": b"compiler", "bin/llvm-exegesis": b"large benchmark",
            "bin/ld.lld": b"linker", "include/llvm/IR/Module.h": b"SDK header",
            "include/llvm-c/Core.h": b"C SDK", "include/clang/AST/Expr.h": b"C++ SDK",
            "include/clang-c/Index.h": b"Clang C SDK",
            "include/c++/v1/vector": b"C++ standard library header",
            "lib/libLLVMCore.a": b"SDK static library",
            "lib/libclangAST.a": b"Clang static library",
            "lib/cmake/llvm/LLVMConfig.cmake": b"SDK CMake",
            "lib/pkgconfig/llvm.pc": b"SDK pkg-config",
            "lib/libLLVM.dylib": b"shared LLVM", "lib/libLLVM-22.dylib": b"shared LLVM",
            "lib/libclang-cpp.dylib": b"different library",
            "lib/libc++.a": b"C++ runtime",
            "lib/clang/22/include/stddef.h": b"builtin header",
            "lib/clang/22/lib/linux/libclang_rt.builtins.a": b"target runtime",
            "THIRD-PARTY-LICENSES.txt": b"licenses",
            "third-party-licenses/LLVM-LICENSE.txt": b"complete LLVM license",
        }
        for name, content in self.files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (self.root / "bin/clang++").symlink_to("clang")

    def prune(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return prune(self.root)

    def test_removes_sdk_and_benchmark_but_preserves_runtime_and_licenses(self):
        self.prune()
        for name in ["include/c++/v1/vector", "lib/libc++.a", "lib/clang/22/include/stddef.h",
                     "lib/clang/22/lib/linux/libclang_rt.builtins.a", "bin/ld.lld",
                     "third-party-licenses/LLVM-LICENSE.txt", "THIRD-PARTY-LICENSES.txt"]:
            self.assertEqual((self.root / name).read_bytes(), self.files[name])
        for name in ["include/llvm", "include/llvm-c", "include/clang", "include/clang-c",
                     "lib/cmake", "lib/pkgconfig", "lib/libLLVMCore.a", "lib/libclangAST.a",
                     "bin/llvm-exegesis"]:
            self.assertFalse((self.root / name).exists(), name)
        self.assertTrue((self.root / "bin/clang++").is_symlink())
        validate_layout(self.root)

    def test_identical_dylibs_keep_both_loader_names_as_hard_links(self):
        self.assertEqual(self.prune(), len(b"shared LLVM"))
        self.assertTrue((self.root / "lib/libLLVM.dylib").samefile(self.root / "lib/libLLVM-22.dylib"))
        self.assertFalse((self.root / "lib/libLLVM.dylib").samefile(self.root / "lib/libclang-cpp.dylib"))
        self.assertEqual(self.prune(), 0)

    def test_archive_restores_hard_links_and_clang_alias(self):
        self.prune()
        archive = self.root.parent / "runtime.tar"
        subprocess.run(["tar", "-cf", str(archive), "-C", str(self.root.parent), "esp-clang"],
                       env=dict(os.environ, COPYFILE_DISABLE="1"), check=True)
        with tarfile.open(archive) as stream:
            self.assertTrue(any(member.islnk() for member in stream.getmembers()))
        destination = self.root.parent / "relocated 空格"
        destination.mkdir()
        subprocess.run(["tar", "-xf", str(archive), "-C", str(destination)], check=True)
        root = destination / "esp-clang"
        validate_layout(root)
        self.assertEqual((root / "bin/clang++").read_bytes(), b"compiler")

    def test_different_permissions_are_not_deduplicated(self):
        first = self.root / "lib/libLLVM.dylib"
        second = self.root / "lib/libLLVM-22.dylib"
        first.chmod(0o755)
        second.chmod(0o644)
        self.prune()
        self.assertFalse(first.samefile(second))

    def test_check_rejects_development_files(self):
        with self.assertRaisesRegex(ValueError, "development directory"):
            validate_layout(self.root)

    def test_check_rejects_broken_and_external_symlinks(self):
        self.prune()
        alias = self.root / "bin/broken"
        alias.symlink_to("missing")
        with self.assertRaisesRegex(ValueError, "broken or external"):
            validate_layout(self.root)
        alias.unlink()
        alias.symlink_to(self.root.parent)
        with self.assertRaisesRegex(ValueError, "broken or external"):
            validate_layout(self.root)

    def test_failed_hard_link_preserves_the_original_library(self):
        with mock.patch("package_unix.os.link", side_effect=OSError("unsupported")):
            with self.assertRaisesRegex(OSError, "unsupported"):
                self.prune()
        for name in ["lib/libLLVM.dylib", "lib/libLLVM-22.dylib"]:
            self.assertEqual((self.root / name).read_bytes(), b"shared LLVM")

    def test_real_shared_library_dependency_must_be_bundled(self):
        cc = shutil.which("cc")
        darwin = platform.system() == "Darwin"
        if not cc or (not darwin and not shutil.which("readelf")):
            self.skipTest("native C compiler/readelf required")
        root = self.root.parent / "dependency fixture"
        (root / "bin").mkdir(parents=True)
        (root / "lib").mkdir()
        library = root / ("lib/libFixture.dylib" if darwin else "lib/libFixture.so.1")
        source = root / "provider.c"
        source.write_text("int provider(void) { return 42; }\n")
        main = root / "main.c"
        main.write_text("extern int provider(void); int main(void) { return provider() != 42; }\n")
        flags = (["-dynamiclib", "-Wl,-install_name,@rpath/libFixture.dylib"] if darwin else
                 ["-shared", "-fPIC", "-Wl,-soname,libFixture.so.1"])
        subprocess.run([cc, *flags, str(source), "-o", str(library)], check=True)
        if not darwin:
            (root / "lib/libFixture.so").write_text("/* GNU ld script */\nINPUT ( libFixture.so.1 )\n")
        rpath = "@loader_path/../lib" if darwin else "$ORIGIN/../lib"
        subprocess.run([cc, str(main), "-L" + str(root / "lib"), "-lFixture",
                        "-Wl,-rpath," + rpath, "-o", str(root / "bin/consumer")], check=True)
        arch = "aarch64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
        target = arch + ("-apple-darwin" if darwin else "-linux-gnu")
        validate_dependencies(root, target)
        library.unlink()
        with self.assertRaisesRegex(ValueError, "unbundled shared library"):
            validate_dependencies(root, target)


if __name__ == "__main__":
    unittest.main()
