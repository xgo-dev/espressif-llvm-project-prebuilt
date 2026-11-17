#!/usr/bin/env python3
"""Require every LLGo toolchain archive before assembling a release."""
import argparse
import hashlib
from pathlib import Path
import shutil

HOSTS = (
    "x86_64-linux-gnu", "aarch64-linux-gnu",
    "x86_64-apple-darwin", "aarch64-apple-darwin",
    "x86_64-w64-mingw32", "aarch64-w64-mingw32", "i686-w64-mingw32",
)


def expected_archives(payload, sdk):
    return {f"clang-esp-{payload}-{host}.tar.xz" for host in HOSTS} | {
        f"llvm-sdk-{sdk}-i686-w64-mingw32.tar.xz"}


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def prepare(artifacts, destination, payload, sdk):
    archives = {}
    for path in artifacts.rglob("*.tar.xz"):
        if path.name in archives:
            raise ValueError(f"duplicate archive: {path.name}")
        archives[path.name] = path
    expected = expected_archives(payload, sdk)
    if archives.keys() != expected:
        raise ValueError(f"incomplete release: missing={sorted(expected - archives.keys())}, "
                         f"unexpected={sorted(archives.keys() - expected)}")
    checksums = []
    for name, path in sorted(archives.items()):
        sidecar = path.with_name(name + ".sha256")
        lines = sidecar.read_text().strip().splitlines()
        actual = digest(path)
        if len(lines) != 1 or not lines[0].split() or lines[0].split()[0] != actual:
            raise ValueError(f"checksum mismatch: {name}")
        checksums.append(f"{actual}  {name}\n")
    # Validate the complete input before exposing any files to the upload step.
    destination.mkdir(parents=True, exist_ok=False)
    for (name, path), checksum in zip(sorted(archives.items()), checksums):
        shutil.copyfile(path, destination / name)
        (destination / (name + ".sha256")).write_text(checksum)
    (destination / f"clang-esp-{payload}-checksum.sha256").write_text("".join(checksums))
    print(f"Verified complete release: {len(archives)} archives, including Windows x86 SDK")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("payload")
    parser.add_argument("sdk")
    args = parser.parse_args()
    prepare(args.artifacts, args.destination, args.payload, args.sdk)
