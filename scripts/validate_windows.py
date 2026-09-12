#!/usr/bin/env python3
"""Check Windows payload architecture and imports without executing its tools."""
import argparse
import os
from pathlib import Path
import struct

MACHINES = {"x86_64-w64-mingw32": 0x8664, "aarch64-w64-mingw32": 0xAA64}
# Windows SDK import libraries used by LLVM and the bundled MinGW runtimes.
SYSTEM_DLLS = {
    "advapi32.dll", "bcrypt.dll", "comdlg32.dll", "crypt32.dll", "dbghelp.dll",
    "gdi32.dll", "imagehlp.dll", "iphlpapi.dll", "kernel32.dll", "msvcrt.dll",
    "ntdll.dll", "ole32.dll", "oleaut32.dll", "psapi.dll", "rpcrt4.dll",
    "secur32.dll", "shell32.dll", "shlwapi.dll", "ucrtbase.dll", "user32.dll",
    "userenv.dll", "version.dll", "winmm.dll", "ws2_32.dll",
}


def inspect_pe(path):
    data = path.read_bytes()
    def unpack(fmt, offset):
        return struct.unpack_from(fmt, data, offset)
    if data[:2] != b"MZ":
        raise ValueError(f"{path}: missing DOS header")
    pe, = unpack("<I", 0x3C)
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError(f"{path}: missing PE signature")
    machine, count = unpack("<HH", pe + 4)
    optional_size, = unpack("<H", pe + 20)
    optional = pe + 24
    magic, = unpack("<H", optional)
    if magic != 0x20B:
        raise ValueError(f"{path}: expected PE32+ optional header")
    headers_size, = unpack("<I", optional + 60)
    sections = []
    for i in range(count):
        size, rva, raw_size, raw = unpack("<IIII", optional + optional_size + i * 40 + 8)
        sections.append((rva, max(size, raw_size), raw, raw_size))
    def offset(rva):
        if rva < headers_size:
            return rva
        for start, size, raw, raw_size in sections:
            if start <= rva < start + size and rva - start < raw_size:
                return raw + rva - start
        raise ValueError(f"{path}: unmapped RVA {rva:#x}")
    def name(rva):
        start = offset(rva)
        return data[start:data.index(b"\0", start)].decode("ascii").lower()
    imports = set()
    directories, = unpack("<I", optional + 108)
    for index, entry_size, name_offset in ((1, 20, 12), (13, 32, 4)):
        if index >= directories:
            continue
        rva, size = unpack("<II", optional + 112 + index * 8)
        if not rva:
            continue
        for pos in range(offset(rva), offset(rva) + size, entry_size):
            entry = data[pos:pos + entry_size]
            if not any(entry):
                break
            if index == 13 and unpack("<I", pos)[0] != 1:
                raise ValueError(f"{path}: unsupported VA-based delay import")
            imports.add(name(unpack("<I", pos + name_offset)[0]))
    return machine, imports


def validate(root, target, native=False):
    expected = MACHINES[target]
    if native:
        # PROCESSOR_ARCHITEW6432 identifies the OS when invoked through x64 bash
        # or Python on Windows ARM64; interpreter bitness is not the host.
        host = os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE", "")
        wanted = "ARM64" if expected == 0xAA64 else "AMD64"
        if os.name != "nt" or host.upper() != wanted:
            raise ValueError(f"native validation requires Windows {wanted}, got {os.name}/{host}")
    manifest = dict(line.split("=", 1) for line in (root / "LLGO-LLVM-MANIFEST.txt").read_text().splitlines() if "=" in line)
    if manifest.get("host_target") != target:
        raise ValueError(f"manifest host_target is not {target}")
    files = {p.name.lower(): p for p in (root / "bin").iterdir() if p.suffix.lower() in (".exe", ".dll")}
    if not files:
        raise ValueError("no Windows binaries found")
    for path in files.values():
        machine, imports = inspect_pe(path)
        if machine != expected:
            raise ValueError(f"{path}: PE machine {machine:#x}, expected {expected:#x}")
        for dll in imports:
            if dll in files or dll in SYSTEM_DLLS or dll.startswith(("api-ms-", "ext-ms-")):
                continue
            raise ValueError(f"{path}: unbundled non-system dependency {dll}")
    print(f"Validated {len(files)} PE files and DLL imports for {target}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("target", choices=MACHINES)
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    validate(args.root, args.target, args.native)
