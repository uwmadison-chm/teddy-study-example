#!/bin/sh
# Writes REDCap randomization lists of Teddy modules parameters for this study,
# one CSV per event. For example:
#   ./generate-lists.sh --out-dir=lists --count=100 --events=base,pw1 \
#       --modules='questions:relaxed,energy|sentences:pick(3)|videologs:01'
# pick(N) chooses N of the module's items for each row. Run with --help for more.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "$here/teddy/package.json" ]; then
    git -C "$here" submodule update --init
fi
if [ ! -d "$here/teddy/node_modules" ]; then
    (cd "$here/teddy" && npm ci --no-audit --no-fund)
fi
exec node "$here/teddy/scripts/generate-lists.ts" --study="$here" "$@"
