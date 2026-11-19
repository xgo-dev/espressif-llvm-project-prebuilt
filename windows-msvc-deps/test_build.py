import unittest

from build import inspect_imports, inspect_library


class BinaryChecks(unittest.TestCase):
    machine = 'IMAGE_FILE_MACHINE_AMD64'

    def test_reject_mixed_archives(self):
        with self.assertRaises(ValueError):
            inspect_library('Machine: IMAGE_FILE_MACHINE_AMD64\n'
                            'Machine: IMAGE_FILE_MACHINE_ARM64\n'
                            'Directive(s): /DEFAULTLIB:libcmt', self.machine)

    def test_reject_dynamic_or_debug_crt(self):
        for crt in ('msvcrt', 'msvcrtd', 'libcmtd'):
            with self.subTest(crt=crt), self.assertRaises(ValueError):
                inspect_library(f'Machine: {self.machine}\n'
                                f'/DEFAULTLIB:libcmt /DEFAULTLIB:{crt}', self.machine)

    def test_release_static_crt(self):
        inspect_library(f'Machine: {self.machine}\n/DEFAULTLIB:libcmt', self.machine)

    def test_reject_runtime_dlls(self):
        for dll in ('zstd.dll', 'zlib1.dll', 'VCRUNTIME140.dll', 'ucrtbase.dll'):
            with self.subTest(dll=dll), self.assertRaises(ValueError):
                inspect_imports(f'Machine: {self.machine}\nName: {dll}\n', self.machine)

    def test_system_imports(self):
        self.assertEqual(inspect_imports(f'Machine: {self.machine}\nName: KERNEL32.dll\n',
                                        self.machine), ['KERNEL32.dll'])


if __name__ == '__main__':
    unittest.main()
