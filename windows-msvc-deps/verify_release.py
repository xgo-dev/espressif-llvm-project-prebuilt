#!/usr/bin/env python3
"""Require two complete, qualified packages before publishing a new release."""
import json
import os
from pathlib import Path
import sys
import zipfile

from build import HERE, sha256


def verify(directory, tag, revision):
    contract = json.loads((HERE / 'contract.json').read_text())
    if tag != f"llvm-msvc-deps-{contract['version']}":
        raise ValueError('Release tag differs from the checked-in package version')
    expected = set()
    for arch, target in contract['targets'].items():
        stem = f"llvm-msvc-deps-{contract['version']}-windows-{arch}-msvc-mt"
        archive = directory / f'{stem}.zip'
        report = directory / f'{stem}.validation.json'
        checksum = directory / f'{stem}.zip.sha256'
        expected.update((archive.name, report.name, checksum.name))
        digest = sha256(archive)
        if checksum.read_text().strip() != f'{digest}  {archive.name}':
            raise ValueError(f'Incorrect checksum for {arch}')
        validation = json.loads(report.read_text())
        if (validation['architecture'] != arch or
                validation['sdk_sha256'] != target['sdk_sha256'] or
                validation['archive_sha256'] != digest or
                validation['producer_revision'] != revision or
                set(validation['executables']) != {'FileCheck', 'compression-smoke'}):
            raise ValueError(f'Incorrect qualification report for {arch}')
        with zipfile.ZipFile(archive) as package:
            manifest = json.loads(package.read('build-manifest.json'))
            if (manifest['architecture'] != arch or manifest['runtime'] != 'MT' or
                    manifest['version'] != contract['version'] or
                    manifest['producer_revision'] != revision or
                    manifest['sources'] != contract['sources']):
                raise ValueError(f'Incorrect build manifest for {arch}')
    if {p.name for p in directory.iterdir()} != expected:
        raise ValueError('Unexpected release files')


if __name__ == '__main__':
    verify(Path(sys.argv[1]), os.environ['RELEASE_TAG'], os.environ['GITHUB_SHA'])
