# Windows archive packaging

Windows tool aliases must retain their executable names: LLVM uses the image
name to select modes such as `clang++`, `ld.lld`, and `llvm-ranlib`. Store
byte-identical executable aliases as tar hard links. Do not replace them with
launcher programs or symbolic links. Avoid repacking through formats or tools
that flatten hard links, including ordinary ZIP archives.

`scripts/deduplicate_windows_tools.py` checks contents before linking files.
`release.sh` then creates the final `.tar.xz` with system tar and multi-threaded
xz compression. Extract it with tar or 7-Zip preserving hard links. Native Windows CI
checks that extraction restores hard links before running the complete payload
validation. Every tool, target, runtime, and license remains in the archive.

For packaging-only releases, publish a new payload revision and keep the LLVM
source contract fixed. Existing release assets and their checksums must remain
unchanged. Consumers should update the payload version and SHA-256 together
after the new assets have passed validation and been published.

The current payload is Espressif LLVM 22.1.4, built with the pinned LLVM 22
llvm-mingw bootstrap. The separate Windows x86 host SDK stays on upstream LLVM
22.1.8. LLVM 23 adoption is deferred while its upstream issue blocks LLGo;
packaging changes must keep the LLVM 22 source and SDK contracts intact.

Windows Clang, LLD and LLVM utilities share the packaged LLVM DLL. LLVM's
existing MinGW interface annotations and hidden default visibility restrict
exports to its public interface. Keep the upstream `--export-all-symbols`
linker option: explicit runtime exports otherwise suppress ordinary public
exports. Hidden internal symbols remain excluded even with that option. The native
and cross-build validators check PE machine types, complete DLL dependencies,
and actual LLVM DLL imports before running code-generation and LTO tests.
Clang's own C++ library remains statically linked into its tools; this avoids
requiring a second Clang DLL interface while still sharing the LLVM backends.

Before the full build, a small DLL test uses the patched LLVM headers to link
C API calls, C++ methods/static data, explicit template instantiations and a
virtual factory/delete pair. Native Windows CI executes it with the payload's
runtime DLLs. This test is an artifact for CI only, not part of the toolchain.
