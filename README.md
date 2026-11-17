# Espressif LLVM executable toolchains

LLVM 23 development and releases are maintained on the `llvm23` branch.
Target LLVM 23 pull requests at that branch; `main` retains the LLVM 22 line.

The `clang-esp-<payload>-<host>.tar.xz` archives are runtime toolchains for
Clang, LLVM and LLD, including LLGo's embedded targets. All eight compiler
backends and the ESP32/ESP8266 assembler aliases are retained.

Unix archives omit LLVM/Clang development headers, static SDK libraries,
CMake/pkg-config metadata and benchmark tools. They retain shared libraries,
Clang resource headers, target builtins, host C++ standard library files and
licenses. Identical executables and shared libraries use tar hard links to
preserve every loader/driver name without storing duplicate contents.

Building LLVM bindings or LLGo itself still requires a separately pinned
development SDK. Existing LLGo SDK downloads remain on their published SDK
versions; runtime download pins can move independently. The Windows x86 host
SDK is likewise independent of the ESP executable payload.

`./release.sh <host>` builds, packages and tests a toolchain. Unix builds
also hide the development installation and retest the extracted archive.
CI runs actual LLGo ESP32/ESP32-C3 firmware builds with default and Full LTO
against all four Unix runtime packages, without extracting a development SDK.

A release contains seven ESP runtime archives (Linux and macOS x86_64/ARM64,
and Windows x86/x86_64/ARM64), plus the Windows x86 development SDK. The release
workflow calls the SDK workflow and waits for its native Windows validation.
Before upload, it checks that all eight archives and their SHA-256 sidecars are
present and valid; the combined checksum file covers all eight archives.

Create the payload tag from the validated `llvm23` commit, or dispatch the
release workflow with `--ref llvm23` and `upload=true`. The tag must match
`ESP_LLVM_PAYLOAD_VERSION` in `toolchain.env`. Manually created release tags are
bound to the workflow commit. LLVM 23 releases do not replace the repository's
default Latest release while the LLVM 22 line remains on `main`.
