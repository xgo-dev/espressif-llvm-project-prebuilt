#!/usr/bin/env python3
"""Build real LLGo firmware using only the extracted Unix runtime payload."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tarfile

from package_unix import digest


# Published LLVM 22 consumer baseline; hashes are from llgo1.0.9.checksums.txt.
BASELINES = {
    "aarch64-apple-darwin": ("darwin-arm64", "1a1679d6a930a722e092ce50483edaa601fb69f6c928a3c27ece1663e97fd809"),
    "x86_64-apple-darwin": ("darwin-amd64", "837912697d3588e2539b56602ea1ae9f73fb569eac594b14713513d88c8d5e50"),
    "aarch64-linux-gnu": ("linux-arm64", "557fe638181f53715a222e4d59907f0c196a6af97094288c20bc2d0815f29e1e"),
    "x86_64-linux-gnu": ("linux-amd64", "174545189b890dace9043cae60297b5a457a63512d48ce2e0b5a2c6a65350874"),
}


def copy_runtime(source, destination):
    # A tar round trip keeps both hard links and relative symlinks, including
    # when the test SDK and consumer live on separate filesystems.
    destination.mkdir()
    env = os.environ.copy()
    env["COPYFILE_DISABLE"] = "1"
    pack = subprocess.Popen(["tar", "-cf", "-", "-C", str(source), "."],
                            stdout=subprocess.PIPE, env=env)
    unpack = subprocess.run(["tar", "-xf", "-", "-C", str(destination)], stdin=pack.stdout)
    pack.stdout.close()
    if pack.wait() or unpack.returncode:
        raise RuntimeError("could not stage the extracted runtime for the consumer test")


def run(command, env, cwd):
    result = subprocess.run([str(arg) for arg in command], env=env, cwd=cwd,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=900)
    if result.returncode:
        raise RuntimeError("{} exited {}:\n{}".format(command, result.returncode, result.stdout[-24000:]))
    return result.stdout


def validate(sdk, target, work, archive=None):
    platform, expected = BASELINES[target]
    work.mkdir(parents=True, exist_ok=False)
    if archive is None:
        archive = work / ("llgo1.0.9." + platform + ".tar.gz")
        url = "https://github.com/xgo-dev/llgo/releases/download/v1.0.9/" + archive.name
        subprocess.run(["curl", "-fsSL", "--connect-timeout", "30", "--max-time", "600",
                        "--retry", "3", url, "-o", str(archive)], check=True)
    if digest(archive).hex() != expected:
        raise ValueError("LLGo consumer archive SHA-256 mismatch")
    consumer = work / "consumer package"
    consumer.mkdir()
    # Extract the compiler, runtime sources and target definitions. No headers,
    # tools or libraries from the baseline ESP development SDK are extracted.
    with tarfile.open(archive, mode="r|gz") as stream:
        for member in stream:
            top = Path(member.name).parts
            if top and top[0] in ("bin", "runtime", "targets", "LICENSES"):
                stream.extract(member, consumer, filter="data")
    binary = consumer / "bin/llgo"
    if not binary.is_file():
        raise ValueError("published LLGo archive does not contain bin/llgo")
    staged_sdk = consumer / "crosscompile/clang"
    staged_sdk.parent.mkdir()
    copy_runtime(sdk, staged_sdk)
    for directory in ("llvm", "llvm-c", "clang", "clang-c"):
        if (staged_sdk / "include" / directory).exists():
            raise ValueError("consumer test unexpectedly contains development headers")
    source = work / "firmware source"
    (source / "embedded").mkdir(parents=True)
    (source / "go.mod").write_text("module esp-runtime-smoke\n\ngo 1.27.0\n\nrequire github.com/goplus/lib v0.6.3\n")
    (source / "embedded/main.go").write_text("package main\nfunc main() {}\n")
    env = os.environ.copy()
    for name in ("LLGO_ROOT", "LLVM_CONFIG", "CC", "CXX", "CGO_CPPFLAGS", "CGO_CFLAGS",
                 "CGO_CXXFLAGS", "CGO_LDFLAGS", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH",
                 "DYLD_FALLBACK_LIBRARY_PATH", "GOOS", "GOARCH"):
        env.pop(name, None)
    env.update(GOTOOLCHAIN="local", CGO_ENABLED="1", LLGO_BUILD_CACHE="0",
               XDG_CACHE_HOME=str(work / "empty cache"))
    version = run([binary, "version"], env, source).strip()
    print("Testing {} on {}".format(version, target), flush=True)
    run(["go", "mod", "download", "github.com/goplus/lib"], env, source)
    results = []
    for board, machine in (("esp32-coreboard-v2", 94), ("esp32c3", 243)):
        for mode in ("default", "full"):
            print("Building {} / {} using the extracted runtime".format(board, mode), flush=True)
            output = source / (board + "-" + mode + ".out")
            command = [binary, "build", "-x"]
            if mode == "full":
                command.append("-lto=full")
            trace = run(command + ["-target", board, "-o", output, "./embedded"], env, source)
            (source / (board + "-" + mode + ".log")).write_text(trace)
            header = Path(str(output) + ".elf").read_bytes()[:20]
            if header[:6] != b"\x7fELF\x01\x01" or int.from_bytes(header[18:20], "little") != machine:
                raise ValueError("LLGo firmware did not produce the expected Xtensa/RISC-V ELF")
            if "ESP Clang not found" in trace or "crosscompile/clang/bin/" not in trace:
                raise ValueError("LLGo did not use the newly installed runtime payload")
            results.append(board + ":" + mode)
            print("PASS " + results[-1], flush=True)
    (work / "consumer-results.json").write_text(json.dumps({
        "target": target, "consumer": "LLGo 1.0.9", "consumer_sha256": expected,
        "firmware": results, "status": "passed",
    }, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sdk", type=Path)
    parser.add_argument("target", choices=BASELINES)
    parser.add_argument("work", type=Path)
    parser.add_argument("--llgo-archive", type=Path)
    args = parser.parse_args()
    validate(args.sdk.resolve(), args.target, args.work.resolve(), args.llgo_archive)
