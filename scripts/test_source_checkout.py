#!/usr/bin/env python3
"""Ensure the actual release downloader preserves tags for package tests."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class SourceCheckoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.upstream = self.root / "upstream"
        self.upstream.mkdir()
        self.git(self.upstream, "init", "-q")
        (self.upstream / "source").write_text("fixture\n")
        self.git(self.upstream, "add", ".")
        self.git(self.upstream, "-c", "user.name=Source Test", "-c", "user.email=source@example.invalid",
                 "commit", "-qm", "source fixture")
        self.sha = self.git(self.upstream, "rev-parse", "HEAD").strip()
        self.checkout = self.root / "checkout"
        for name in ("release.sh", "toolchain.env"):
            shutil.copy2(ROOT / name, self.root / name)

    @staticmethod
    def git(path, *args):
        return subprocess.check_output(["git", "-C", str(path), *args], text=True,
                                       stderr=subprocess.STDOUT)

    def download(self, ref):
        env = dict(os.environ, LLVM_REPOSITORY=self.upstream.as_uri(), LLVM_REF=ref,
                   LLVM_PROJECTDIR=str(self.checkout), SDKROOT="/unused-in-source-test")
        result = subprocess.run(["bash", "-c", 'source "$1"; ESP_LLVM_PATCHES=""; download_llvm_source',
                                 "source-test", str(self.root / "release.sh")],
                                env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.git(self.checkout, "rev-parse", "HEAD").strip(), self.sha)

    def test_bare_commit_reproduces_package_test_failure(self):
        self.download(self.sha)
        result = subprocess.run(["git", "-C", str(self.checkout), "describe", "--abbrev=7"],
                                text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No names found", result.stderr)

    def test_annotated_tag_survives_shallow_download_and_reuse(self):
        tag = "esp-23.1.6_fixture"
        self.git(self.upstream, "-c", "user.name=Source Test", "-c", "user.email=source@example.invalid",
                 "tag", "-a", tag, "-m", "source tag")
        for attempt in ("fresh", "reuse"):
            with self.subTest(attempt=attempt):
                self.download(tag)
                self.assertEqual(self.git(self.checkout, "describe", "--abbrev=7").strip(), tag)
                self.assertEqual(self.git(self.checkout, "cat-file", "-t", tag).strip(), "tag")
                self.assertEqual(self.git(self.checkout, "rev-parse", "--is-shallow-repository").strip(), "true")


if __name__ == "__main__":
    unittest.main()
