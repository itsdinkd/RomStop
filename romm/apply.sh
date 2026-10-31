#!/usr/bin/env bash
# Add RomStop to a RomM source checkout at the version in ROMM_VERSION.
# Usage: romm/apply.sh /path/to/romm-checkout
set -euo pipefail
dest="$(cd "$1" && pwd)"
here="$(cd "$(dirname "$0")" && pwd)"
cp -R "$here/overlay/." "$dest/"
git -C "$dest" apply --whitespace=nowarn "$here/romm.patch"
echo "RomStop added to $dest"
