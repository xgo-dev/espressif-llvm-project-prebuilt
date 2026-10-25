#!/usr/bin/env python3
"""Check pinned source acquisition against a local Git repository."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LLVMSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        shutil.copy2(ROOT / 'release.sh', self.root / 'release.sh')
        self.upstream = self.root / 'upstream'
        self.git('init', '-q', str(self.upstream))
        (self.upstream / 'source.txt').write_text('first\n')
        self.commit('first')
        self.first = self.git('-C', str(self.upstream), 'rev-parse', 'HEAD').strip()
        self.git('-C', str(self.upstream), '-c', 'user.name=Test', '-c',
                 'user.email=test@example.invalid', 'tag', '-am', 'release', 'release-test')
        (self.upstream / 'source.txt').write_text('second\n')
        self.commit('second')
        self.second = self.git('-C', str(self.upstream), 'rev-parse', 'HEAD').strip()
        self.source = self.root / 'source with spaces'
        (self.root / 'toolchain.env').write_text(
            f'ESP_LLVM_SOURCE_REPOSITORY="{self.upstream}"\n'
            f'ESP_LLVM_SOURCE_REF={self.first}\n'
            'ESP_LLVM_EXPECTED_VERSION=23.1.4\n'
            'ESP_LLVM_PAYLOAD_VERSION=23.1.4_test\n'
            'ESP_LLVM_PATCHES=""\n')

    def git(self, *args):
        return subprocess.check_output(['git', *args], text=True, stderr=subprocess.DEVNULL)

    def commit(self, message):
        self.git('-C', str(self.upstream), 'add', '.')
        self.git('-C', str(self.upstream), '-c', 'user.name=Test', '-c',
                 'user.email=test@example.invalid', 'commit', '-qm', message)

    def acquire(self, ref=None):
        env = os.environ.copy()
        env.update(LLVM_PROJECTDIR=str(self.source), SDKROOT='/unused')
        if ref:
            env['LLVM_REF'] = ref
        return subprocess.run(
            ['bash', '-c', 'source "$1"; download_llvm_source; '
             'test -z "$LLVM_SOURCE_PATCH_SHA256"', '_', str(self.root / 'release.sh')],
            env=env, text=True, capture_output=True, timeout=15)

    def test_commit_and_idempotent_reuse(self):
        for _ in range(2):
            result = self.acquire()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.git('-C', str(self.source), 'rev-parse', 'HEAD').strip(), self.first)

    def test_annotated_tag(self):
        result = self.acquire('release-test')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git('-C', str(self.source), 'rev-parse', 'HEAD').strip(), self.first)
        self.assertEqual(self.git('-C', str(self.source), 'describe', '--abbrev=7').strip(), 'release-test')
        self.assertEqual(self.git('-C', str(self.source), 'cat-file', '-t', 'refs/tags/release-test').strip(), 'tag')

    def test_existing_checkout_acquires_tag_metadata(self):
        self.assertEqual(self.acquire().returncode, 0)
        self.assertEqual(self.git('-C', str(self.source), 'tag').strip(), '')
        for _ in range(2):
            result = self.acquire('release-test')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.git('-C', str(self.source), 'describe', '--abbrev=7').strip(), 'release-test')
            self.assertEqual(self.git('-C', str(self.source), 'rev-parse', 'HEAD').strip(), self.first)

    def test_mismatch_does_not_change_checkout(self):
        self.assertEqual(self.acquire().returncode, 0)
        result = self.acquire(self.second)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Use a fresh LLVM_PROJECTDIR', result.stderr)
        self.assertEqual(self.git('-C', str(self.source), 'rev-parse', 'HEAD').strip(), self.first)

    def test_invalid_ref(self):
        result = self.acquire('no-such-ref')
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
