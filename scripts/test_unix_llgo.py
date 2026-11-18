#!/usr/bin/env python3
"""Build real LLGo firmware using only the extracted Unix runtime payload."""
import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import shlex
import sys

# LLVM 23 migration snapshot; includes numeric LTO optimization levels and
# O2 pipelines with optsize/minsize attributes. Do not use LLVM 22 binaries.
CONSUMER_REVISION = "39332bc27391a6190d2c98781e858d3478cce49a"
CONSUMER_REPOSITORY = "https://github.com/xgo-dev/llgo.git"
TARGETS = ("aarch64-apple-darwin", "x86_64-apple-darwin",
           "aarch64-linux-gnu", "x86_64-linux-gnu")


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


def run(command, env, cwd, log=None):
    result = subprocess.run([str(arg) for arg in command], env=env, cwd=cwd,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=900)
    if log is not None:
        log.write_text(result.stdout)
    if result.returncode:
        raise RuntimeError("{} exited {}:\n{}".format(command, result.returncode, result.stdout[-24000:]))
    return result.stdout


def check_headers(headers):
    for name in ("llvm-c/Core.h", "llvm/Config/llvm-config.h"):
        if not (headers / "include" / name).is_file():
            raise ValueError("missing consumer development header: " + name)
    config = (headers / "include/llvm/Config/llvm-config.h").read_text()
    for field, value in (("MAJOR", "23"), ("MINOR", "1"), ("PATCH", "6")):
        if not re.search(r"^#define LLVM_VERSION_" + field + r" " + value + r"$", config, re.M):
            raise ValueError("consumer headers must match LLVM 23.1.6")


def validate(sdk, target, work, headers):
    check_headers(headers)
    work.mkdir(parents=True, exist_ok=False)
    consumer = work / "consumer package"
    consumer.mkdir()
    subprocess.run(["git", "init", "-q", str(consumer)], check=True)
    subprocess.run(["git", "-C", str(consumer), "fetch", "--depth=1",
                    CONSUMER_REPOSITORY, CONSUMER_REVISION], check=True)
    subprocess.run(["git", "-C", str(consumer), "checkout", "--detach", "FETCH_HEAD"], check=True)
    revision = subprocess.check_output(["git", "-C", str(consumer), "rev-parse", "HEAD"], text=True).strip()
    if revision != CONSUMER_REVISION:
        raise ValueError("LLGo consumer source revision mismatch")
    binary = consumer / "bin/llgo"
    binary.parent.mkdir(exist_ok=True)
    # Headers are a separate CI artifact from this exact LLVM build. They are
    # used only to compile the consumer, never inserted in the runtime payload.
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
                 "DYLD_FALLBACK_LIBRARY_PATH", "GOOS", "GOARCH", "GOFLAGS", "GOWORK"):
        env.pop(name, None)
    env.update(GOTOOLCHAIN="local", CGO_ENABLED="1", LLGO_BUILD_CACHE="0",
               XDG_CACHE_HOME=str(work / "empty cache"))
    if sys.platform == "darwin":
        env["SDKROOT"] = run(["xcrun", "--show-sdk-path"], env, source).strip()
    llvm_version = run([staged_sdk / "bin/llvm-config", "--version"], env, source).strip()
    if llvm_version != "23.1.6":
        raise ValueError("expected LLVM 23.1.6 consumer runtime, got " + llvm_version)
    build_env = dict(env, CC=shlex.join([str(staged_sdk / "bin/clang")]),
                     CXX=shlex.join([str(staged_sdk / "bin/clang++")]),
                     CGO_CPPFLAGS=shlex.join(["-I" + str(headers / "include")]),
                     CGO_CXXFLAGS="-std=c++17",
                     CGO_LDFLAGS=shlex.join(["-L" + str(staged_sdk / "lib"),
                                           "-lLLVM-23", "-Wl,-rpath," + str(staged_sdk / "lib")]))
    run(["go", "build", "-tags=byollvm", "-o", binary, "./cmd/llgo"],
        build_env, consumer, log=work / "build-consumer.log")
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
            trace = run(command + ["-target", board, "-o", output, "./embedded"], env, source,
                        log=source / (board + "-" + mode + ".log"))
            header = Path(str(output) + ".elf").read_bytes()[:20]
            if header[:6] != b"\x7fELF\x01\x01" or int.from_bytes(header[18:20], "little") != machine:
                raise ValueError("LLGo firmware did not produce the expected Xtensa/RISC-V ELF")
            if "ESP Clang not found" in trace or "crosscompile/clang/bin/" not in trace:
                raise ValueError("LLGo did not use the newly installed runtime payload")
            results.append(board + ":" + mode)
            print("PASS " + results[-1], flush=True)
    (work / "consumer-results.json").write_text(json.dumps({
        "target": target, "consumer": version, "consumer_revision": revision,
        "llvm_version": llvm_version,
        "firmware": results, "status": "passed",
    }, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sdk", type=Path)
    parser.add_argument("target", choices=TARGETS)
    parser.add_argument("work", type=Path)
    parser.add_argument("--llvm-headers", type=Path, required=True)
    args = parser.parse_args()
    validate(args.sdk.resolve(), args.target, args.work.resolve(), args.llvm_headers.resolve())
