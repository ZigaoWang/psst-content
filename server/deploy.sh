#!/bin/sh
# Copies the pipeline code to the VPS and installs its dependencies there.
# Usage: sh server/deploy.sh   (from the repository root; uses the "bwh" SSH host unless PSST_SSH_HOST is set)
set -eu
host="${PSST_SSH_HOST:-bwh}"
target=/www/wwwroot/psst/app
rsync -az --delete \
  --exclude .git --exclude .venv --exclude work --exclude export --exclude candidates --exclude plans \
  --exclude tmp --exclude areas --exclude '__pycache__' ./ "$host:$target/"
ssh "$host" "cd $target && ~/.local/bin/uv sync -q --no-dev && echo 'Deployed to $target'"
