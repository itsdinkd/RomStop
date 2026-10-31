#!/usr/bin/env bash
# Regenerate overlay/ and romm.patch from a RomM checkout you edited (dev only).
# Usage: romm/export-from-tree.sh /path/to/romm-checkout
# New files go to overlay/ at the same paths; edits to upstream files go to romm.patch.
set -euo pipefail
src="$(cd "$1" && pwd)"
here="$(cd "$(dirname "$0")" && pwd)"
base="$(cat "$here/ROMM_VERSION")"

rm -rf "$here/overlay"
git -C "$src" ls-files --others --exclude-standard | grep -v '^frontend/node_modules/' | while read -r f; do
  mkdir -p "$here/overlay/$(dirname "$f")"
  cp "$src/$f" "$here/overlay/$f"
done
git -C "$src" diff "$base" -- . ':(exclude)frontend/node_modules' >"$here/romm.patch"
echo "overlay: $(find "$here/overlay" -type f | wc -l) files; romm.patch: $(grep -c '^diff --git' "$here/romm.patch") files changed"
