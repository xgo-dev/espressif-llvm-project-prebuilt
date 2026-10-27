#!/usr/bin/env bash
# Build a real Windows/386 LLVM development SDK with the pinned MinGW compiler.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$root/toolchain.env"
source_repository="$LLVM_WINDOWS_SDK_REPOSITORY"
target=i686-w64-mingw32
source_dir="${LLVM_SDK_SOURCE_DIR:-$root/llvm-sdk-source}"
build_root="${LLVM_SDK_BUILD_DIR:-$root/build-sdk}"
jobs="${LLVM_SDK_JOBS:-$(getconf _NPROCESSORS_ONLN)}"
link_jobs="${LLVM_SDK_LINK_JOBS:-1}"
if [[ ! "$jobs" =~ ^[1-9][0-9]*$ || ! "$link_jobs" =~ ^[1-9][0-9]*$ ]]; then
    echo 'Error: LLVM_SDK_JOBS and LLVM_SDK_LINK_JOBS must be positive integers' >&2
    exit 1
fi
if ! command -v "$target-clang" >/dev/null || ! command -v "$target-clang++" >/dev/null; then
    echo 'Error: the pinned llvm-mingw bootstrap is required' >&2
    exit 1
fi
bootstrap="$(dirname "$(command -v "$target-clang")")/.."
shopt -s nullglob
runtime_dlls=("$bootstrap/$target/bin/"*.dll)
runtime_notices=("$bootstrap/$target/share/mingw32/"COPYING*)
if (( ${#runtime_dlls[@]} == 0 )); then
    echo "Error: bootstrap runtime DLLs are missing from $bootstrap/$target/bin" >&2
    exit 1
fi
if (( ${#runtime_notices[@]} == 0 )) || [[ ! -f "$bootstrap/LICENSE.TXT" ]]; then
    echo "Error: bootstrap redistribution licenses are missing from $bootstrap" >&2
    exit 1
fi
if [[ ! -e "$source_dir/.git" ]]; then
    git init "$source_dir"
fi
if ! git -C "$source_dir" remote get-url origin >/dev/null 2>&1; then
    git -C "$source_dir" remote add origin "$source_repository"
fi
# Check before checkout so a retry never discards local edits, including after
# an interrupted init/fetch. An empty initialized repository is safe to resume.
check_clean_source() {
    local source_status
    if ! source_status="$(git -C "$source_dir" status --porcelain)"; then
        echo "Error: cannot inspect LLVM source worktree $source_dir" >&2
        exit 1
    fi
    if [[ -n "$source_status" ]]; then
        echo "Error: LLVM source worktree $source_dir is dirty; use a clean source directory" >&2
        exit 1
    fi
}
check_clean_source
revision="$(git -C "$source_dir" rev-parse --verify HEAD 2>/dev/null || true)"
if [[ "$revision" != "$LLVM_WINDOWS_SDK_REF" ]]; then
    echo "Fetching LLVM revision $LLVM_WINDOWS_SDK_REF (current: ${revision:-no checkout})"
    # A reusable checkout may still have the previous source repository as
    # origin. Fetch the configured repository without rewriting that remote.
    if ! git -C "$source_dir" fetch --depth=1 "$source_repository" "$LLVM_WINDOWS_SDK_REF"; then
        echo "Error: could not fetch pinned LLVM revision $LLVM_WINDOWS_SDK_REF into $source_dir" >&2
        exit 1
    fi
    git -C "$source_dir" checkout --detach FETCH_HEAD
fi
check_clean_source
revision="$(git -C "$source_dir" rev-parse --verify HEAD)"
if [[ "$revision" != "$LLVM_WINDOWS_SDK_REF" ]]; then
    echo "Error: LLVM source $source_dir is at $revision, expected $LLVM_WINDOWS_SDK_REF" >&2
    exit 1
fi
if [[ ! -f "$source_dir/LICENSE.TXT" ]]; then
    echo "Error: LLVM source license is missing from $source_dir/LICENSE.TXT" >&2
    exit 1
fi
common=(
    -G Ninja -DCMAKE_BUILD_TYPE=Release -DLLVM_INCLUDE_TESTS=OFF
    -DLLVM_INCLUDE_BENCHMARKS=OFF -DLLVM_INCLUDE_EXAMPLES=OFF
    -DLLVM_INCLUDE_DOCS=OFF -DLLVM_ENABLE_ZLIB=OFF -DLLVM_ENABLE_ZSTD=OFF
    -DLLVM_ENABLE_LIBXML2=OFF -DLLVM_ENABLE_LIBEDIT=OFF
    -DLLVM_ENABLE_TERMINFO=OFF -DLLVM_ENABLE_Z3_SOLVER=OFF
    "-DLLVM_PARALLEL_LINK_JOBS=$link_jobs"
)
ccache_executable="$(command -v ccache || true)"
if [[ -n "$ccache_executable" ]]; then
    common+=("-DCMAKE_C_COMPILER_LAUNCHER=$ccache_executable" "-DCMAKE_CXX_COMPILER_LAUNCHER=$ccache_executable")
    "$ccache_executable" --zero-stats
elif [[ "${REQUIRE_CCACHE:-0}" == 1 ]]; then
    echo 'Error: REQUIRE_CCACHE=1 but ccache is unavailable' >&2
    exit 1
fi
cmake -S "$source_dir/llvm" -B "$build_root/native" "${common[@]}" \
    -DLLVM_TARGETS_TO_BUILD=X86
cmake --build "$build_root/native" --target llvm-tblgen llvm-min-tblgen -j"$jobs"
cmake -S "$source_dir/llvm" -B "$build_root/$target" "${common[@]}" \
    -DCMAKE_SYSTEM_NAME=Windows -DCMAKE_SYSTEM_PROCESSOR=i686 \
    -DCMAKE_C_COMPILER="$target-clang" -DCMAKE_CXX_COMPILER="$target-clang++" \
    -DCMAKE_RC_COMPILER="$target-windres" \
    -DCMAKE_FIND_ROOT_PATH="$bootstrap/$target" \
    -DCMAKE_FIND_ROOT_PATH_MODE_PROGRAM=NEVER \
    -DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=ONLY -DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY \
    -DLLVM_HOST_TRIPLE="$target" -DLLVM_DEFAULT_TARGET_TRIPLE="$target" \
    -DLLVM_NATIVE_TOOL_DIR="$build_root/native/bin" \
    '-DLLVM_TARGETS_TO_BUILD=X86;ARM;AArch64;AVR;Mips;RISCV;WebAssembly' \
    -DLLVM_ENABLE_RTTI=ON -DLLVM_ENABLE_EH=ON \
    -DLLVM_BUILD_LLVM_DYLIB=OFF -DLLVM_LINK_LLVM_DYLIB=OFF \
    -DCMAKE_INSTALL_PREFIX="$build_root/install/llvm-sdk"
# Static LLVM libraries avoid PE's export ordinal limit for the C++ API used by
# the Go binding. They are linked into the actual 32-bit compiler, not shipped
# as a 64-bit SDK relabelled with a different target triple.
cmake --build "$build_root/$target" --target install-llvm-headers \
    install-llvm-libraries install-llvm-config install-cmake-exports -j"$jobs"
sdk="$build_root/install/llvm-sdk"
cp "${runtime_dlls[@]}" "$sdk/bin/"
mkdir -p "$sdk/third-party-licenses"
cp "$source_dir/LICENSE.TXT" "$sdk/third-party-licenses/LLVM-LICENSE.txt"
# The bootstrap's redistribution notices cover the bundled UCRT/C++ runtimes.
cp "$bootstrap/LICENSE.TXT" "$sdk/third-party-licenses/LLVM-MinGW-LICENSE.txt"
cp "${runtime_notices[@]}" "$sdk/third-party-licenses/"
cat > "$sdk/LLGO-LLVM-MANIFEST.txt" <<MANIFEST
host_target=$target
llvm_version=$LLVM_WINDOWS_SDK_VERSION
source_repository=$source_repository
source_revision=$LLVM_WINDOWS_SDK_REF
windows_bootstrap=llvm-mingw-$LLVM_MINGW_VERSION
link_mode=static
MANIFEST
python3 "$root/scripts/validate_windows.py" "$sdk" "$target" --llvm-version "$LLVM_WINDOWS_SDK_VERSION"
mkdir -p "$root/dist"
archive="$root/dist/llvm-sdk-$LLVM_WINDOWS_SDK_VERSION-$target.tar.xz"
tar -cJf "$archive" -C "$(dirname "$sdk")" llvm-sdk
(cd "$root/dist" && sha256sum "$(basename "$archive")" > "$(basename "$archive").sha256")
