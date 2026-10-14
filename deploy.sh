#!/bin/sh
# Builds Teddy with this study and copies it to the web server.
#
#   ./deploy.sh                      # to TARGET below
#   DEPLOY_TARGET=host:/path/ ./deploy.sh
#
# A build with debug on records nothing, so this refuses to deploy one unless
# ALLOW_DEBUG=1 is set.
set -eu
cd "$(dirname "$0")"

TARGET=${DEPLOY_TARGET:-teddy.example.edu:/var/www/teddy/}

debug=$(node --input-type=module -e 'import c from "./config.js"; console.log(c.debug === true)')
if [ "$debug" = true ] && [ "${ALLOW_DEBUG:-}" != 1 ]; then
    echo "config.js has debug: true, so this build would send nothing to pig." >&2
    echo "Set debug: false, or run with ALLOW_DEBUG=1 to deploy it anyway." >&2
    exit 1
fi

./build.sh

# New assets go up before the index.html that refers to them, and old ones are
# removed after, so someone loading the page mid-deploy gets one build or the
# other, not a mix.
rsync -a --exclude 'index.html*' teddy/dist/ "$TARGET"
rsync -a --delete teddy/dist/ "$TARGET"
echo "Deployed to $TARGET"
