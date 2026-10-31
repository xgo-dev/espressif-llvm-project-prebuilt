# Espressif LLVM executable toolchains

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
