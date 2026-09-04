#!/bin/bash
# Espressif LLVM Cross-Platform Release Builder
# Usage: ./release.sh <platform>
# Based on the working Makefile and build script

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/toolchain.env"

TAG="${TAG:-$ESP_LLVM_PAYLOAD_VERSION}"
VERSION_STRING="$TAG"
LLVM_PROJECTDIR="${LLVM_PROJECTDIR:-llvm-project}"
BUILD_DIR_BASE="${BUILD_DIR_BASE:-build}"
LLVM_REF="${LLVM_REF:-$ESP_LLVM_SOURCE_REF}"
LLVM_EXPECTED_VERSION="${LLVM_EXPECTED_VERSION:-$ESP_LLVM_EXPECTED_VERSION}"
LLVM_EXPECTED_MAJOR="${LLVM_EXPECTED_VERSION%%.*}"
LLVM_SOURCE_REVISION=""
LLVM_SOURCE_PATCH_SHA256=""
CCACHE_EXECUTABLE=""
# The pinned Espressif build scripts omit opt from their default Toolchain
# distribution. LLGo needs it for IR verification and pass-plugin smoke tests,
# so keep the upstream component set and add opt to the packaged payload.
WINDOWS_DISTRIBUTION_COMPONENTS="clang-format;clang-resource-headers;clang-tidy;clang;clangd;dsymutil;llc;lld;llvm-ar;llvm-config;llvm-cov;llvm-cxxfilt;llvm-dwarfdump;llvm-nm;llvm-objcopy;llvm-objdump;llvm-profdata;llvm-ranlib;llvm-readelf;llvm-readobj;llvm-size;llvm-strings;llvm-strip;llvm-symbolizer;LTO;opt"

# Resolve source and build roots before changing into the per-platform build
# directory. This keeps local qualification builds isolated from the checkout
# and makes absolute paths behave the same way as the workflow defaults.
if [[ "$LLVM_PROJECTDIR" != /* ]]; then
    LLVM_PROJECTDIR="$SCRIPT_DIR/$LLVM_PROJECTDIR"
fi
if [[ "$BUILD_DIR_BASE" != /* ]]; then
    BUILD_DIR_BASE="$SCRIPT_DIR/$BUILD_DIR_BASE"
fi

# Detect host system
if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "win32" ]] || [[ -n "${WINDIR:-}" ]]; then
    HOST_OS="Windows_NT"
else
    HOST_OS="$(uname -s)"
fi

# Set macOS SDK root if on macOS
if [[ "$HOST_OS" == "Darwin" ]]; then
    if [[ -z "${SDKROOT:-}" ]]; then
        detected_sdkroot="$(xcrun --show-sdk-path)"
        export SDKROOT="$detected_sdkroot"
        echo "Setting SDKROOT to: $SDKROOT"
    fi
fi

# Supported build targets (native builds only)
VALID_TARGETS="aarch64-apple-darwin aarch64-linux-gnu x86_64-apple-darwin x86_64-linux-gnu x86_64-w64-mingw32"

# Function to show usage
show_usage() {
    echo "Espressif LLVM Cross-Platform Release Builder"
    echo ""
    echo "Usage: $0 <platform>"
    echo ""
    echo "Supported platforms:"
    for target in $VALID_TARGETS; do
        echo "  - $target"
    done
    echo ""
    echo "Environment variables:"
    echo "  TAG              - Payload version (default: $TAG)"
    echo "  LLVM_REF         - Exact Espressif LLVM tag/ref (default: $LLVM_REF)"
    echo "  LLVM_EXPECTED_VERSION - llvm-config version prefix (default: $LLVM_EXPECTED_VERSION)"
    echo "  LLVM_PROJECTDIR  - LLVM source directory (default: $LLVM_PROJECTDIR)"
    echo "  BUILD_DIR_BASE   - Build directory base (default: $BUILD_DIR_BASE)"
    echo "  REQUIRE_CCACHE   - Fail if ccache is unavailable (default: 0)"
    echo ""
}

# Function to download LLVM source
download_llvm_source() {
    if [[ ! -d "$LLVM_PROJECTDIR" ]]; then
        echo "Cloning LLVM project ref $LLVM_REF..."
        git clone --branch "$LLVM_REF" --depth=1 https://github.com/espressif/llvm-project "$LLVM_PROJECTDIR"
    else
        local current_ref
        current_ref="$(git -C "$LLVM_PROJECTDIR" describe --tags --exact-match 2>/dev/null || true)"
        if [[ "$current_ref" != "$LLVM_REF" ]]; then
            echo "Error: $LLVM_PROJECTDIR is at '${current_ref:-an untagged revision}', expected $LLVM_REF" >&2
            echo "Use a fresh LLVM_PROJECTDIR instead of silently reusing different LLVM sources." >&2
            return 1
        fi
        echo "LLVM project directory already contains $LLVM_REF."
    fi
    LLVM_SOURCE_REVISION="$(git -C "$LLVM_PROJECTDIR" rev-parse HEAD)"

    local patch_path
    local patch_files=()
    for patch_path in $ESP_LLVM_PATCHES; do
        local absolute_patch="$SCRIPT_DIR/$patch_path"
        if [[ ! -f "$absolute_patch" ]]; then
            echo "Error: LLVM source patch not found: $patch_path" >&2
            return 1
        fi
        if git -C "$LLVM_PROJECTDIR" apply --check "$absolute_patch" 2>/dev/null; then
            echo "Applying LLVM source patch: $patch_path"
            git -C "$LLVM_PROJECTDIR" apply "$absolute_patch"
        elif git -C "$LLVM_PROJECTDIR" apply --reverse --check "$absolute_patch" 2>/dev/null; then
            echo "LLVM source patch is already applied: $patch_path"
        else
            echo "Error: LLVM source patch does not apply cleanly: $patch_path" >&2
            return 1
        fi
        patch_files+=("$absolute_patch")
    done

    if command -v sha256sum >/dev/null 2>&1; then
        LLVM_SOURCE_PATCH_SHA256="$(cat "${patch_files[@]}" | sha256sum | cut -d' ' -f1)"
    else
        LLVM_SOURCE_PATCH_SHA256="$(cat "${patch_files[@]}" | shasum -a 256 | cut -d' ' -f1)"
    fi
}

# Base CMake arguments - common to all platforms
get_base_cmake_args() {
    cat << 'EOF'
-G Ninja
-DCMAKE_BUILD_TYPE=Release
-DLLVM_INCLUDE_TESTS=OFF
-DLLVM_ENABLE_TERMINFO=OFF
-DLLVM_ENABLE_ZSTD=OFF
-DLLVM_ENABLE_Z3_SOLVER=OFF
-DLLVM_ENABLE_OCAMLDOC=OFF
-DLLVM_ENABLE_LIBXML2=OFF
-DLLVM_TOOL_CLANG_TOOLS_EXTRA_BUILD=OFF
-DCLANG_ENABLE_ARCMT=OFF
-DLLVM_TARGETS_TO_BUILD=X86;ARM;AArch64;AVR;Mips;RISCV;WebAssembly
-DLLVM_EXPERIMENTAL_TARGETS_TO_BUILD=Xtensa
-DLLVM_ENABLE_PROJECTS=clang;lld
-DLLVM_ENABLE_RUNTIMES=compiler-rt;libcxx;libcxxabi;libunwind
-DLLVM_POLLY_LINK_INTO_TOOLS=ON
-DLLVM_BUILD_EXTERNAL_COMPILER_RT=ON
-DLLVM_ENABLE_EH=ON
-DLLVM_ENABLE_RTTI=ON
-DLLVM_INCLUDE_DOCS=OFF
-DLLVM_INCLUDE_EXAMPLES=OFF
-DLLVM_INCLUDE_TESTS=OFF
-DLLVM_INCLUDE_BENCHMARKS=OFF
-DLLVM_BUILD_DOCS=OFF
-DLLVM_ENABLE_DOXYGEN=OFF
-DLLVM_INSTALL_UTILS=OFF
-DLLVM_ENABLE_Z3_SOLVER=OFF
-DLLVM_ENABLE_LIBEDIT=OFF
-DLLVM_OPTIMIZED_TABLEGEN=ON
-DLLVM_USE_RELATIVE_PATHS_IN_FILES=ON
-DLLVM_SOURCE_PREFIX=.
-DLIBCXX_INSTALL_MODULES=ON
-DCLANG_FORCE_MATCHING_LIBCLANG_SOVERSION=OFF
-DCOMPILER_RT_BUILD_SANITIZERS=OFF
-DCOMPILER_RT_BUILD_XRAY=OFF
-DCOMPILER_RT_BUILD_LIBFUZZER=OFF
-DCOMPILER_RT_BUILD_PROFILE=OFF
-DCOMPILER_RT_BUILD_MEMPROF=OFF
-DCOMPILER_RT_BUILD_ORC=OFF
-DCOMPILER_RT_BUILD_GWP_ASAN=OFF
-DCOMPILER_RT_BUILD_CTX_PROFILE=OFF
-DCMAKE_POSITION_INDEPENDENT_CODE=ON
-DLLVM_ENABLE_PER_TARGET_RUNTIME_DIR=OFF
-DCOMPILER_RT_DEFAULT_TARGET_ONLY=ON
-DCOMPILER_RT_USE_BUILTINS_LIBRARY=ON
-DLIBCXX_ENABLE_STATIC_ABI_LIBRARY=ON
-DLIBCXX_STATICALLY_LINK_ABI_IN_SHARED_LIBRARY=OFF
-DLIBCXX_STATICALLY_LINK_ABI_IN_STATIC_LIBRARY=ON
-DLIBCXX_USE_COMPILER_RT=ON
-DLIBCXX_HAS_ATOMIC_LIB=OFF
-DLIBCXXABI_ENABLE_STATIC_UNWINDER=ON
-DLIBCXXABI_STATICALLY_LINK_UNWINDER_IN_SHARED_LIBRARY=OFF
-DLIBCXXABI_STATICALLY_LINK_UNWINDER_IN_STATIC_LIBRARY=ON
-DLIBCXXABI_USE_COMPILER_RT=ON
-DLIBCXXABI_USE_LLVM_UNWINDER=ON
-DLIBUNWIND_USE_COMPILER_RT=ON
-DSANITIZER_CXX_ABI=libc++
-DSANITIZER_TEST_CXX=libc++
-DLLVM_LINK_LLVM_DYLIB=ON
-DCLANG_LINK_CLANG_DYLIB=ON
-DCMAKE_STRIP=/usr/bin/strip
EOF
}

# macOS-specific CMake arguments
get_macos_cmake_args() {
    local target="$1"
    local arch

    if [[ "$target" == "aarch64-apple-darwin" ]]; then
        arch="arm64"
    else
        arch="x86_64"
    fi

    cat << EOF
-DLLVM_BUILD_LLVM_C_DYLIB=ON
-DLLVM_ENABLE_LIBCXX=ON
-DLIBCXX_PSTL_BACKEND=libdispatch
-DCMAKE_OSX_SYSROOT=$SDKROOT
-DCMAKE_OSX_ARCHITECTURES=$arch
-DLIBCXXABI_USE_SYSTEM_LIBS=ON
EOF
}

# Linux-specific CMake arguments
get_linux_cmake_args() {
    cat << 'EOF'
-DLLVM_ENABLE_LIBXML2=OFF
-DLLVM_ENABLE_LIBCXX=OFF
-DCLANG_DEFAULT_CXX_STDLIB=libstdc++
-DLLVM_BUILD_LLVM_DYLIB=ON
-DCOMPILER_RT_USE_LLVM_UNWINDER=ON
EOF
}

# Function to get platform-specific CMake arguments
get_platform_cmake_args() {
    local target="$1"

    case "$target" in
        *-apple-darwin)
            get_macos_cmake_args "$target"
            ;;
        *-linux-gnu*)
            get_linux_cmake_args
            ;;
        *)
            echo "Unknown target platform: $target" >&2
            return 1
            ;;
    esac
}

# Function to set up build environment (native builds only)
setup_build_env() {
    local target="$1"

    # All builds are native, no cross-compilation setup needed
    echo "Setting up native build environment for $target"
}

# Function to get number of CPU cores
get_cpu_cores() {
    if [[ "$HOST_OS" == "Darwin" ]]; then
        sysctl -n hw.ncpu
    elif [[ "$HOST_OS" == "Linux" ]]; then
        nproc
    elif [[ "$HOST_OS" == "Windows_NT" ]]; then
        echo "${NUMBER_OF_PROCESSORS:-4}"
    else
        echo "4"
    fi
}

setup_ccache() {
    CCACHE_EXECUTABLE="$(command -v ccache || true)"
    if [[ -z "$CCACHE_EXECUTABLE" ]]; then
        if [[ "${REQUIRE_CCACHE:-0}" == "1" ]]; then
            echo "Error: REQUIRE_CCACHE=1 but ccache is unavailable" >&2
            return 1
        fi
        echo "ccache is unavailable; continuing without a compiler cache"
        return
    fi

    echo "Using ccache compiler launcher: $CCACHE_EXECUTABLE"
    "$CCACHE_EXECUTABLE" --version
    "$CCACHE_EXECUTABLE" --zero-stats
}

get_ccache_cmake_args() {
    if [[ -n "$CCACHE_EXECUTABLE" ]]; then
        printf '%s\n' \
            "-DCMAKE_C_COMPILER_LAUNCHER=$CCACHE_EXECUTABLE" \
            "-DCMAKE_CXX_COMPILER_LAUNCHER=$CCACHE_EXECUTABLE"
    fi
}

validate_ccache_configuration() {
    local build_dir="$1"
    local cache_file="$build_dir/CMakeCache.txt"
    local variable value

    [[ -n "$CCACHE_EXECUTABLE" ]] || return
    [[ -f "$cache_file" ]] || {
        echo "Error: CMake cache is missing from $build_dir" >&2
        return 1
    }
    for variable in CMAKE_C_COMPILER_LAUNCHER CMAKE_CXX_COMPILER_LAUNCHER; do
        value="$(sed -n "s/^${variable}:[^=]*=//p" "$cache_file" | tail -n 1)"
        if [[ "$value" != "$CCACHE_EXECUTABLE" ]]; then
            echo "Error: $variable is '$value', expected '$CCACHE_EXECUTABLE'" >&2
            return 1
        fi
    done
}

# Function to create release directory structure
create_release_structure() {
    local target="$1"
    local install_dir="$2"
    local release_dir="dist/${target}/esp-clang"

    echo "Creating release structure in $release_dir..."

    # Create release directory
    rm -rf "dist/${target}"
    mkdir -p "$release_dir"

    # Copy installation files
    if [[ -d "$install_dir" ]]; then
        cp -r "$install_dir"/* "$release_dir"/
    else
        echo "Warning: Install directory $install_dir not found"
        return 1
    fi

    install_clang_driver_aliases "$release_dir" "$target"

    write_payload_manifest "$release_dir" "$target"

    install_payload_licenses "$release_dir"

    validate_release "$release_dir" "$target"

    echo "Release directory created: $release_dir"
    echo "Contents:"
    ls -la "$release_dir"

    # Create tarball
    echo "Creating tarball package..."
    mkdir -p dist
    cd "dist/${target}"
    tar -cJf "../clang-esp-${VERSION_STRING}-${target}.tar.xz" esp-clang/
    cd - > /dev/null

    write_archive_checksum "dist/clang-esp-${VERSION_STRING}-${target}.tar.xz"

    echo "Tarball created: dist/clang-esp-${VERSION_STRING}-${target}.tar.xz"
    echo "Checksum created: dist/clang-esp-${VERSION_STRING}-${target}.tar.xz.sha256"
    echo "Package size: $(du -h "dist/clang-esp-${VERSION_STRING}-${target}.tar.xz" | cut -f1)"
}

install_clang_driver_aliases() {
    local release_dir="$1"
    local target="$2"
    local exe_suffix=""
    local alias

    if [[ "$target" == *-w64-mingw32 ]]; then
        exe_suffix=".exe"
    fi
    [[ -f "$release_dir/bin/clang$exe_suffix" ]] || {
        echo "Error: clang$exe_suffix is missing; cannot install driver aliases" >&2
        return 1
    }

    # Espressif's Xtensa driver runs an external integrated assembler selected
    # from the CPU name. These aliases are required on every host, not only on
    # Windows: ESP32 uses xtensa-esp32-elf-clang-as and ESP8266 uses
    # xtensa-esp8266-elf-clang-as. Keep clang-as too for the generic driver
    # contract.
    for alias in \
        clang-as \
        xtensa-esp32-elf-clang-as \
        xtensa-esp8266-elf-clang-as; do
        rm -f "$release_dir/bin/$alias$exe_suffix"
        if [[ -n "$exe_suffix" ]]; then
            # CPack drops Windows symlinks. Regular PE aliases survive archive
            # creation and native Windows extraction reliably.
            cp "$release_dir/bin/clang.exe" "$release_dir/bin/$alias.exe"
        else
            ln -s clang "$release_dir/bin/$alias"
        fi
    done
}

write_payload_manifest() {
    local release_dir="$1"
    local target="$2"
    local llvm_targets="X86;ARM;AArch64;AVR;Mips;RISCV;WebAssembly;Xtensa"
    cat > "$release_dir/LLGO-LLVM-MANIFEST.txt" << EOF
payload_version=$VERSION_STRING
llvm_source_repository=https://github.com/espressif/llvm-project
llvm_source_ref=$LLVM_REF
llvm_source_revision=$LLVM_SOURCE_REVISION
llvm_source_patches=$ESP_LLVM_PATCHES
llvm_source_patch_sha256=$LLVM_SOURCE_PATCH_SHA256
llvm_expected_version=$LLVM_EXPECTED_VERSION
llvm_targets=$llvm_targets
host_target=$target
EOF
    if [[ "$target" == *-w64-mingw32 ]]; then
        cat >> "$release_dir/LLGO-LLVM-MANIFEST.txt" << EOF
windows_build_scripts_repository=$ESP_LLVM_BUILD_SCRIPTS_REPOSITORY
windows_build_scripts_revision=$ESP_LLVM_BUILD_SCRIPTS_REF
windows_bootstrap=llvm-mingw-$LLVM_MINGW_VERSION
windows_bootstrap_sha256=$LLVM_MINGW_LINUX_X86_64_SHA256
EOF
    fi
}

write_archive_checksum() {
    local archive="$1"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$archive" > "$archive.sha256"
    else
        shasum -a 256 "$archive" > "$archive.sha256"
    fi
}

install_payload_licenses() {
    local release_dir="$1"
    local license_dir="$release_dir/third-party-licenses"
    local component source destination

    mkdir -p "$license_dir"
    cat > "$release_dir/THIRD-PARTY-LICENSES.txt" << 'EOF'
This product embeds and uses the following pieces of software which have
additional or alternate licenses:
 - LLVM: third-party-licenses/LLVM-LICENSE.txt
 - Clang: third-party-licenses/CLANG-LICENSE.txt
 - lld: third-party-licenses/LLD-LICENSE.txt
 - compiler-rt: third-party-licenses/COMPILER-RT-LICENSE.txt
 - libc++: third-party-licenses/LIBCXX-LICENSE.txt
 - libc++abi: third-party-licenses/LIBCXXABI-LICENSE.txt
 - libunwind: third-party-licenses/LIBUNWIND-LICENSE.txt
EOF

    for component in llvm clang lld compiler-rt libcxx libcxxabi libunwind; do
        source="$LLVM_PROJECTDIR/$component/LICENSE.TXT"
        destination="$(printf '%s' "$component" | tr '[:lower:]' '[:upper:]')-LICENSE.txt"
        [[ -f "$source" ]] || {
            echo "Error: $component license is missing from $LLVM_PROJECTDIR" >&2
            return 1
        }
        cp "$source" "$license_dir/$destination"
    done
}

validate_release() {
    local release_dir="$1"
    local target="$2"
    local actual_version targets tool tool_version target_name test_dir exe_suffix

    exe_suffix=""
    if [[ "$target" == *-w64-mingw32 ]]; then
        exe_suffix=".exe"
    fi

    # Keep this list aligned with LLGo's actual embedded build, archive,
    # inspection, size-report, and release-smoke consumers. The payload may
    # contain additional LLVM utilities, but these are its supported contract.
    local required_tools=(
        clang clang++ clang-as
        xtensa-esp32-elf-clang-as xtensa-esp8266-elf-clang-as
        ld.lld lld llc opt
        llvm-ar llvm-config llvm-nm llvm-objcopy llvm-objdump llvm-ranlib
        llvm-readelf llvm-readobj llvm-size llvm-strip
    )
    for tool in "${required_tools[@]}"; do
        if [[ ! -x "$release_dir/bin/$tool$exe_suffix" ]]; then
            echo "Error: required tool $tool is missing from $release_dir/bin" >&2
            return 1
        fi
    done

    actual_version="$("$release_dir/bin/llvm-config$exe_suffix" --version)"
    if [[ "$actual_version" != "$LLVM_EXPECTED_VERSION"* ]]; then
        echo "Error: llvm-config reports $actual_version, expected $LLVM_EXPECTED_VERSION.x" >&2
        return 1
    fi
    for tool in \
        clang clang++ clang-as ld.lld llc opt \
        llvm-ar llvm-nm llvm-objcopy llvm-objdump llvm-ranlib \
        llvm-readelf llvm-readobj llvm-size llvm-strip; do
        tool_version="$("$release_dir/bin/$tool$exe_suffix" --version)"
        if [[ "$tool_version" != *"$LLVM_EXPECTED_VERSION"* ]]; then
            echo "Error: $tool reports $tool_version, expected $LLVM_EXPECTED_VERSION.x" >&2
            return 1
        fi
    done

    targets="$("$release_dir/bin/llvm-config$exe_suffix" --targets-built)"
    local required_targets=(X86 ARM AArch64 AVR Mips RISCV WebAssembly Xtensa)
    for target_name in "${required_targets[@]}"; do
        if [[ " $targets " != *" $target_name "* ]]; then
            echo "Error: required LLVM target $target_name is missing: $targets" >&2
            return 1
        fi
    done

    if [[ "$target" == *-w64-mingw32 ]]; then
        # Espressif's MinGW distribution is an executable target toolchain,
        # not the LLVM development SDK used to build LLGo's Go bindings. Its
        # pinned component set intentionally installs only the public LTO
        # interface, rather than llvm-headers and LLVM CMake package files.
        [[ -f "$release_dir/include/llvm-c/lto.h" ]] || {
            echo "Error: LLVM LTO header is missing from Windows payload" >&2
            return 1
        }
        [[ -f "$release_dir/bin/libLTO.dll" ]] || {
            echo "Error: LLVM LTO DLL is missing from Windows payload" >&2
            return 1
        }
        [[ -f "$release_dir/lib/libLTO.dll.a" ]] || {
            echo "Error: LLVM LTO import library is missing from Windows payload" >&2
            return 1
        }
        [[ ! -e "$release_dir/lib/clang-runtimes/multilib.yaml" ]] || {
            echo "Error: runtime-free Windows payload contains a stale multilib.yaml" >&2
            return 1
        }
    else
        [[ -f "$release_dir/include/llvm-c/Core.h" ]] || {
            echo "Error: llvm-c/Core.h is missing" >&2
            return 1
        }
        [[ -f "$release_dir/lib/cmake/llvm/LLVMConfig.cmake" ]] || {
            echo "Error: LLVMConfig.cmake is missing" >&2
            return 1
        }
    fi
    [[ -f "$release_dir/THIRD-PARTY-LICENSES.txt" ]] || {
        echo "Error: third-party license summary is missing" >&2
        return 1
    }
    for component in LLVM CLANG LLD COMPILER-RT LIBCXX LIBCXXABI LIBUNWIND; do
        [[ -f "$release_dir/third-party-licenses/$component-LICENSE.txt" ]] || {
            echo "Error: $component license is missing from the payload" >&2
            return 1
        }
    done
    if [[ "$target" != *-w64-mingw32 ]] &&
       ! find "$release_dir/lib" -maxdepth 1 -name "libLLVM-$LLVM_EXPECTED_MAJOR.*" -print -quit | grep -q .; then
        echo "Error: libLLVM-$LLVM_EXPECTED_MAJOR shared library is missing" >&2
        return 1
    fi

    test_dir="$(mktemp -d)"
    cat > "$test_dir/verify.ll" << 'EOF'
define i32 @llgo_esp_verify_smoke(i32 %a, i32 %b) {
  %sum = add i32 %a, %b
  ret i32 %sum
}
EOF
    if ! "$release_dir/bin/opt$exe_suffix" -passes=verify -disable-output \
        "$test_dir/verify.ll"; then
        rm -rf "$test_dir"
        echo "Error: opt failed to verify valid LLVM IR" >&2
        return 1
    fi

    if [[ "$target" != *-w64-mingw32 ]]; then
        cat > "$test_dir/llvm-c-sdk.c" << EOF
#include <llvm-c/Core.h>
int main(void) {
  unsigned major = 0, minor = 0, patch = 0;
  LLVMGetVersion(&major, &minor, &patch);
  return major == $LLVM_EXPECTED_MAJOR ? 0 : 1;
}
EOF
        if ! "$release_dir/bin/clang" \
                -I"$release_dir/include" "$test_dir/llvm-c-sdk.c" \
                -L"$release_dir/lib" "-Wl,-rpath,$release_dir/lib" \
                "-lLLVM-$LLVM_EXPECTED_MAJOR" \
                -o "$test_dir/llvm-c-sdk" ||
           ! env \
                DYLD_LIBRARY_PATH="$release_dir/lib${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}" \
                LD_LIBRARY_PATH="$release_dir/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
                "$test_dir/llvm-c-sdk"; then
            rm -rf "$test_dir"
            echo "Error: packaged LLVM C SDK failed to compile, link, or run" >&2
            return 1
        fi
    fi

    cat > "$test_dir/schedule-region.S" << 'EOF'
        .text
        .begin schedule
        .global schedule_region_smoke_test
schedule_region_smoke_test:
        ret
        .end schedule
EOF
    local cpu
    for cpu in esp32 esp8266; do
        if ! "$release_dir/bin/clang++$exe_suffix" \
            --target=xtensa-esp-unknown-elf -mcpu="$cpu" -Werror -c \
            "$test_dir/schedule-region.S" \
            -o "$test_dir/schedule-region-$cpu.o"; then
            rm -rf "$test_dir"
            echo "Error: the Clang C++ driver cannot execute the $cpu Xtensa assembler" >&2
            return 1
        fi
    done

    cat > "$test_dir/codegen.c" << 'EOF'
int llgo_esp_codegen_smoke(int a, int b) { return a + b; }
EOF
    if ! "$release_dir/bin/clang$exe_suffix" --target=xtensa-esp-unknown-elf \
        -mcpu=esp32 -Werror -ffreestanding -c "$test_dir/codegen.c" -o "$test_dir/esp32.o" ||
       ! "$release_dir/bin/clang$exe_suffix" --target=xtensa-esp-unknown-elf \
        -mcpu=esp8266 -Werror -ffreestanding -c "$test_dir/codegen.c" -o "$test_dir/esp8266.o" ||
       ! "$release_dir/bin/clang$exe_suffix" --target=riscv32-esp-unknown-elf \
        -mcpu=generic-rv32 -Werror -g -ffreestanding -c \
        "$test_dir/codegen.c" -o "$test_dir/esp32c3.o"; then
        rm -rf "$test_dir"
        echo "Error: ESP32, ESP8266, or ESP32-C3 code generation failed" >&2
        return 1
    fi

    # Exercise every non-Espressif backend required by LLGo's embedded target
    # profiles. llvm-config proves registration; these compilations also prove
    # that the packaged Clang driver can reach each backend and emit an object.
    if ! "$release_dir/bin/clang$exe_suffix" --target=i386-unknown-none \
        -march=i386 -Werror -ffreestanding -c "$test_dir/codegen.c" -o "$test_dir/x86.o" ||
       ! "$release_dir/bin/clang$exe_suffix" --target=thumbv6m-none-eabi \
        -mcpu=cortex-m0 -Werror -ffreestanding -c "$test_dir/codegen.c" -o "$test_dir/arm.o" ||
       ! "$release_dir/bin/clang$exe_suffix" --target=aarch64-none-elf \
        -Werror -ffreestanding -c "$test_dir/codegen.c" -o "$test_dir/aarch64.o" ||
       ! "$release_dir/bin/clang$exe_suffix" --target=avr -mmcu=atmega328p \
        -Werror -ffreestanding -c "$test_dir/codegen.c" -o "$test_dir/avr.o" ||
       ! "$release_dir/bin/clang$exe_suffix" --target=mipsel-unknown-elf \
        -march=mips32r2 -mabi=32 -Werror -ffreestanding -c \
        "$test_dir/codegen.c" -o "$test_dir/mips.o" ||
       ! "$release_dir/bin/clang$exe_suffix" --target=wasm32-unknown-unknown \
        -Werror -ffreestanding -c "$test_dir/codegen.c" -o "$test_dir/wasm.o"; then
        rm -rf "$test_dir"
        echo "Error: a non-ESP embedded target failed Clang code generation" >&2
        return 1
    fi
    if ! "$release_dir/bin/ld.lld$exe_suffix" -r \
        "$test_dir/esp32.o" -o "$test_dir/esp32-linked.o" ||
       ! "$release_dir/bin/ld.lld$exe_suffix" -r \
        "$test_dir/esp8266.o" -o "$test_dir/esp8266-linked.o" ||
       ! "$release_dir/bin/ld.lld$exe_suffix" -r \
        "$test_dir/esp32c3.o" -o "$test_dir/esp32c3-linked.o"; then
        rm -rf "$test_dir"
        echo "Error: ESP32, ESP8266, or ESP32-C3 relocatable link failed" >&2
        return 1
    fi

    # Exercise the rest of LLGo's payload tool contract against a real ESP32-C3
    # object instead of merely checking filenames in the archive.
    if ! "$release_dir/bin/llvm-ar$exe_suffix" rcs \
            "$test_dir/libesp32c3-smoke.a" "$test_dir/esp32c3.o" ||
       ! "$release_dir/bin/llvm-ranlib$exe_suffix" \
            "$test_dir/libesp32c3-smoke.a" ||
       ! "$release_dir/bin/llvm-ar$exe_suffix" t \
            "$test_dir/libesp32c3-smoke.a" | grep -q 'esp32c3.o' ||
       ! "$release_dir/bin/llvm-nm$exe_suffix" --defined-only \
            "$test_dir/esp32c3.o" | grep -q 'llgo_esp_codegen_smoke' ||
       ! "$release_dir/bin/llvm-objdump$exe_suffix" --section-headers \
            "$test_dir/esp32c3.o" >/dev/null ||
       ! "$release_dir/bin/llvm-readelf$exe_suffix" --file-header \
            "$test_dir/esp32c3.o" >/dev/null ||
       ! "$release_dir/bin/llvm-readobj$exe_suffix" --file-headers \
            "$test_dir/esp32c3.o" >/dev/null ||
       ! "$release_dir/bin/llvm-size$exe_suffix" \
            "$test_dir/esp32c3.o" >/dev/null ||
       ! "$release_dir/bin/llvm-objcopy$exe_suffix" --strip-debug \
            "$test_dir/esp32c3.o" "$test_dir/esp32c3-objcopy.o" ||
       ! "$release_dir/bin/llvm-strip$exe_suffix" --strip-debug \
            -o "$test_dir/esp32c3-strip.o" "$test_dir/esp32c3.o"; then
        rm -rf "$test_dir"
        echo "Error: an LLVM archive or object-inspection payload tool failed" >&2
        return 1
    fi

    # lld performs the LTO plugin work in-process. This proves that the
    # packaged Clang can emit LLVM bitcode and the packaged linker can load and
    # lower it for an Espressif target.
    if ! "$release_dir/bin/clang$exe_suffix" --target=riscv32-esp-unknown-elf \
            -mcpu=generic-rv32 -Werror -ffreestanding -flto -c \
            "$test_dir/codegen.c" -o "$test_dir/esp32c3-lto.o" ||
       ! "$release_dir/bin/ld.lld$exe_suffix" -r \
            "$test_dir/esp32c3-lto.o" -o "$test_dir/esp32c3-lto-linked.o"; then
        rm -rf "$test_dir"
        echo "Error: ESP32-C3 LTO compile or plugin-backed link failed" >&2
        return 1
    fi
    for object in \
        "$test_dir/esp32.o" "$test_dir/esp8266.o" "$test_dir/esp32c3.o" \
        "$test_dir/x86.o" "$test_dir/arm.o" "$test_dir/aarch64.o" \
        "$test_dir/avr.o" "$test_dir/mips.o" "$test_dir/wasm.o" \
        "$test_dir/esp32-linked.o" "$test_dir/esp8266-linked.o" \
        "$test_dir/esp32c3-linked.o"; do
        if [[ ! -s "$object" ]]; then
            rm -rf "$test_dir"
            echo "Error: code generation produced an empty object: $object" >&2
            return 1
        fi
    done
    rm -rf "$test_dir"

    echo "Validated LLVM $actual_version payload with targets: $targets"
}

download_build_scripts() {
    local scripts_dir="${ESP_LLVM_BUILD_SCRIPTS_DIR:-$SCRIPT_DIR/esp-llvm-embedded-toolchain}"
    if [[ ! -d "$scripts_dir/.git" ]]; then
        git clone --filter=blob:none --no-checkout "$ESP_LLVM_BUILD_SCRIPTS_REPOSITORY" "$scripts_dir"
        git -C "$scripts_dir" fetch --depth=1 origin "$ESP_LLVM_BUILD_SCRIPTS_REF"
        git -C "$scripts_dir" checkout --detach FETCH_HEAD
    fi
    local actual_revision
    actual_revision="$(git -C "$scripts_dir" rev-parse HEAD)"
    if [[ "$actual_revision" != "$ESP_LLVM_BUILD_SCRIPTS_REF" ]]; then
        echo "Error: $scripts_dir is at $actual_revision, expected $ESP_LLVM_BUILD_SCRIPTS_REF" >&2
        return 1
    fi
    echo "$scripts_dir"
}

build_windows_platform() {
    local target="$1"
    local build_dir="$BUILD_DIR_BASE/$target"
    local install_dir="$SCRIPT_DIR/install/$target"
    local release_dir="$build_dir/unpack/esp-clang"
    local scripts_dir
    local ccache_args=()

    if ! command -v x86_64-w64-mingw32-clang >/dev/null 2>&1 ||
       ! command -v x86_64-w64-mingw32-clang++ >/dev/null 2>&1; then
        echo "Error: the pinned llvm-mingw bootstrap is required for $target" >&2
        return 1
    fi
    scripts_dir="$(download_build_scripts)"
    while IFS= read -r arg; do
        ccache_args+=("$arg")
    done < <(get_ccache_cmake_args)

    mkdir -p "$build_dir" "$install_dir"
    # LLVM_TOOLCHAIN_ENABLED_TARGETS only selects the Espressif runtime
    # families recognized by the pinned wrapper. LLVM_TARGETS_TO_BUILD expands
    # the compiler itself; validate_release enforces that complete contract.
    cmake -S "$scripts_dir" -B "$build_dir" -G Ninja \
        "${ccache_args[@]}" \
        -DFETCHCONTENT_SOURCE_DIR_LLVMPROJECT="$LLVM_PROJECTDIR" \
        -DFETCHCONTENT_QUIET=OFF \
        -DLLVM_TOOLCHAIN_C_LIBRARY=none \
        -DLLVM_TOOLCHAIN_CXX_LIBRARIES= \
        -DLLVM_TOOLCHAIN_RT_LIBRARIES= \
        -DLLVM_TOOLCHAIN_INCLUDE_GNU_BINUTILS=OFF \
        -DLLVM_TOOLCHAIN_ESPRESSIF=ON \
        -DLLVM_TOOLCHAIN_CROSS_BUILD_MINGW=ON \
        -DLLVM_TOOLCHAIN_HOST_TRIPLE="$target" \
        '-DLLVM_TOOLCHAIN_ENABLED_TARGETS=RISCV;Xtensa' \
        '-DLLVM_TARGETS_TO_BUILD=X86;ARM;AArch64;AVR;Mips;RISCV;WebAssembly' \
        -DLLVM_EXPERIMENTAL_TARGETS_TO_BUILD=Xtensa \
        -DLLVM_Toolchain_DISTRIBUTION_COMPONENTS="$WINDOWS_DISTRIBUTION_COMPONENTS" \
        -DLLVM_TOOLCHAIN_PACKAGE_NAME=esp-clang \
        -DESP_TOOLCHAIN_VER="esp-$VERSION_STRING" \
        -DCLANG_REPOSITORY_STRING=https://github.com/espressif/llvm-project.git \
        -DCPACK_ARCHIVE_THREADS=0 \
        -DCMAKE_INSTALL_PREFIX="$install_dir"
    validate_ccache_configuration "$build_dir"

    cmake --build "$build_dir" --target package-llvm-toolchain -j"$(get_cpu_cores)"
    cmake --build "$build_dir" --target unpack-llvm-toolchain
    if [[ ! -d "$release_dir" ]]; then
        echo "Error: packaged Windows toolchain is missing $release_dir" >&2
        return 1
    fi

    install_clang_driver_aliases "$release_dir" "$target"

    # This executable-only payload deliberately builds no target runtime
    # variants. The build scripts nevertheless install an empty multilib.yaml,
    # which makes Clang diagnose every ESP invocation and turns into a hard
    # error under LLGo's -Werror target profiles.
    rm -f "$release_dir/lib/clang-runtimes/multilib.yaml"
    rmdir "$release_dir/lib/clang-runtimes" 2>/dev/null || true
    write_payload_manifest "$release_dir" "$target"
    install_payload_licenses "$release_dir"

    # Cross-built PE executables cannot run on the Linux builder. Check the
    # complete archive on a native Windows runner before it can be released.
    for tool in \
        clang clang++ clang-as \
        xtensa-esp32-elf-clang-as xtensa-esp8266-elf-clang-as \
        ld.lld lld llc opt \
        llvm-ar llvm-config llvm-nm llvm-objcopy llvm-objdump llvm-ranlib \
        llvm-readelf llvm-readobj llvm-size llvm-strip; do
        [[ -f "$release_dir/bin/$tool.exe" ]] || {
            echo "Error: required Windows tool $tool.exe is missing" >&2
            return 1
        }
    done
    [[ -f "$release_dir/include/llvm-c/lto.h" ]] || {
        echo "Error: LLVM LTO header is missing from Windows payload" >&2
        return 1
    }
    [[ -f "$release_dir/bin/libLTO.dll" ]] || {
        echo "Error: LLVM LTO DLL is missing from Windows payload" >&2
        return 1
    }
    [[ -f "$release_dir/lib/libLTO.dll.a" ]] || {
        echo "Error: LLVM LTO import library is missing from Windows payload" >&2
        return 1
    }
    [[ -f "$release_dir/third-party-licenses/COPYING.MinGW-w64-runtime.txt" ]] || {
        echo "Error: MinGW runtime license is missing from Windows payload" >&2
        return 1
    }

    cmake --build "$build_dir" --target repack-llvm-toolchain
    local generated_archive
    generated_archive="$(find "$build_dir" -maxdepth 1 -name "clang-esp-*-$target.tar.xz" -print -quit)"
    if [[ -z "$generated_archive" ]]; then
        echo "Error: Windows package archive was not generated" >&2
        return 1
    fi
    mkdir -p "$SCRIPT_DIR/dist"
    local archive="$SCRIPT_DIR/dist/clang-esp-$VERSION_STRING-$target.tar.xz"
    cp "$generated_archive" "$archive"
    write_archive_checksum "$archive"
    echo "Windows cross-build completed: $archive"
}

# Main build function (native builds only)
build_platform() {
    local target="$1"

    if [[ "$target" == *-w64-mingw32 ]]; then
        build_windows_platform "$target"
        return
    fi

    echo "Building LLVM for platform: $target"
    echo "Version: $VERSION_STRING"
    echo "Host OS: $HOST_OS"
    echo "LLVM Source Ref: $LLVM_REF"
    echo "LLVM Source Revision: $LLVM_SOURCE_REVISION"
    echo ""

    # Create build and install directories
    local build_dir="$BUILD_DIR_BASE/$target"
    local install_dir="$PWD/install/$target"

    mkdir -p "$build_dir"
    mkdir -p "$install_dir"

    # Set up build environment
    setup_build_env "$target"

    # Prepare CMake arguments without flattening values through shell word
    # splitting (SDK paths may contain spaces).
    local cmake_args=()
    while IFS= read -r arg; do
        cmake_args+=("$arg")
    done < <({
        get_base_cmake_args
        get_platform_cmake_args "$target"
        get_ccache_cmake_args
        echo "-DCMAKE_INSTALL_PREFIX=$install_dir"
    })

    echo "CMake configuration:"
    printf '  %s\n' "${cmake_args[@]}"
    echo ""

    # Configure
    echo "Configuring build for $target..."
    cd "$build_dir"
    cmake "$LLVM_PROJECTDIR/llvm" "${cmake_args[@]}"
    validate_ccache_configuration "$build_dir"

    # Build
    echo "Building $target..."
    local cores
    cores="$(get_cpu_cores)"
    echo "Using $cores CPU cores for build"

    # Build only essential tools to significantly reduce build time
    # LLGo currently only requires libLLVM.dylib for dynamic libraries, and this dylib will be built with the following targets
    ninja -j"$cores" clang llvm-config llvm-ar llvm-nm llc opt lld

    # Install
    echo "Installing $target..."
    ninja install

    # Return to original directory
    cd - > /dev/null

    # Create release directory structure
    create_release_structure "$target" "$install_dir"

    echo ""
    echo "Build completed successfully for $target!"
    echo "Release directory: dist/${target}/esp-clang"
    echo "Install directory: $install_dir"
    echo "Tarball: dist/clang-esp-${VERSION_STRING}-${target}.tar.xz"
}

# Main script logic
main() {
    if [[ "$#" -eq 3 && "$1" == "--validate" ]]; then
        validate_release "$2" "$3"
        return
    fi
    if [[ $# -ne 1 ]]; then
        show_usage
        exit 1
    fi

    local target="$1"

    # Validate target
    local target_valid=0
    for valid_target in $VALID_TARGETS; do
        if [[ "$target" == "$valid_target" ]]; then
            target_valid=1
            break
        fi
    done

    if [[ $target_valid -eq 0 ]]; then
        echo "Error: Invalid target '$target'"
        echo ""
        show_usage
        exit 1
    fi

    # Check for required tools
    if ! command -v cmake >/dev/null 2>&1; then
        echo "Error: cmake is required but not installed"
        exit 1
    fi

    if ! command -v ninja >/dev/null 2>&1; then
        echo "Error: ninja is required but not installed"
        exit 1
    fi

    if ! command -v git >/dev/null 2>&1; then
        echo "Error: git is required but not installed"
        exit 1
    fi

    setup_ccache

    # Download LLVM source
    download_llvm_source

    # Build the platform
    build_platform "$target"
}

# Run main function when executed, while allowing validation helpers to be
# sourced by local tests and CI diagnostics.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
