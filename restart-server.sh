#!/bin/bash
# Restart the local server via systemd.
# The systemd unit is the single source of truth for OPTILAYER_DIR
# and other environment; do not start the server out-of-band.
#
# Selects the unit based on the worktree directory name:
#   design-tools-live  → design-tools-live.service (port 8082)
#   anything else      → design-tools-work.service (port 8081)
set -e

DIR_NAME=$(basename "$(realpath "$(dirname "$0")")")
case "$DIR_NAME" in
    design-tools-live) UNIT=design-tools-live; PORT=8082 ;;
    *)                 UNIT=design-tools-work; PORT=8081 ;;
esac

sudo systemctl restart "$UNIT"
echo "Restarted $UNIT — http://localhost:$PORT"
echo "Logs: journalctl -u $UNIT -f"
