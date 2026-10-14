#!/bin/sh
# Runs Teddy's dev server with this study, at http://localhost:9000/.
# Changes to config.js and the CSVs reload the page.
set -eu
cd "$(dirname "$0")"

if [ ! -f teddy/package.json ]; then
    git submodule update --init
fi

cd teddy
if [ ! -d node_modules ]; then
    npm ci --no-audit --no-fund
fi
TEDDY_STUDY=.. exec npx vite
