#!/usr/bin/env python3
"""Exercise SDK source recovery and build orchestration without building LLVM."""
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parent
TARGET = "i686-w64-mingw32"


class WindowsSDKBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "scripts").mkdir()
        for name in ("build_windows_sdk.sh", "validate_windows.py"):
            shutil.copy2(SCRIPTS / name, self.root / "scripts" / name)
        self.upstream = self.root / "upstream"
        self.upstream.mkdir()
        self.git(self.upstream, "init", "-q")
        (self.upstream / "llvm").mkdir()
        (self.upstream / "llvm/CMakeLists.txt").write_text("# fixture source\n")
        (self.upstream / "LICENSE.TXT").write_text("LLVM fixture license\n")
        self.git(self.upstream, "add", ".")
        self.git(self.upstream, "-c", "user.name=SDK Test", "-c", "user.email=sdk@example.invalid",
                 "commit", "-qm", "fixture source")
        self.revision = self.git(self.upstream, "rev-parse", "HEAD").strip()
        (self.root / "toolchain.env").write_text(
            "LLVM_WINDOWS_SDK_REPOSITORY=https://github.com/llvm/llvm-project.git\n"
            f"LLVM_WINDOWS_SDK_REF={self.revision}\nLLVM_WINDOWS_SDK_VERSION=99.4.2\nLLVM_MINGW_VERSION=fixture\n")
        self.source = self.root / "source"
        self.bootstrap = self.root / "bootstrap with spaces"
        self.tools = self.bootstrap / "bin"
        self.tools.mkdir(parents=True)
        for tool in ("clang", "clang++", "windres"):
            self.executable(self.tools / f"{TARGET}-{tool}", "#!/bin/sh\nexit 0\n")
        self.executable(self.tools / "ccache", "#!/bin/sh\nexit 0\n")
        self.executable(self.tools / "getconf", "#!/bin/sh\nprintf '5\\n'\n")
        self.log = self.root / "cmake.jsonl"
        self.executable(self.tools / "cmake", """#!/usr/bin/env python3
import json, os, pathlib, sys
with open(os.environ['SDK_TEST_CMAKE_LOG'], 'a') as output:
    output.write(json.dumps(sys.argv[1:]) + '\\n')
for arg in sys.argv[1:]:
    if arg.startswith('-DCMAKE_INSTALL_PREFIX='):
        (pathlib.Path(arg.split('=', 1)[1]) / 'bin').mkdir(parents=True, exist_ok=True)
""")
        (self.bootstrap / "LICENSE.TXT").write_text("bootstrap fixture license\n")
        self.dll = self.bootstrap / TARGET / "bin/runtime.dll"
        self.dll.parent.mkdir(parents=True)
        # A minimal PE32 parser fixture. Real bootstrap PE/DLL validation and
        # executable/API smoke tests run separately in the workflow.
        pe = bytearray(0x80 + 24 + 224)
        pe[:2] = b"MZ"
        struct.pack_into("<I", pe, 0x3C, 0x80)
        pe[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<HH", pe, 0x84, 0x14C, 0)
        struct.pack_into("<H", pe, 0x80 + 20, 224)
        struct.pack_into("<H", pe, 0x80 + 24, 0x10B)
        self.dll.write_bytes(pe)
        self.notice = self.bootstrap / TARGET / "share/mingw32/COPYING.fixture"
        self.notice.parent.mkdir(parents=True)
        self.notice.write_text("runtime fixture license\n")
        self.env = dict(os.environ, PATH=str(self.tools) + os.pathsep + os.environ["PATH"],
                        LLVM_SDK_SOURCE_DIR=str(self.source), LLVM_SDK_BUILD_DIR=str(self.root / "build"),
                        SDK_TEST_CMAKE_LOG=str(self.log), REQUIRE_CCACHE="1",
                        GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0=f"url.{self.upstream.as_uri()}.insteadOf",
                        GIT_CONFIG_VALUE_0="https://github.com/llvm/llvm-project.git")
        for name in ("LLVM_SDK_JOBS", "LLVM_SDK_LINK_JOBS"):
            self.env.pop(name, None)

    @staticmethod
    def executable(path, text):
        path.write_text(text)
        path.chmod(0o755)

    @staticmethod
    def git(path, *args):
        return subprocess.check_output(["git", "-C", str(path), *args], text=True, stderr=subprocess.STDOUT)

    def run_builder(self, error=None):
        result = subprocess.run(["bash", str(self.root / "scripts/build_windows_sdk.sh")],
                                env=self.env, text=True, capture_output=True)
        if error is None:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.git(self.source, "rev-parse", "HEAD").strip(), self.revision)
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(error, result.stderr)
            self.assertFalse(self.log.exists(), "failure reached the expensive CMake build")
        return result

    def test_fresh_and_interrupted_source_checkouts(self):
        for state in ("fresh", "initialized", "fetched"):
            with self.subTest(state=state):
                if self.source.exists():
                    shutil.rmtree(self.source)
                if state != "fresh":
                    self.source.mkdir()
                    self.git(self.source, "init", "-q")
                if state == "fetched":
                    self.git(self.source, "fetch", "-q", str(self.upstream), self.revision)
                self.run_builder()

    def test_compiler_cache_and_parallelism_apply_to_both_builds(self):
        self.run_builder()
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        configs = [args for args in calls if "-S" in args]
        self.assertEqual(len(configs), 2)
        for args in configs:
            self.assertIn(f"-DCMAKE_C_COMPILER_LAUNCHER={self.tools / 'ccache'}", args)
            self.assertIn(f"-DCMAKE_CXX_COMPILER_LAUNCHER={self.tools / 'ccache'}", args)
            self.assertIn("-DLLVM_PARALLEL_LINK_JOBS=1", args)
        for args in (args for args in calls if "--build" in args):
            self.assertIn("-j5", args)
        manifest = (self.root / "build/install/llvm-sdk/LLGO-LLVM-MANIFEST.txt").read_text()
        self.assertIn("llvm_version=99.4.2\n", manifest)

    def test_job_override(self):
        self.env.update(LLVM_SDK_JOBS="3", LLVM_SDK_LINK_JOBS="2")
        self.run_builder()
        for args in map(json.loads, self.log.read_text().splitlines()):
            self.assertIn("-j3" if "--build" in args else "-DLLVM_PARALLEL_LINK_JOBS=2", args)

    def test_dirty_source_is_preserved(self):
        self.git(self.root, "clone", "-q", str(self.upstream), str(self.source))
        license = self.source / "LICENSE.TXT"
        license.write_text("local edits\n")
        self.run_builder("is dirty; use a clean source directory")
        self.assertEqual(license.read_text(), "local edits\n")

    def test_clean_source_at_another_revision(self):
        self.git(self.root, "clone", "-q", str(self.upstream), str(self.source))
        (self.source / "extra").write_text("different committed revision\n")
        self.git(self.source, "add", ".")
        self.git(self.source, "-c", "user.name=SDK Test", "-c", "user.email=sdk@example.invalid",
                 "commit", "-qm", "another revision")
        self.run_builder()

    def test_fetch_failure_is_diagnostic(self):
        with (self.root / "toolchain.env").open("a") as config:
            config.write(f'LLVM_WINDOWS_SDK_REPOSITORY="{self.root / "missing-upstream"}"\n')
        self.run_builder(f"could not fetch pinned LLVM revision {self.revision}")

    def test_configured_repository_overrides_stale_origin(self):
        self.source.mkdir()
        self.git(self.source, "init", "-q")
        old_origin = str(self.root / "previous-upstream")
        self.git(self.source, "remote", "add", "origin", old_origin)
        self.run_builder()
        self.assertEqual(self.git(self.source, "remote", "get-url", "origin").strip(), old_origin)

    def test_missing_bootstrap_dlls(self):
        self.dll.unlink()
        self.run_builder("bootstrap runtime DLLs are missing")

    def test_missing_bootstrap_notices(self):
        self.notice.unlink()
        self.run_builder("bootstrap redistribution licenses are missing")

    def test_invalid_parallelism(self):
        self.env["LLVM_SDK_JOBS"] = "0"
        self.run_builder("must be positive integers")


if __name__ == "__main__":
    unittest.main()
