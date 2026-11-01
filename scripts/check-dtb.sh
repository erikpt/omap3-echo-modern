#!/usr/bin/env bash
# Check that the board DTB built by Buildroot looks right.
# Usage: check-dtb.sh <buildroot-output-dir>
# Exit 0 only if omap3-echo.dtb is found under the directory and decompiles
# to a tree with the amazon,omap3-echo compatible and a memory@ node that has
# a reg property.
set -euo pipefail

usage() {
    echo "usage: ${0##*/} <buildroot-output-dir>" >&2
}

if [ "$#" -ne 1 ]; then
    usage
    exit 2
fi
outdir=$1
if [ ! -d "$outdir" ]; then
    echo "error: not a directory: $outdir" >&2
    exit 2
fi

if [ -x "$outdir/host/bin/dtc" ]; then
    dtc=$outdir/host/bin/dtc
elif command -v dtc >/dev/null 2>&1; then
    dtc=$(command -v dtc)
else
    echo "error: no dtc found (looked for $outdir/host/bin/dtc and dtc in PATH)" >&2
    exit 2
fi

dtb=$(find "$outdir" -name omap3-echo.dtb -type f -print -quit 2>/dev/null || true)
if [ -z "$dtb" ]; then
    echo "error: omap3-echo.dtb not found under $outdir" >&2
    exit 1
fi

if ! dts=$("$dtc" -I dtb -O dts "$dtb" 2>/dev/null); then
    echo "error: dtc could not decompile $dtb" >&2
    exit 1
fi

# dtc prints a string list as "a", "b", or as "a\0b" when it cannot tell the
# property is a list; accept both.
if ! grep -Eq '(["[:space:]]|\\0)amazon,omap3-echo("|\\0)' <<<"$dts"; then
    echo "error: $dtb lacks the amazon,omap3-echo compatible" >&2
    exit 1
fi

# A memory@ node must contain a reg property: scan node by node.
if ! awk '
    /^[ \t]*memory@[^ \t{]*[ \t]*\{/ { inmem = 1; next }
    inmem && /^[ \t]*reg[ \t]*=/ { found = 1 }
    inmem && /^[ \t]*\};/ { inmem = 0 }
    END { exit found ? 0 : 1 }
' <<<"$dts"; then
    echo "error: $dtb has no reg property in a memory@ node" >&2
    exit 1
fi

echo "ok: $dtb (amazon,omap3-echo, memory reg present)"
