import unittest
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

from build import HERE, inspect_imports, inspect_library, relocate_pkgconfig, sha256
from verify_release import verify


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
        for dll in ('zstd.dll', 'zlib1.dll', 'VCRUNTIME140.dll', 'ucrtbase.dll',
                    'api-ms-win-crt-runtime-l1-1-0.dll'):
            with self.subTest(dll=dll), self.assertRaises(ValueError):
                inspect_imports(f'Machine: {self.machine}\nName: {dll}\n', self.machine)

    def test_system_imports(self):
        self.assertEqual(inspect_imports(f'Machine: {self.machine}\nName: KERNEL32.dll\n',
                                        self.machine), ['KERNEL32.dll'])


class PkgConfigChecks(unittest.TestCase):
    def test_zlib_cmake_expanded_paths(self):
        source = ('prefix=C:/a/_temp/stage\nexec_prefix=C:/a/_temp/stage\n'
                  'libdir=C:/a/_temp/stage/lib\nsharedlibdir=C:/a/_temp/stage/lib\n'
                  'includedir=C:/a/_temp/stage/include\nVersion: 1.3.2\n'
                  'Libs: -L${libdir} -L${sharedlibdir} -lz\nCflags: -I${includedir}\n')
        result = relocate_pkgconfig(source, 'zs')
        self.assertNotIn('C:/a/', result)
        self.assertIn('prefix=${pcfiledir}/../..\n', result)
        self.assertIn('libdir=${prefix}/lib\n', result)
        self.assertIn('includedir=${prefix}/include\n', result)
        self.assertIn('Libs: -L"${libdir}" -lzs\n', result)
        self.assertIn('Version: 1.3.2\n', result)

    def test_zstd_static_library_name(self):
        result = relocate_pkgconfig('prefix=C:/stage\nLibs: -L${libdir} -lzstd\n',
                                    'zstd_static')
        self.assertIn('Libs: -L"${libdir}" -lzstd_static\n', result)


class ReleaseChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.contract = json.loads((HERE / 'contract.json').read_text())
        self.revision = 'a' * 40
        self.tag = 'llvm-msvc-deps-' + self.contract['version']
        for arch, target in self.contract['targets'].items():
            stem = f"{self.tag}-windows-{arch}-msvc-mt"
            archive = self.root / f'{stem}.zip'
            files = {name: name.encode() for name in (
                'lib/zs.lib', 'lib/zstd_static.lib',
                'licenses/zlib-LICENSE', 'licenses/zstd-LICENSE')}
            manifest = {**self.contract, 'architecture': arch,
                        'producer_revision': self.revision,
                        'files': {n: hashlib.sha256(b).hexdigest() for n, b in files.items()}}
            with zipfile.ZipFile(archive, 'w') as package:
                for name, data in files.items():
                    package.writestr(name, data)
                package.writestr('build-manifest.json', json.dumps(manifest))
            digest = sha256(archive)
            (self.root / f'{stem}.zip.sha256').write_text(f'{digest}  {archive.name}\n')
            (self.root / f'{stem}.validation.json').write_text(json.dumps({
                'architecture': arch, 'sdk_sha256': target['sdk_sha256'],
                'archive_sha256': digest, 'producer_revision': self.revision,
                'executables': {'FileCheck': [], 'compression-smoke': []}}))

    def test_complete_release(self):
        verify(self.root, self.tag, self.revision)

    def test_missing_architecture(self):
        next(self.root.glob('*aarch64*.zip')).unlink()
        with self.assertRaises(FileNotFoundError):
            verify(self.root, self.tag, self.revision)

    def test_different_revision(self):
        with self.assertRaises(ValueError):
            verify(self.root, self.tag, 'b' * 40)

    def test_corrupt_archive(self):
        with next(self.root.glob('*.zip')).open('ab') as file:
            file.write(b'corrupt')
        with self.assertRaises(ValueError):
            verify(self.root, self.tag, self.revision)


if __name__ == '__main__':
    unittest.main()
