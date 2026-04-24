#!/bin/bash
# Push the current committed work branch to the live worktree and restart the live service.
set -e

LIVE_DIR="$(dirname "$0")/../design-tools-live"
SERVICE="design-tools-live"

if [ ! -d "$LIVE_DIR" ]; then
  echo "Error: live worktree not found at $LIVE_DIR"
  echo "Set it up first with:"
  echo "  git worktree add ../design-tools-live master"
  exit 1
fi

git -C "$LIVE_DIR" fetch origin master
git -C "$LIVE_DIR" reset --hard origin/master
HASH=$(git -C "$LIVE_DIR" rev-parse --short HEAD)

sudo systemctl restart "$SERVICE"
echo "Live updated to $HASH and restarted."
