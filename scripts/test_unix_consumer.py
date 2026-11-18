#!/usr/bin/env python3
"""Catch consumer/runtime version mismatches and preserve failed build traces."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from test_unix_llgo import check_headers, run


class ConsumerTests(unittest.TestCase):
    def test_matching_and_old_headers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "include/llvm-c").mkdir(parents=True)
            (root / "include/llvm-c/Core.h").touch()
            config = root / "include/llvm/Config/llvm-config.h"
            config.parent.mkdir(parents=True)
            config.write_text("#define LLVM_VERSION_MAJOR 23\n#define LLVM_VERSION_MINOR 1\n#define LLVM_VERSION_PATCH 6\n")
            check_headers(root)
            config.write_text(config.read_text().replace("MAJOR 23", "MAJOR 22"))
            with self.assertRaisesRegex(ValueError, "must match LLVM 23.1.6"):
                check_headers(root)

    def test_missing_headers(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "missing consumer development header"):
                check_headers(Path(temporary))

    def test_failed_command_keeps_full_trace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log = root / "failure.log"
            with self.assertRaises(RuntimeError):
                run([sys.executable, "-c", "print('begin'); print('x'*25000); print('end'); raise SystemExit(1)"],
                    os.environ, root, log=log)
            self.assertTrue(log.read_text().startswith("begin\n"))
            self.assertTrue(log.read_text().endswith("end\n"))


if __name__ == "__main__":
    unittest.main()
