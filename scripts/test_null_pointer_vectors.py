#!/usr/bin/env python3
"""Exercise LLVM #235980 with the tools from an extracted runtime payload."""
import argparse
from pathlib import Path
import subprocess
import tempfile


IR = """@scalar = global ptr null
@v2 = global <2 x ptr> zeroinitializer
@v4 = global <4 x ptr> splat (ptr null)
@array = global [2 x ptr] zeroinitializer
"""


def validate(bindir):
    def tool(name):
        path = bindir / name
        return str(path if path.is_file() else path.with_suffix(".exe"))

    def run(name, *args):
        subprocess.run([tool(name), *map(str, args)], check=True)

    with tempfile.TemporaryDirectory(prefix="llvm-null-vector-") as directory:
        root = Path(directory)
        source = root / "null-vector.ll"
        source.write_text(IR)
        for triple in ("x86_64-unknown-linux-gnu", "i686-unknown-linux-gnu",
                       "x86_64-pc-windows-msvc"):
            output = root / (triple + ".o")
            run("llc", "-mtriple=" + triple, "-data-sections", "-filetype=obj",
                source, "-o", output)
            if output.stat().st_size == 0:
                raise ValueError("empty object for " + triple)
        # LLD runs its own LTO code generator; testing llc alone misses it.
        bitcode = root / "null-vector.bc"
        run("opt", "-mtriple=x86_64-unknown-linux-gnu", "-passes=verify",
            source, "-o", bitcode)
        linked = root / "lto.o"
        run("ld.lld", "-r", bitcode, "-o", linked)
        if linked.stat().st_size == 0:
            raise ValueError("empty LTO object")
    print("PASS null-pointer vector ELF32/ELF64/COFF emission and ELF LTO")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bindir", type=Path)
    validate(parser.parse_args().bindir.resolve())
