#!/usr/bin/env python3
"""Remove LLVM development inputs from a staged Unix executable toolchain."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import uuid


DEVELOPMENT_DIRS = ("include/llvm", "include/llvm-c", "include/clang",
                    "include/clang-c", "lib/cmake", "lib/pkgconfig")
BENCHMARK_TOOLS = ("llvm-exegesis", "llvm-opt-benchmark")
SYSTEM_ELF_LIBRARIES = re.compile(
    r"^(?:lib(?:c|m|dl|pthread|rt|resolv|util|atomic)\.so\.[0-9]+|"
    r"libgcc_s\.so\.1|libstdc\+\+\.so\.6|libz\.so\.1|ld-linux[^/]*\.so\.[0-9]+)$")


def shared_library(path):
    return path.name.endswith(".dylib") or ".so" in path.name


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.digest()


def runtime_files(root):
    return sorted(list((root / "bin").glob("*")) +
                  [path for path in (root / "lib").glob("*") if shared_library(path)])


def deduplicate(root, check=False):
    groups = {}
    saved = 0
    for path in runtime_files(root):
        if path.is_symlink() or not path.is_file():
            continue
        info = path.stat()
        key = (info.st_size, stat.S_IMODE(info.st_mode))
        groups.setdefault(key, []).append(path)
    for paths in groups.values():
        if len(paths) < 2:
            continue
        by_hash = {}
        for path in paths:
            by_hash.setdefault(digest(path), []).append(path)
        for canonical, *aliases in by_hash.values():
            for alias in aliases:
                if canonical.samefile(alias):
                    continue
                if check:
                    raise ValueError("identical runtime files are not hard-linked: " + str(alias))
                temporary = alias.with_name("." + alias.name + "." + uuid.uuid4().hex)
                try:
                    os.link(str(canonical), str(temporary))
                    os.replace(str(temporary), str(alias))
                finally:
                    if temporary.exists():
                        temporary.unlink()
                saved += canonical.stat().st_size
                print("Hard-linked {} -> {}".format(alias.name, canonical.name))
    return saved


def prune(root):
    if not (root / "bin/clang").is_file():
        raise ValueError("staged Clang is missing: " + str(root))
    for relative in DEVELOPMENT_DIRS:
        path = root / relative
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            shutil.rmtree(str(path))
    for pattern in ("libLLVM*.a", "libclang*.a"):
        for path in (root / "lib").glob(pattern):
            path.unlink()
    for name in BENCHMARK_TOOLS:
        path = root / "bin" / name
        if path.exists() or path.is_symlink():
            path.unlink()
    return deduplicate(root)


def validate_layout(root):
    for relative in DEVELOPMENT_DIRS:
        path = root / relative
        if path.exists() or path.is_symlink():
            raise ValueError("development directory leaked into runtime payload: " + relative)
    for pattern in ("libLLVM*.a", "libclang*.a"):
        if list((root / "lib").glob(pattern)):
            raise ValueError("static SDK libraries leaked into runtime payload: " + pattern)
    for name in BENCHMARK_TOOLS:
        if (root / "bin" / name).exists():
            raise ValueError("benchmark tool leaked into runtime payload: " + name)
    for path in root.rglob("*"):
        if path.is_symlink():
            try:
                path.resolve(strict=True).relative_to(root.resolve())
            except (ValueError, OSError, RuntimeError) as error:
                raise ValueError("broken or external payload symlink: " + str(path)) from error
    deduplicate(root, check=True)


def validate_dependencies(root, target):
    darwin = target.endswith("-apple-darwin")
    command = shutil.which("otool" if darwin else "readelf")
    if command is None and not darwin:
        command = shutil.which("llvm-readelf")
    if command is None:
        raise ValueError("otool/readelf is required to validate shared library dependencies")
    machine = {"x86_64-linux-gnu": 62, "aarch64-linux-gnu": 183,
               "x86_64-apple-darwin": 0x1000007,
               "aarch64-apple-darwin": 0x100000c}[target]
    count = 0
    for path in runtime_files(root):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as stream:
            header = stream.read(20)
        if header.startswith(b"#!"):
            continue
        if darwin:
            if header[:4] != b"\xcf\xfa\xed\xfe":
                raise ValueError("expected a 64-bit Mach-O tool/library: " + str(path))
            actual = int.from_bytes(header[4:8], "little")
            output = subprocess.check_output([command, "-L", str(path)], text=True)
            imports = [line.strip().split(" (", 1)[0] for line in output.splitlines()[1:]]
        else:
            # libc++.so is normally a GNU linker script combining libc++,
            # libc++abi and libunwind; it is a runtime link input, not an ELF.
            if header[:4] != b"\x7fELF" and shared_library(path):
                script = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.S).strip()
                if not re.match(r"^(?:INPUT|GROUP)\s*\(", script):
                    raise ValueError("unrecognized shared-library linker script: " + str(path))
                for token in re.findall(r"[-\w+./:]+", script):
                    if token in ("INPUT", "GROUP", "AS_NEEDED"):
                        continue
                    name = (token[3:] if token.startswith("-l:") else
                            "lib" + token[2:] + ".so" if token.startswith("-l") else token)
                    if "/" in name or not (root / "lib" / name).is_file():
                        raise ValueError("unbundled linker-script dependency: {} -> {}".format(path, token))
                continue
            if header[:4] != b"\x7fELF" or header[4:6] != b"\x02\x01":
                raise ValueError("expected a 64-bit little-endian ELF tool/library: " + str(path))
            actual = int.from_bytes(header[18:20], "little")
            output = subprocess.check_output([command, "-d", str(path)], text=True)
            imports = re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", output)
        if actual != machine:
            raise ValueError("tool/library architecture does not match {}: {}".format(target, path))
        for dependency in imports:
            if darwin and dependency.startswith(("/usr/lib/", "/System/Library/")):
                continue
            if not darwin and SYSTEM_ELF_LIBRARIES.fullmatch(dependency):
                continue
            name = dependency.rsplit("/", 1)[-1]
            if darwin and not dependency.startswith(("@rpath/", "@loader_path/", "@executable_path/")):
                raise ValueError("non-relocatable library dependency: " + dependency)
            if not (root / "lib" / name).is_file():
                raise ValueError("unbundled shared library dependency: {} -> {}".format(path, dependency))
        count += 1
    print("Validated {} relocated tools/shared libraries for {}".format(count, target))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("target")
    parser.add_argument("--check", action="store_true", help="Validate without changing files")
    args = parser.parse_args()
    if not args.check:
        saved = prune(args.root)
        print("Deduplicated {:.2f} MiB of identical runtime files".format(saved / 1024**2))
    validate_layout(args.root)
    validate_dependencies(args.root, args.target)
