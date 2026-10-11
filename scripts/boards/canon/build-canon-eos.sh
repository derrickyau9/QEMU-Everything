#!/usr/bin/env bash
# Independent build; does not touch another camera's QEMU runtime.
set -euo pipefail
CANON_QEMU_PIN=4b667a1d3c08ab7a55835d15ddbd884fa754946d
CANON_LAB_ROOT="${1:-${XDG_CACHE_HOME:-$HOME/.cache}/qemu-everything/canon-eos-build}"
CANON_BUILD_JOBS="${CANON_BUILD_JOBS:-4}"
case "$CANON_BUILD_JOBS" in ''|*[!0-9]*) echo 'CANON_BUILD_JOBS must be an integer' >&2; exit 2;; esac
if (( CANON_BUILD_JOBS < 1 || CANON_BUILD_JOBS > 16 )); then echo 'CANON_BUILD_JOBS must be 1..16' >&2; exit 2; fi
for CANON_TOOL in git make gcc g++ pkg-config python3; do
    command -v "$CANON_TOOL" >/dev/null || { echo "Missing build tool: $CANON_TOOL" >&2; exit 2; }
done
pkg-config --exists glib-2.0 pixman-1 || { echo 'Need GLib and Pixman development packages' >&2; exit 2; }
mkdir -p "$CANON_LAB_ROOT"
CANON_LAB_ROOT="$(cd "$CANON_LAB_ROOT" && pwd -P)"
CANON_SOURCE="$CANON_LAB_ROOT/qemu-eos"
if [[ ! -d "$CANON_SOURCE/.git" ]]; then
    if [[ -e "$CANON_SOURCE" ]]; then echo 'Source path exists without Git metadata; refusing overwrite' >&2; exit 2; fi
    git clone --depth 1 --branch qemu-eos-v4.2.1 https://github.com/reticulatedpines/qemu-eos.git "$CANON_SOURCE"
fi
if [[ "$(git -C "$CANON_SOURCE" rev-parse HEAD)" != "$CANON_QEMU_PIN" ]]; then
    echo "Expected qemu-eos commit $CANON_QEMU_PIN; refusing another source" >&2; exit 2
fi
if [[ -n "$(git -C "$CANON_SOURCE" status --porcelain --untracked-files=no)" ]]; then
    echo 'Tracked qemu-eos source modifications found; refusing an unreviewed build' >&2; exit 2
fi
mkdir -p "$CANON_LAB_ROOT/build"
cd "$CANON_LAB_ROOT/build"
"$CANON_SOURCE/configure" --target-list=arm-softmmu --disable-docs --disable-gtk --disable-sdl --enable-vnc --disable-werror --enable-plugins --disable-capstone
make -j"$CANON_BUILD_JOBS"
./arm-softmmu/qemu-system-arm -M help | awk '$1 == "50D" {found=1; print} END {exit !found}'
printf 'Built Canon QEMU: %s/build/arm-softmmu/qemu-system-arm\n' "$CANON_LAB_ROOT"
