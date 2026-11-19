#!/usr/bin/env python3
"""Build and qualify the external static libraries needed by the MSVC SDK."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import zipfile

HERE = Path(__file__).resolve().parent


def run(*args, **kwargs):
    print('+', ' '.join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def capture(*args):
    return run(*args, stdout=subprocess.PIPE, text=True).stdout.strip()


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(url, digest, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or sha256(destination) != digest:
        temporary = destination.with_suffix(destination.suffix + '.part')
        run('curl.exe', '--fail', '--location', '--retry', '5', '--retry-all-errors',
            '--output', temporary, url)
        if sha256(temporary) != digest:
            raise ValueError(f'SHA-256 mismatch: {url}')
        temporary.replace(destination)
    # Cached downloads are checked before every extraction.
    if sha256(destination) != digest:
        raise ValueError(f'SHA-256 mismatch: {destination}')
    return destination


def inspect_library(output, machine):
    machines = re.findall(r'Machine: (\w+)', output)
    if not machines or set(machines) != {machine}:
        raise ValueError(f'Unexpected library machines: {set(machines)}')
    directives = output.upper()
    if not re.search(r'DEFAULTLIB:["\s]*LIBCMT\b', directives):
        raise ValueError('Static release CRT (LIBCMT) is absent')
    if re.search(r'DEFAULTLIB:["\s]*(?:MSVCRT|MSVCRTD|LIBCMTD)\b', directives):
        raise ValueError('Dynamic or debug CRT is present')


def inspect_imports(output, machine):
    machines = re.findall(r'Machine: (\w+)', output)
    if machines != [machine]:
        raise ValueError(f'Unexpected executable machine: {machines}')
    imports = re.findall(r'^\s*Name: (.+\.dll)\s*$', output, re.MULTILINE | re.I)
    for name in imports:
        if re.search(r'zlib|zstd|msvcp|msvcr|vcruntime|ucrt|api-ms-win-crt-|libgcc|libstdc\+\+|libwinpthread', name, re.I):
            raise ValueError(f'Unexpected runtime dependency: {name}')
    return imports


def relocate_pkgconfig(text, library):
    # zlib's CMake install expands every directory separately, so changing
    # prefix alone does not relocate libdir/includedir/sharedlibdir.
    values = {'prefix': '${pcfiledir}/../..', 'exec_prefix': '${prefix}',
              'libdir': '${prefix}/lib', 'sharedlibdir': '${prefix}/lib',
              'includedir': '${prefix}/include'}
    for name, value in values.items():
        text = re.sub(rf'^{name}=.*$', f'{name}={value}', text, flags=re.M)
    # Upstream Unix names (-lz/-lzstd) differ from the MSVC static archives.
    return re.sub(r'^Libs:.*$', f'Libs: -L"${{libdir}}" -l{library}', text, flags=re.M)


def build(arch, work, output):
    contract = json.loads((HERE / 'contract.json').read_text())
    target = contract['targets'][arch]
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    # Preserve only checked downloads across local reruns. Derived files must
    # not survive changes to the compiler, sources or packaging contract.
    for name in ('sdk', 'sdk-tar', 'stage', 'source-zlib', 'source-zstd',
                 'build-zlib', 'build-zstd', 'relocated dependencies'):
        derived = work / name
        if derived.exists():
            shutil.rmtree(derived)
    downloads = work / 'downloads'
    sdk_archive = download(target['sdk_url'], target['sdk_sha256'], downloads / 'sdk.tar.xz')
    sdk_parent = work / 'sdk'
    sdk_parent.mkdir()
    # Windows' bundled bsdtar can stall on the upstream xz archive. Use the
    # same two-stage 7-Zip extraction as LLGo's Windows SDK installer, selecting
    # only the compiler, resource headers and libraries used by these probes.
    seven_zip = Path(os.environ['ProgramFiles']) / '7-Zip/7z.exe'
    tar_directory = work / 'sdk-tar'
    tar_directory.mkdir()
    sdk_name = f"clang+llvm-{contract['llvm_version']}-{arch}-pc-windows-msvc"
    tools_needed = ('clang.exe', 'clang-cl.exe', 'clang++.exe', 'lld-link.exe',
                    'llvm-config.exe', 'llvm-readobj.exe', 'llvm-ar.exe', 'llvm-lib.exe')
    entries = [f'{sdk_name}/bin/{name}' for name in tools_needed]
    entries += [f'{sdk_name}/bin/*.dll', f'{sdk_name}/include/*',
                f'{sdk_name}/lib/clang/*/include/*']
    entries += [f'{sdk_name}/lib/{name}' for name in
                ('LLVMFileCheck.lib', 'LLVMSupport.lib', 'LLVMDemangle.lib')]
    run(seven_zip, 'x', '-y', f'-o{tar_directory}', sdk_archive)
    run(seven_zip, 'x', '-y', f'-o{sdk_parent}', tar_directory / 'sdk.tar', *entries)
    shutil.rmtree(tar_directory)
    sdk = sdk_parent / sdk_name
    tools = sdk / 'bin'
    llvm_config = tools / 'llvm-config.exe'
    if capture(llvm_config, '--version') != contract['llvm_version']:
        raise ValueError('Incorrect LLVM SDK version')
    readobj = tools / 'llvm-readobj.exe'
    compiler = tools / 'clang-cl.exe'
    stage = work / 'stage'
    stage.mkdir()
    source_roots = {}
    for name in ('zlib', 'zstd'):
        source = contract['sources'][name]
        archive = download(source['url'], source['sha256'], downloads / f'{name}.tar.gz')
        source_parent = work / f'source-{name}'
        source_parent.mkdir()
        with tarfile.open(archive) as tar:
            # Match the official Windows builder: zstd tests contain symlinks
            # and are not needed for these library-only builds.
            members = [m for m in tar if name != 'zstd' or '/tests/' not in m.name]
            tar.extractall(source_parent, members=members, filter='data')
        source_root = source_parent / f"{name}-{source['version']}"
        source_roots[name] = source_root
        options = (['-DZLIB_BUILD_TESTING=OFF', '-DZLIB_BUILD_SHARED=OFF',
                    '-DZLIB_BUILD_STATIC=ON', '-DZLIB_INSTALL=ON'] if name == 'zlib' else
                   ['-DZSTD_BUILD_PROGRAMS=OFF', '-DZSTD_BUILD_TESTS=OFF',
                    '-DZSTD_BUILD_SHARED=OFF', '-DZSTD_BUILD_STATIC=ON',
                    '-DZSTD_USE_STATIC_RUNTIME=ON'])
        cmake_source = source_root if name == 'zlib' else source_root / 'build/cmake'
        build_dir = work / f'build-{name}'
        run('cmake', '-S', cmake_source, '-B', build_dir, '-G', 'Ninja',
            '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded',
            # zstd 1.5.7 selects pre-3.15 CMake policies; otherwise the runtime
            # variable can be ignored even though configuration succeeds.
            '-DCMAKE_POLICY_DEFAULT_CMP0091=NEW',
            f'-DCMAKE_C_COMPILER={compiler.as_posix()}',
            f'-DCMAKE_CXX_COMPILER={compiler.as_posix()}',
            f'-DCMAKE_INSTALL_PREFIX={stage.as_posix()}', *options)
        run('cmake', '--build', build_dir, '--parallel', '4')
        run('cmake', '--install', build_dir)
    expected = {'zs.lib', 'zstd_static.lib'}
    if {p.name for p in stage.rglob('*.lib')} != expected or list(stage.rglob('*.dll')):
        raise ValueError('Unexpected libraries in dependency package')
    for pc in stage.rglob('*.pc'):
        library = {'zlib.pc': 'zs', 'libzstd.pc': 'zstd_static'}[pc.name]
        pc.write_text(relocate_pkgconfig(pc.read_text(), library))
    for metadata in list(stage.rglob('*.cmake')) + list(stage.rglob('*.pc')):
        if stage.as_posix().lower() in metadata.read_text().replace('\\', '/').lower():
            raise ValueError(f'Non-relocatable metadata: {metadata}')
    licenses = stage / 'licenses'
    licenses.mkdir()
    for name in source_roots:
        shutil.copyfile(source_roots[name] / 'LICENSE', licenses / f'{name}-LICENSE')
    manifest = {
        **contract, 'architecture': arch, 'triple': f'{arch}-pc-windows-msvc',
        'producer_revision': capture('git', 'rev-parse', 'HEAD'),
        'compiler': capture(compiler, '--version'),
        'msvc_tools_version': os.environ.get('VCToolsVersion'),
        'windows_sdk_version': os.environ.get('WindowsSDKVersion'),
        'files': {p.relative_to(stage).as_posix(): sha256(p)
                  for p in sorted(stage.rglob('*')) if p.is_file()},
    }
    (stage / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    stem = f"llvm-msvc-deps-{contract['version']}-windows-{arch}-msvc-mt"
    archive = output / f'{stem}.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as package:
        for path in sorted(stage.rglob('*')):
            if path.is_file():
                package.write(path, path.relative_to(stage).as_posix())
    # Exercise only the published ZIP's contents, after relocation to a path with spaces.
    relocated = work / 'relocated dependencies'
    with zipfile.ZipFile(archive) as package:
        package.extractall(relocated)
    for relative, digest in manifest['files'].items():
        if sha256(relocated / relative) != digest:
            raise ValueError(f'Extracted file hash mismatch: {relative}')
    for name in sorted(expected):
        report = capture(readobj, '--file-headers', '--coff-directives', relocated / 'lib' / name)
        (work / f'{name}.inspection.txt').write_text(report)
        inspect_library(report, target['machine'])
    system_libs = capture(llvm_config, '--link-static', '--system-libs').split()
    if not expected.issubset(set(system_libs)):
        raise ValueError(f'Unexpected LLVM compression dependencies: {system_libs}')
    # The optional Windows manifest component requires libxml2, but these consumers do not.
    system_libs = [x for x in system_libs if x not in ('libxml2s.lib', 'xml2s.lib')]
    flags = [f'--target={arch}-pc-windows-msvc', '-fuse-ld=lld',
             '-fms-runtime-lib=static', '-std=c++17', f'-I{sdk / "include"}',
             f'-L{sdk / "lib"}', f'-L{relocated / "lib"}']
    libraries = [sdk / 'lib' / name for name in
                 ('LLVMFileCheck.lib', 'LLVMSupport.lib', 'LLVMDemangle.lib')]
    libraries += [f'-l{Path(name).stem}' for name in system_libs]
    filecheck = contract['sources']['filecheck']
    filecheck_source = download(filecheck['url'], filecheck['sha256'], downloads / 'FileCheck.cpp')
    validation = {'architecture': arch, 'sdk_sha256': target['sdk_sha256'], 'executables': {}}
    for name, source in [('compression-smoke', HERE / 'smoke.cpp'), ('FileCheck', filecheck_source)]:
        executable = work / f'{name}.exe'
        run(tools / 'clang++.exe', *flags, source, *libraries, '-o', executable)
        report = capture(readobj, '--file-headers', '--coff-imports', executable)
        validation['executables'][name] = inspect_imports(report, target['machine'])
        if name == 'FileCheck':
            check = work / 'check.txt'
            check.write_text('CHECK: llvm-msvc-deps\n')
            run(executable, check, input='llvm-msvc-deps\n', text=True)
        else:
            run(executable)
    validation['archive_sha256'] = sha256(archive)
    validation['producer_revision'] = manifest['producer_revision']
    (output / f'{stem}.validation.json').write_text(json.dumps(validation, indent=2) + '\n')
    (output / f'{stem}.zip.sha256').write_text(f"{sha256(archive)}  {archive.name}\n")
    print(f'Validated {archive}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--arch', choices=('x86_64', 'aarch64'), required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.arch, args.work.resolve(), args.output.resolve())
