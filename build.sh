#!/bin/sh
# Builds Teddy with this study into teddy/dist/.
set -eu
cd "$(dirname "$0")"

if [ ! -f teddy/package.json ]; then
    git submodule update --init
fi

cd teddy
npm ci --no-audit --no-fund
TEDDY_STUDY=.. npm run build
