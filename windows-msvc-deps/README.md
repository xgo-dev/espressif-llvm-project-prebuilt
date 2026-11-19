# Windows MSVC LLVM dependencies

The official LLVM 23.1.6 Windows development archives enable zlib and zstd but
omit their static libraries. This independent workflow supplies `zs.lib` and
`zstd_static.lib`, headers, CMake/pkg-config metadata, licenses and a build
manifest for x64 and ARM64 host applications that statically link LLVM.

`contract.json` pins the original source archives, official LLVM SDKs and
FileCheck source by SHA-256. Native Windows jobs build Release `/MT` libraries
with the corresponding official SDK's clang-cl, inspect every archive member's
machine type and CRT directives, and package the results. After extracting the
ZIP into a different path containing spaces, each job links and runs LLVM zlib
and zstd round trips and FileCheck. Executable imports must not introduce
compression DLLs or a dynamic CRT. The SDK is neither rebuilt nor redistributed.

Run `windows-msvc-deps/run.ps1 -Architecture x86_64` (or `aarch64`) on the
matching Windows host with Visual Studio C++ tools, Python 3.12, CMake and Ninja.
`RUNNER_TEMP` must point to an empty work area for a new build. Output is in
`dist/`. The `Windows MSVC dependencies` workflow also supports manual builds.

Publish by pushing a **new** tag `llvm-msvc-deps-<version>` matching the checked-in
contract. Both native jobs must pass before the release job verifies and uploads
the two ZIPs, checksums and validation reports. Existing assets are never
overwritten. This release does not run the ESP or x86 MinGW SDK builds.

Consumers pin the release tag, producer revision and per-architecture archive
SHA-256, verify downloaded bytes, and add the extracted `lib` directory to their
LLVM link search path. CMake consumers can also use the installed headers and
metadata. These packages are for the MSVC host SDK, independently of the C ABI
selected for LLGo programs and of the ESP toolchain under `crosscompile/clang`.
